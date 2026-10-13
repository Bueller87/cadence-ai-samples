"""Worker-local OpenAI Agents role Activity and provider configuration."""

from agents import (
    Agent,
    OpenAIProvider,
    RunConfig,
    Runner,
)
from cadence import activity
from openai import AsyncOpenAI

from activities import AgentRequest, LIVE_AGENT_ACTIVITY, MAX_REPORT_LENGTH
from config import ModelConfig
from live import (
    LiveConfigurationError,
    LiveSchemaError,
    raise_model_error,
    validate_live,
)


ROLE_INSTRUCTIONS = {
    "planner": (
        "You are a research planner. Follow the requested output format exactly "
        "and keep the plan bounded."
    ),
    "specialist": (
        "You are a research specialist. Use only the supplied simulated source "
        "briefs. Never imply that you searched the web or verified factual claims."
    ),
    "synthesizer": (
        "You are a report synthesizer. Produce bounded Markdown and preserve every "
        "research thread plus the simulated-source disclaimer."
    ),
    "editor": (
        "You are a report editor. Return the complete updated Markdown report and "
        "preserve content that the edit does not change."
    ),
}


def openai_compatible_base_url(provider: str, endpoint: str) -> str:
    """Derive the OpenAI-compatible route from a provider-native catalog base."""
    if provider == "google":
        return endpoint.rstrip("/") + "/v1beta/openai/"
    if provider == "ollama":
        return endpoint.rstrip("/") + "/v1/"
    if provider == "openai":
        return endpoint
    raise LiveConfigurationError("unsupported model provider")


def build_model_activities(
    selection: ModelConfig, environ: dict[str, str]
) -> "OpenAIAgentActivities":
    """Build Worker-only Activities for the selected catalog provider."""
    validate_live(selection)
    provider = selection.provider
    endpoint = selection.endpoint
    key = environ.get("MODEL_AI_KEY", "").strip()
    if provider != "ollama" and not key:
        raise LiveConfigurationError("live worker requires MODEL_AI_KEY")
    environ["OPENAI_AGENTS_DISABLE_TRACING"] = "1"
    if provider == "google":
        environ["GOOGLE_API_KEY"] = key
    elif provider == "openai":
        environ["OPENAI_API_KEY"] = key
        environ["OPENAI_BASE_URL"] = endpoint
    elif provider == "ollama":
        environ["OLLAMA_API_BASE"] = endpoint
    else:
        raise LiveConfigurationError("unsupported model provider")

    base_url = openai_compatible_base_url(provider, endpoint)
    return OpenAIAgentActivities(
        selection,
        OpenAIProvider(
            openai_client=AsyncOpenAI(
                api_key=key if provider != "ollama" else "ollama",
                base_url=base_url,
                max_retries=0,
            ),
            use_responses=provider == "openai",
        ),
    )


class OpenAIAgentActivities:
    """Own the selected model and execute OpenAI Agents on the Worker."""

    def __init__(
        self, selection: ModelConfig, provider: OpenAIProvider
    ) -> None:
        self._selection = selection
        self._provider = provider

    @activity.method(name=LIVE_AGENT_ACTIVITY)
    async def run_agent(self, request: AgentRequest) -> str:
        if request.role not in ROLE_INSTRUCTIONS:
            raise LiveConfigurationError("unsupported auto-research agent role")
        prompt = request.prompt.strip()
        if not prompt:
            raise LiveConfigurationError("agent prompt must not be empty")
        try:
            result = await Runner.run(
                Agent(
                    name=f"auto-research-{request.role}",
                    model=self._provider.get_model(self._selection.model),
                    instructions=ROLE_INSTRUCTIONS[request.role],
                ),
                prompt,
                run_config=RunConfig(tracing_disabled=True),
            )
        except Exception as error:
            raise_model_error(error)
        answer = str(result.final_output or "").strip()
        if not answer:
            raise LiveSchemaError("model returned no answer")
        if len(answer) > MAX_REPORT_LENGTH:
            raise LiveSchemaError("model returned an over-limit answer")
        return answer
