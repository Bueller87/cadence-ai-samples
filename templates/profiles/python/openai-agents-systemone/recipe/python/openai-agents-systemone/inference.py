"""Worker-local OpenAI Agents Activity and provider configuration."""

from agents import (
    Agent,
    OpenAIProvider,
    RunConfig,
    Runner,
)
from cadence import activity
from openai import AsyncOpenAI

from config import ModelConfig
from live import (
    LiveConfigurationError,
    LiveSchemaError,
    raise_model_error,
    validate_live,
)
from workflow import LIVE_AGENT_ACTIVITY


INSTRUCTION = (
    "Answer the user's request briefly and identify one practical next step. "
    "Do not use tools."
)


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
        )
    )


class OpenAIAgentActivities:
    """Own the selected model and execute OpenAI Agents on the Worker."""

    def __init__(
        self, selection: ModelConfig, provider: OpenAIProvider
    ) -> None:
        self._selection = selection
        self._provider = provider

    @activity.method(name=LIVE_AGENT_ACTIVITY)
    async def run_agent(self, text: str) -> str:
        if not text.strip():
            raise LiveConfigurationError("text must not be empty")
        try:
            result = await Runner.run(
                Agent(
                    name="__RECIPE_SLUG__",
                    model=self._provider.get_model(self._selection.model),
                    instructions=INSTRUCTION,
                ),
                text,
                run_config=RunConfig(tracing_disabled=True),
            )
        except Exception as error:
            raise_model_error(error)
        answer = str(result.final_output or "").strip()
        if not answer:
            raise LiveSchemaError("model returned no answer")
        return answer
