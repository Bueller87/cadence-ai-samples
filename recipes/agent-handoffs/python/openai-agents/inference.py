"""Worker-local model Activity and provider configuration."""

from typing import Any

from agents import Model, OpenAIProvider
from cadence.contrib.openai import OpenAIActivities
from openai import AsyncOpenAI

from config import ModelConfig
from live import LiveConfigurationError, raise_model_error, validate_live


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
) -> OpenAIActivities:
    """Build Worker-only model Activities for the selected catalog entry."""
    validate_live(selection)
    provider = selection.provider
    endpoint = selection.endpoint
    key = environ.get("MODEL_AI_KEY", "").strip()
    if provider != "ollama" and not key:
        raise LiveConfigurationError("live worker requires MODEL_AI_KEY")
    environ["OPENAI_AGENTS_DISABLE_TRACING"] = "1"
    # CadenceModel constructs a proxy OpenAIActivities instance in Workflow code
    # before redirecting invocation to the registered Worker Activity. The SDK
    # constructor requires this value but never uses that proxy client.
    environ["OPENAI_API_KEY"] = key if provider != "ollama" else "ollama"
    if provider == "google":
        environ["GOOGLE_API_KEY"] = key
    elif provider == "openai":
        environ["OPENAI_API_KEY"] = key
        environ["OPENAI_BASE_URL"] = endpoint
    elif provider == "ollama":
        environ["OLLAMA_API_BASE"] = endpoint
    else:
        raise LiveConfigurationError("unsupported model provider")

    model_provider = CatalogModelProvider(
        selection.model,
        openai_client=AsyncOpenAI(
            api_key=key if provider != "ollama" else "ollama",
            base_url=openai_compatible_base_url(provider, endpoint),
            max_retries=0,
        ),
        use_responses=provider == "openai",
    )
    return ConfiguredOpenAIActivities(model_provider)


class SanitizedModel(Model):
    """Translate provider failures before they enter Workflow history."""

    def __init__(self, delegate: Model) -> None:
        self._delegate = delegate

    async def get_response(self, *args: Any, **kwargs: Any) -> Any:
        try:
            return await self._delegate.get_response(*args, **kwargs)
        except Exception as error:
            raise_model_error(error)

    def stream_response(self, *args: Any, **kwargs: Any) -> Any:
        return self._delegate.stream_response(*args, **kwargs)


class CatalogModelProvider(OpenAIProvider):
    """Resolve the Workflow's logical model to the Worker catalog selection."""

    def __init__(self, selected_model: str, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._selected_model = selected_model

    def get_model(self, _model_name: str | None = None) -> Model:
        return SanitizedModel(super().get_model(self._selected_model))


class ConfiguredOpenAIActivities(OpenAIActivities):
    """OpenAI model Activities bound to one Worker-local provider."""

    def __init__(self, provider: CatalogModelProvider) -> None:
        self._openai_provider = provider
