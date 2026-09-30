"""Worker-side Jev classification, live validation, and provider error mapping."""

import json
import os
from collections.abc import Callable, MutableMapping
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from cadence import activity

from config import CatalogSelection
from workflow import CLASSIFY_ACTIVITY, ClassificationDecision


GOOGLE_ENDPOINT = "https://generativelanguage.googleapis.com"


class LiveAuthenticationError(Exception):
    pass


class LiveConfigurationError(ValueError):
    pass


class LiveSchemaError(Exception):
    pass


def retryable(status: int) -> bool:
    return status in {408, 409, 425, 429, 529} or status >= 500


def validate_live(selection: CatalogSelection) -> None:
    if selection.classifier.id != "jev-default" or selection.classifier.provider != "typesafe":
        raise LiveConfigurationError(
            "only jev-default is implemented; other classifiers are catalog candidates"
        )
    if selection.model.provider not in {"google", "ollama", "openai"}:
        raise LiveConfigurationError("unsupported model provider")
    endpoint = urlsplit(selection.model.endpoint)
    if (endpoint.scheme not in {"http", "https"} or not endpoint.hostname
            or endpoint.username or endpoint.password or endpoint.query or endpoint.fragment):
        raise LiveConfigurationError("model endpoint must be a credential-free HTTP URL")
    if (selection.model.provider == "google"
            and selection.model.endpoint.rstrip("/") != GOOGLE_ENDPOINT):
        raise LiveConfigurationError("Google models require the official endpoint")
    if (selection.model.provider == "ollama"
            and endpoint.hostname not in {"localhost", "127.0.0.1", "::1"}):
        raise LiveConfigurationError("Ollama requires a loopback endpoint")


def live_activities(selection: CatalogSelection,
                    environ: MutableMapping[str, str] = os.environ) -> tuple["JevClassifier", object]:
    validate_live(selection)
    model_key = environ.get("MODEL_AI_KEY", "").strip()
    classifier_key = environ.get("CLASSIFIER_AI_KEY", "").strip()
    if (selection.model.provider != "ollama" and not model_key) or not classifier_key:
        raise LiveConfigurationError(
            "live worker requires MODEL_AI_KEY and CLASSIFIER_AI_KEY"
        )
    from inference import build_model_activities

    return (JevClassifier(classifier_key, selection.classifier.endpoint,
                          selection.classifier.model),
            build_model_activities(selection, environ))


class JevClassifier:
    def __init__(self, api_key: str, endpoint: str, model: str,
                 opener: Callable[..., Any] = urlopen) -> None:
        self._api_key = api_key
        self._endpoint = endpoint
        self._model = model
        self._open = opener

    @activity.method(name=CLASSIFY_ACTIVITY)
    def classify(self, text: str) -> ClassificationDecision:
        question = {
            "type": "choice",
            "instructions": "Does this input warrant generative analysis?",
            "criteria": {
                "yes": "Flexible analysis would add useful information.",
                "no": "Deterministic handling is sufficient.",
            },
        }
        request = Request(
            self._endpoint,
            data=json.dumps({"state": text, "model": self._model,
                             "questions": {"analyze": question}}).encode(),
            headers={"Authorization": f"Bearer {self._api_key}",
                     "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with self._open(request, timeout=20) as response:
                answer = json.loads(response.read(1_048_577))["answers"]["analyze"]
        except HTTPError as error:
            if retryable(error.code):
                raise
            if error.code in {401, 403}:
                raise LiveAuthenticationError("Jev authentication failed") from error
            raise LiveSchemaError(f"Jev rejected the request: HTTP {error.code}") from error
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise LiveSchemaError("Jev returned an invalid response") from error
        if not isinstance(answer, dict):
            raise LiveSchemaError("Jev returned an invalid response")
        choice, confidence = answer.get("choice"), answer.get("confidence")
        if answer.get("type") != "choice" or choice not in {"yes", "no"}:
            raise LiveSchemaError("Jev returned an invalid choice")
        if not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
            raise LiveSchemaError("Jev returned an invalid confidence")
        return ClassificationDecision(choice == "yes",
                                      f"Jev selected {choice} ({confidence:.2f})")


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
