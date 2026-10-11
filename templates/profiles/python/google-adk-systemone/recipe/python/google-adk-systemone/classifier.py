"""SystemOne validation and HTTP Activity implementation."""

import asyncio
import json
import math
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from cadence import activity

from config import ClassifierConfig
from workflow import CLASSIFY_ACTIVITY, Classification


class LiveAuthenticationError(Exception):
    pass


class LiveConfigurationError(ValueError):
    pass


class LiveSchemaError(Exception):
    pass


SUPPORTED_CLASSIFIERS = {
    ("jev-default", "typesafe"),
    ("laya-local", "laya"),
}


def validate_classifier(selection: ClassifierConfig) -> None:
    if (selection.id, selection.provider) not in SUPPORTED_CLASSIFIERS:
        raise LiveConfigurationError(
            "supported classifiers are jev-default/typesafe and laya-local/laya"
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
            "classifier endpoint must be an HTTP URL without credentials, "
            "query, or fragment"
        )
    if selection.provider == "typesafe" and endpoint.scheme != "https":
        raise LiveConfigurationError(
            "remote credential-bearing classifiers require an HTTPS endpoint"
        )
    if selection.provider == "laya" and endpoint.hostname not in {
        "localhost", "127.0.0.1", "::1"
    }:
        raise LiveConfigurationError("Laya requires a loopback endpoint")


class SystemOneActivities:
    def __init__(self, selection: ClassifierConfig, api_key: str) -> None:
        validate_classifier(selection)
        if selection.provider == "typesafe" and not api_key.strip():
            raise LiveConfigurationError(
                "live Worker requires CLASSIFIER_AI_KEY for typesafe"
            )
        self._selection = selection
        self._api_key = api_key.strip()

    @activity.method(name=CLASSIFY_ACTIVITY)
    async def classify(self, text: str) -> Classification:
        return await asyncio.to_thread(self._classify, text)

    def _classify(self, text: str) -> Classification:
        if not text.strip():
            raise LiveConfigurationError("text must not be empty")
        payload = json.dumps({
            "state": text,
            "model": self._selection.model,
            "questions": {
                "route": {
                    "type": "choice",
                    "instructions": (
                        "Should this request receive routine handling or "
                        "priority review?"
                    ),
                    "criteria": {
                        "routine": "Normal request without urgent impact.",
                        "priority": "Urgent, blocked, outage, or security impact.",
                    },
                }
            },
        }).encode()
        request = urllib.request.Request(
            self._selection.endpoint,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        if self._api_key:
            request.add_header("Authorization", f"Bearer {self._api_key}")
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                document = json.load(response)
        except urllib.error.HTTPError as error:
            if error.code in {401, 403}:
                raise LiveAuthenticationError(
                    "classifier rejected CLASSIFIER_AI_KEY"
                ) from None
            if error.code in {408, 409, 425, 429, 529} or error.code >= 500:
                raise RuntimeError("temporary classifier request failure") from None
            raise LiveSchemaError(
                f"classifier rejected request: HTTP {error.code}"
            ) from None
        except (urllib.error.URLError, TimeoutError):
            raise RuntimeError("temporary classifier request failure") from None
        return parse_classification(document)


def parse_classification(document: object) -> Classification:
    try:
        model = document["model"]
        answer = document["answers"]["route"]
        label = answer["choice"]
        confidence = float(answer["confidence"])
        probabilities = {
            key: float(value) for key, value in answer["probabilities"].items()
        }
    except (KeyError, TypeError, ValueError, AttributeError):
        raise LiveSchemaError("classifier returned an invalid response") from None
    if (
        not isinstance(model, str)
        or label not in {"routine", "priority"}
        or set(probabilities) != {"routine", "priority"}
        or not math.isfinite(confidence)
        or not 0 <= confidence <= 1
        or any(not math.isfinite(value) or not 0 <= value <= 1
               for value in probabilities.values())
        or not math.isclose(sum(probabilities.values()), 1.0, abs_tol=0.02)
    ):
        raise LiveSchemaError("classifier returned an invalid choice contract")
    return Classification(label, confidence, probabilities, model)
