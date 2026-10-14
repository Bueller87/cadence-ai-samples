"""Google ADK role runs and Worker-local provider Activities."""

import hashlib
from collections.abc import AsyncGenerator, MutableMapping

from cadence import activity, workflow
from cadence.contrib.google_adk import CadenceAgentRunner, GoogleADKActivities
from cadence.contrib.google_adk.cadence_model import CadenceModel
from google.adk.agents import LlmAgent
from google.adk.models.google_llm import Gemini
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.adk.sessions import InMemorySessionService
from google.genai import errors as genai_errors
from google.genai import types

from config import ModelConfig
from live import (
    LiveAuthenticationError,
    LiveConfigurationError,
    LiveSchemaError,
    raise_model_error,
    retryable,
    validate_live,
)
from workflow import AI_ACTIVITY_OPTIONS, LOGICAL_MODEL


ROLE_NAMES = {"planner", "specialist", "synthesizer", "editor"}


def runtime_model(provider: str, model: str) -> str:
    if provider == "google":
        return model
    if provider == "ollama":
        return f"ollama_chat/{model}"
    if provider == "openai":
        return f"openai/{model}"
    raise LiveConfigurationError("unsupported model provider")


def build_model_activities(
    selection: ModelConfig, environ: MutableMapping[str, str]
) -> "ADKActivities":
    validate_live(selection)
    key = environ.get("MODEL_AI_KEY", "").strip()
    if selection.provider != "ollama" and not key:
        raise LiveConfigurationError("live worker requires MODEL_AI_KEY")
    environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
    environ["LITELLM_TELEMETRY"] = "False"
    if selection.provider == "google":
        environ["GOOGLE_API_KEY"] = key
        environ["GOOGLE_GENAI_USE_VERTEXAI"] = "FALSE"
    elif selection.provider == "openai":
        environ["OPENAI_API_KEY"] = key
        environ["OPENAI_BASE_URL"] = selection.endpoint
    else:
        environ["OLLAMA_API_BASE"] = selection.endpoint
    if selection.provider != "google":
        import litellm

        litellm.num_retries = 0
        litellm.telemetry = False
        litellm.suppress_debug_info = True
    return ADKActivities(selection)


class ADKActivities(GoogleADKActivities):
    """Resolve the Workflow's logical model through the Worker's selection."""

    def __init__(self, selection: ModelConfig) -> None:
        self._selection = selection

    @activity.override(GoogleADKActivities.generate_content_async)
    async def generate_content_async(
        self, model_name: str, llm_request: LlmRequest
    ) -> list[LlmResponse]:
        del model_name
        actual_model = runtime_model(
            self._selection.provider, self._selection.model
        )
        runtime_request = llm_request.model_copy(
            update={"model": actual_model}
        )
        try:
            if self._selection.provider == "google":
                model = Gemini(
                    model=self._selection.model,
                    base_url=self._selection.endpoint,
                )
                responses = []
                async for response in model.generate_content_async(
                    llm_request=runtime_request, stream=False
                ):
                    responses.append(response)
                return responses
            return await super().generate_content_async(
                actual_model, runtime_request
            )
        except genai_errors.APIError as error:
            if error.code in {401, 403} or _contains_invalid_key(error.details):
                raise LiveAuthenticationError(
                    "model rejected MODEL_AI_KEY"
                ) from None
            if retryable(error.code):
                raise RuntimeError(
                    f"temporary model request failure: HTTP {error.code}"
                ) from None
            raise LiveSchemaError(
                f"model rejected request: HTTP {error.code}"
            ) from None
        except Exception as error:
            raise_model_error(error)


class RetryingCadenceModel(CadenceModel):
    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        if stream:
            raise RuntimeError("Streaming is not supported")
        call = self._google_adk_activities.generate_content_async.with_options(
            **AI_ACTIVITY_OPTIONS
        )
        for response in await call(model_name=self.model, llm_request=llm_request):
            yield response


async def generate_live_role(role: str, prompt: str, invocation: str) -> str:
    """Run one bounded, tool-free role through the replay-safe ADK runner."""
    if role not in ROLE_NAMES:
        raise LiveConfigurationError("unknown research role")
    if not prompt.strip() or not invocation.strip():
        raise LiveConfigurationError("role prompt and invocation must not be empty")
    agent = LlmAgent(
        name=f"auto_research_{role}",
        model=RetryingCadenceModel(model=LOGICAL_MODEL),
        instruction=(
            "Follow the user's exact output contract. Use only the supplied "
            "synthetic material. Do not use tools or claim factual web research."
        ),
    )
    sessions = InMemorySessionService()
    runner = CadenceAgentRunner(
        app_name="auto-research", agent=agent, session_service=sessions
    )
    info = workflow.WorkflowContext.get().info()
    session_id = hashlib.sha256(
        (
            f"{info.workflow_id}:{info.workflow_run_id}:{role}:{invocation}"
        ).encode("utf-8")
    ).hexdigest()[:32]
    await sessions.create_session(
        app_name=runner.app_name,
        user_id="auto-research",
        session_id=session_id,
    )
    response_text = ""
    async for event in runner.run_async(
        user_id="auto-research",
        session_id=session_id,
        new_message=types.Content(
            role="user", parts=[types.Part.from_text(text=prompt)]
        ),
    ):
        if event.is_final_response() and event.content and event.content.parts:
            response_text = "".join(
                part.text or "" for part in event.content.parts
            ).strip()
    if not response_text:
        raise LiveSchemaError(f"{role} returned no final response")
    return response_text


def _contains_invalid_key(value: object) -> bool:
    if isinstance(value, dict):
        return any(
            (key == "reason" and item == "API_KEY_INVALID")
            or _contains_invalid_key(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_contains_invalid_key(item) for item in value)
    return False
