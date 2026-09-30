"""System One classification, live validation, and provider error mapping."""

from __future__ import annotations

import json
import os
from collections.abc import Callable, MutableMapping
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from urllib.parse import urlsplit

from cadence import activity

from config import CatalogSelection
from workflow import CLASSIFY_ACTIVITY, ClassificationDecision, ReleaseNote


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
    classifier = (selection.classifier.id, selection.classifier.provider)
    if classifier not in {("jev-default", "typesafe"), ("laya-local", "laya")}:
        raise LiveConfigurationError(
            "supported classifiers are jev-default and laya-local; "
            "other classifiers are catalog candidates"
        )
    if selection.classifier.id == "laya-local":
        endpoint = urlsplit(selection.classifier.endpoint)
        if (
            endpoint.scheme not in {"http", "https"}
            or endpoint.hostname not in {"localhost", "127.0.0.1", "::1"}
            or endpoint.username
            or endpoint.password
            or endpoint.query
            or endpoint.fragment
        ):
            raise LiveConfigurationError("laya-local requires a loopback HTTP endpoint")
    if selection.model.provider not in {"google", "ollama", "openai"}:
        raise LiveConfigurationError("unsupported model provider")
    endpoint = urlsplit(selection.model.endpoint)
    if (endpoint.scheme not in {"http", "https"} or not endpoint.hostname
            or endpoint.username or endpoint.password or endpoint.query or endpoint.fragment):
        raise LiveConfigurationError("model endpoint must be an HTTP URL without credentials, query, or fragment")
    if selection.model.provider == "google" and selection.model.endpoint.rstrip("/") != GOOGLE_ENDPOINT:
        raise LiveConfigurationError("live mode requires Google's official endpoint")
    if selection.model.provider == "ollama" and endpoint.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise LiveConfigurationError("Ollama requires a loopback endpoint")


def live_activities(
    selection: CatalogSelection,
    environ: MutableMapping[str, str] = os.environ,
) -> tuple["SystemOneClassifier", object]:
    validate_live(selection)
    model_key = environ.get("MODEL_AI_KEY", "").strip()
    classifier_key = environ.get("CLASSIFIER_AI_KEY", "").strip()
    if selection.model.provider != "ollama" and not model_key:
        raise LiveConfigurationError("live worker requires MODEL_AI_KEY")
    if selection.classifier.id == "jev-default" and not classifier_key:
        raise LiveConfigurationError("jev-default requires CLASSIFIER_AI_KEY")
    from inference import build_model_activities

    return (
        SystemOneClassifier(
            classifier_key or None,
            selection.classifier.endpoint,
            selection.classifier.model,
        ),
        build_model_activities(selection, environ),
    )


class SystemOneClassifier:
    def __init__(
        self,
        api_key: str | None,
        endpoint: str,
        model: str,
        opener: Callable[..., Any] = urlopen,
    ) -> None:
        self._api_key = api_key
        self._endpoint = endpoint
        self._model = model
        self._open = opener

    @activity.method(name=CLASSIFY_ACTIVITY)
    def classify(self, update: ReleaseNote) -> ClassificationDecision:
        question = {
            "type": "choice",
            "instructions": (
                "Does this release potentially affect the fictional application's "
                "background-job behavior?"
            ),
            "criteria": {
                "yes": "It may affect retries, queues, workers, scheduling, or timeouts.",
                "no": "It does not affect background-job behavior.",
            },
        }
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        request = Request(
            self._endpoint,
            data=json.dumps(
                {
                    "state": f"Version: {update.version}\nRelease notes: {update.notes}",
                    "model": self._model,
                    "questions": {"relevant": question},
                }
            ).encode(),
            headers=headers,
            method="POST",
        )
        try:
            with self._open(request, timeout=20) as response:
                answer = json.loads(response.read(1_048_577))["answers"]["relevant"]
        except HTTPError as error:
            if retryable(error.code):
                raise
            if error.code in {401, 403}:
                raise LiveAuthenticationError("classifier authentication failed") from error
            raise LiveSchemaError(
                f"System One rejected the request: HTTP {error.code}"
            ) from error
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise LiveSchemaError("System One returned an invalid response") from error

        if not isinstance(answer, dict):
            raise LiveSchemaError("System One returned an invalid response")

        choice = answer.get("choice")
        confidence = answer.get("confidence")
        if answer.get("type") != "choice" or choice not in ("yes", "no"):
            raise LiveSchemaError("System One returned an invalid choice")
        if not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
            raise LiveSchemaError("System One returned an invalid confidence")
        return ClassificationDecision(
            choice == "yes", f"System One selected {choice} ({confidence:.2f})"
        )


def raise_model_error(error: Exception) -> None:
    """Keep provider bodies (which may echo headers) out of Activity failures."""
    status = getattr(error, "status_code", None)
    if status in {401, 403}:
        raise LiveAuthenticationError("model rejected MODEL_AI_KEY") from None
    if isinstance(status, int) and not retryable(status):
        raise LiveSchemaError(f"model rejected request: HTTP {status}") from None
    if isinstance(error, (TypeError, ValueError)):
        raise LiveSchemaError("model configuration or schema is invalid") from None
    raise RuntimeError("temporary model request failure") from None
