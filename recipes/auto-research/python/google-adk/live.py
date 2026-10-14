"""Live model validation, credential checks, and sanitized provider errors."""

from collections.abc import MutableMapping
from urllib.parse import urlsplit

from config import ModelConfig


SUPPORTED_PROVIDERS = {"google", "ollama", "openai"}


class LiveAuthenticationError(Exception):
    """The provider rejected the configured credential."""


class LiveConfigurationError(ValueError):
    """Live mode is not safely or completely configured."""


class LiveSchemaError(Exception):
    """The provider returned an unusable response."""


def retryable(status: int) -> bool:
    return status in {408, 409, 425, 429, 529} or status >= 500


def validate_live(selection: ModelConfig) -> None:
    if selection.provider not in SUPPORTED_PROVIDERS:
        raise LiveConfigurationError(
            "model provider must be google, ollama, or openai"
        )
    endpoint = urlsplit(selection.endpoint)
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
    if selection.provider in {"google", "openai"} and endpoint.scheme != "https":
        raise LiveConfigurationError(
            "remote credential-bearing providers require an HTTPS endpoint"
        )
    if (
        selection.provider == "ollama"
        and endpoint.hostname not in {"localhost", "127.0.0.1", "::1"}
    ):
        raise LiveConfigurationError("Ollama requires a loopback endpoint")


def live_model_activities(
    selection: ModelConfig, environ: MutableMapping[str, str]
) -> object:
    validate_live(selection)
    key = environ.get("MODEL_AI_KEY", "").strip()
    if selection.provider != "ollama" and not key:
        raise LiveConfigurationError("live worker requires MODEL_AI_KEY")
    from inference import build_model_activities

    return build_model_activities(selection, environ)


def raise_model_error(error: Exception) -> None:
    """Keep provider bodies, which may echo headers, out of Activity failures."""
    status = getattr(error, "status_code", None)
    if status in {401, 403}:
        raise LiveAuthenticationError("model rejected MODEL_AI_KEY") from None
    if isinstance(status, int) and not retryable(status):
        raise LiveSchemaError(f"model rejected request: HTTP {status}") from None
    if isinstance(error, (TypeError, ValueError)):
        raise LiveSchemaError("model configuration or schema is invalid") from None
    raise RuntimeError("temporary model request failure") from None
