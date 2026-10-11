"""Worker-local Google ADK agent Activity and provider configuration."""

from collections.abc import MutableMapping
from uuid import uuid4
from urllib.parse import urlsplit

from cadence import activity
from google.adk.agents import LlmAgent
from google.adk.models.google_llm import Gemini
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import errors as genai_errors
from google.genai import types

from config import ModelConfig
from workflow import LIVE_AGENT_ACTIVITY


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
) -> "GoogleADKAgentActivities":
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
    return GoogleADKAgentActivities(model)


class GoogleADKAgentActivities:
    """Own the selected model and execute the ADK integration on the Worker."""

    def __init__(self, selection: ModelConfig) -> None:
        self._selection = selection

    def _model(self) -> object:
        if self._selection.provider == "google":
            return Gemini(
                model=self._selection.model,
                base_url=self._selection.endpoint,
            )
        return runtime_model(self._selection.provider, self._selection.model)

    @activity.method(name=LIVE_AGENT_ACTIVITY)
    async def run_agent(self, prompt: str) -> str:
        if not prompt.strip():
            raise LiveConfigurationError("text must not be empty")
        sessions = InMemorySessionService()
        agent = LlmAgent(
            name="recipe_agent",
            model=self._model(),
            instruction=INSTRUCTION,
        )
        runner = Runner(
            app_name="recipe-starter",
            agent=agent,
            session_service=sessions,
        )
        session_id = str(uuid4())
        await sessions.create_session(
            app_name=runner.app_name,
            user_id="recipe-user",
            session_id=session_id,
        )
        response_text = ""
        try:
            async for event in runner.run_async(
                user_id="recipe-user",
                session_id=session_id,
                new_message=types.Content(
                    role="user",
                    parts=[types.Part.from_text(text=prompt)],
                ),
            ):
                if event.is_final_response() and event.content and event.content.parts:
                    response_text = "".join(
                        part.text or "" for part in event.content.parts
                    ).strip()
        except Exception as error:
            _raise_model_error(error)
        if not response_text:
            raise LiveSchemaError("model returned no response")
        return response_text


def _retryable(status: int) -> bool:
    return status in {408, 409, 425, 429, 529} or status >= 500


def _raise_model_error(error: Exception) -> None:
    if isinstance(error, genai_errors.APIError):
        status = error.code
        if status in {401, 403} or _contains_invalid_key(error.details):
            raise LiveAuthenticationError(
                "model rejected MODEL_AI_KEY"
            ) from None
        if _retryable(status):
            raise RuntimeError(
                f"temporary model request failure: HTTP {status}"
            ) from None
        raise LiveSchemaError(
            f"model rejected request: HTTP {status}"
        ) from None

    status = getattr(error, "status_code", None)
    if status in {401, 403}:
        raise LiveAuthenticationError("model rejected MODEL_AI_KEY") from None
    if isinstance(status, int) and not _retryable(status):
        raise LiveSchemaError(f"model rejected request: HTTP {status}") from None
    if isinstance(error, (TypeError, ValueError)):
        raise LiveSchemaError("model configuration or schema is invalid") from None
    raise RuntimeError("temporary model request failure") from None


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
