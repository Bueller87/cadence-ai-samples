"""Google ADK model dispatch; provider clients are created only by Workers."""

from collections.abc import AsyncGenerator, MutableMapping
from urllib.parse import urlsplit

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
from workflow import AGENT_ACTIVITY_OPTIONS


SUPPORTED_MODELS = {
    "gemini-flash-lite": "google",
    "llama3.2-local": "ollama",
    "openai-nano": "openai",
}
INSTRUCTION = (
    "Answer the user's request briefly. Do not use tools or claim to have taken "
    "actions outside this conversation."
)


class LiveAuthenticationError(Exception):
    """The live provider rejected its credential."""


class LiveConfigurationError(ValueError):
    """The selected live model or endpoint is unsupported."""


class LiveSchemaError(Exception):
    """The live provider rejected or returned an invalid message."""


def validate_live(model: ModelConfig) -> None:
    if SUPPORTED_MODELS.get(model.id) != model.provider:
        raise LiveConfigurationError(
            "supported model selections are gemini-flash-lite/google, "
            "llama3.2-local/ollama, and openai-nano/openai"
        )
    endpoint = urlsplit(model.endpoint)
    if (
        endpoint.scheme not in {"http", "https"}
        or not endpoint.hostname
        or endpoint.username
        or endpoint.password
        or endpoint.query
        or endpoint.fragment
    ):
        raise LiveConfigurationError(
            "model endpoint must be an HTTP URL without credentials, query, or fragment"
        )
    if model.provider in {"google", "openai"} and endpoint.scheme != "https":
        raise LiveConfigurationError(
            "remote credential-bearing providers require an HTTPS endpoint"
        )
    if model.provider == "ollama" and endpoint.hostname not in {
        "localhost",
        "127.0.0.1",
        "::1",
    }:
        raise LiveConfigurationError("Ollama requires a loopback catalog endpoint")


def runtime_model(provider: str, model: str) -> str:
    if provider == "google":
        return model
    if provider == "ollama":
        return f"ollama_chat/{model}"
    if provider == "openai":
        return f"openai/{model}"
    raise LiveConfigurationError("unsupported model provider")


def build_model_activities(
    model: ModelConfig, environ: MutableMapping[str, str]
) -> "ADKActivities":
    validate_live(model)
    key = environ.get("MODEL_AI_KEY", "").strip()
    if model.provider != "ollama" and not key:
        raise LiveConfigurationError("live worker requires MODEL_AI_KEY")

    endpoint = model.endpoint
    environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
    environ["LITELLM_TELEMETRY"] = "False"
    if model.provider == "google":
        environ["GOOGLE_API_KEY"] = key
        environ["GOOGLE_GENAI_USE_VERTEXAI"] = "FALSE"
    elif model.provider == "openai":
        environ["OPENAI_API_KEY"] = key
        environ["OPENAI_BASE_URL"] = endpoint
    else:
        environ["OLLAMA_API_BASE"] = endpoint

    if model.provider != "google":
        import litellm

        litellm.num_retries = 0
        litellm.telemetry = False
        litellm.suppress_debug_info = True
    return ADKActivities(model.provider, endpoint)


class ADKActivities(GoogleADKActivities):
    def __init__(self, provider: str, endpoint: str) -> None:
        self._provider = provider
        self._endpoint = endpoint

    @activity.override(GoogleADKActivities.generate_content_async)
    async def generate_content_async(
        self, model_name: str, llm_request: LlmRequest
    ) -> list[LlmResponse]:
        try:
            if self._provider == "google":
                model = Gemini(model=model_name, base_url=self._endpoint)
                responses: list[LlmResponse] = []
                async for response in model.generate_content_async(
                    llm_request=llm_request,
                    stream=False,
                ):
                    responses.append(response)
                return responses
            return await super().generate_content_async(model_name, llm_request)
        except genai_errors.APIError as error:
            body = error.details if isinstance(error.details, dict) else {}
            body = body.get("error", body)
            details = body.get("details", []) if isinstance(body, dict) else []
            invalid_key = any(
                isinstance(detail, dict) and detail.get("reason") == "API_KEY_INVALID"
                for detail in (details if isinstance(details, list) else [])
            )
            if error.code in {401, 403} or invalid_key:
                raise LiveAuthenticationError("Google rejected MODEL_AI_KEY") from None
            if _retryable(error.code):
                raise RuntimeError(f"Google temporary failure: HTTP {error.code}") from None
            raise LiveSchemaError(
                f"Google rejected the request: HTTP {error.code}"
            ) from None
        except (TypeError, ValueError):
            raise LiveSchemaError("model configuration or schema is invalid") from None
        except Exception as error:
            _raise_model_error(error)


class RetryingCadenceModel(CadenceModel):
    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        if stream:
            raise LiveConfigurationError("streaming is not supported")
        call = self._google_adk_activities.generate_content_async.with_options(
            **AGENT_ACTIVITY_OPTIONS
        )
        for response in await call(model_name=self.model, llm_request=llm_request):
            yield response


async def generate_live_response(
    prompt: str, model_name: str | None, provider: str | None
) -> str:
    if not model_name or not provider:
        raise LiveConfigurationError("live mode requires a model selection")
    agent = LlmAgent(
        name="recipe_agent",
        model=RetryingCadenceModel(model=runtime_model(provider, model_name)),
        instruction=INSTRUCTION,
    )
    sessions = InMemorySessionService()
    runner = CadenceAgentRunner(
        app_name="recipe-starter",
        agent=agent,
        session_service=sessions,
    )
    workflow_id = workflow.WorkflowContext.get().info().workflow_id
    await sessions.create_session(
        app_name=runner.app_name,
        user_id="recipe-user",
        session_id=workflow_id,
    )
    response_text = ""
    async for event in runner.run_async(
        user_id="recipe-user",
        session_id=workflow_id,
        new_message=types.Content(
            role="user",
            parts=[types.Part.from_text(text=prompt)],
        ),
    ):
        if event.is_final_response() and event.content and event.content.parts:
            response_text = "".join(
                part.text or "" for part in event.content.parts
            ).strip()
    if not response_text:
        raise LiveSchemaError("model returned no response")
    return response_text


def _retryable(status: int) -> bool:
    return status in {408, 409, 425, 429, 529} or status >= 500


def _raise_model_error(error: Exception) -> None:
    status = getattr(error, "status_code", None)
    if status in {401, 403}:
        raise LiveAuthenticationError("model rejected MODEL_AI_KEY") from None
    if isinstance(status, int) and not _retryable(status):
        raise LiveSchemaError(f"model rejected request: HTTP {status}") from None
    if isinstance(error, (TypeError, ValueError)):
        raise LiveSchemaError("model configuration or schema is invalid") from None
    raise RuntimeError("temporary model request failure") from None
