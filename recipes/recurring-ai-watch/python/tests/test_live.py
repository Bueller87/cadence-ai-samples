from __future__ import annotations

import json
import unittest
from urllib.error import HTTPError

from config import AgentConfig, CatalogSelection, ClassifierConfig, ModelConfig
from live import (
    LiveAuthenticationError,
    LiveConfigurationError,
    LiveJevClassifier,
    configure_live_worker,
    is_retryable_provider_status,
    validate_live_selection,
)
from workflow import MockClassification, ReleaseNote


def selection(*, model_endpoint: str = "https://generativelanguage.googleapis.com"):
    return CatalogSelection(
        agent=AgentConfig(id="google-adk", framework="google-adk"),
        model=ModelConfig(
            id="gemini-flash-lite",
            provider="google",
            model="gemini-3.5-flash-lite",
            endpoint=model_endpoint,
        ),
        classifier=ClassifierConfig(
            id="jev-default",
            provider="typesafe",
            model="jev-latest",
            endpoint="https://api.typesafe.ai/v1/systemone",
        ),
    )


class FakeResponse:
    def __init__(self, document: dict[str, object]) -> None:
        self._body = json.dumps(document).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self, limit: int) -> bytes:
        return self._body[:limit]


class LiveConfigurationTests(unittest.TestCase):
    def test_provider_status_classification(self) -> None:
        self.assertTrue(is_retryable_provider_status(429))
        self.assertTrue(is_retryable_provider_status(503))
        self.assertFalse(is_retryable_provider_status(401))
        self.assertFalse(is_retryable_provider_status(422))

    def test_accepts_only_verified_tuple_and_official_google_endpoint(self) -> None:
        validate_live_selection(selection())

        with self.assertRaisesRegex(LiveConfigurationError, "official Google"):
            validate_live_selection(selection(model_endpoint="https://gateway.example/v1"))

    def test_worker_requires_both_application_credentials(self) -> None:
        with self.assertRaisesRegex(LiveConfigurationError, "MODEL_AI_KEY"):
            configure_live_worker(selection(), {})
        with self.assertRaisesRegex(LiveConfigurationError, "CLASSIFIER_AI_KEY"):
            configure_live_worker(selection(), {"MODEL_AI_KEY": "model-secret"})

    def test_worker_maps_model_key_without_changing_catalog_data(self) -> None:
        environment = {
            "MODEL_AI_KEY": "model-secret",
            "CLASSIFIER_AI_KEY": "classifier-secret",
        }

        classifier, model_activities = configure_live_worker(
            selection(), environment
        )

        self.assertEqual(environment["GOOGLE_API_KEY"], "model-secret")
        self.assertEqual(
            model_activities.generate_content_async.name,
            "GoogleADKActivities.generate_content_async",
        )
        self.assertNotIn("secret", repr(classifier).lower())


class LiveJevClassifierTests(unittest.TestCase):
    def test_uses_proven_one_question_contract(self) -> None:
        requests: list[tuple[object, int]] = []

        def open_request(request: object, timeout: int):
            requests.append((request, timeout))
            return FakeResponse(
                {
                    "model": "jev-1.13.0",
                    "answers": {
                        "warrants_report": {
                            "type": "choice",
                            "choice": "yes",
                            "confidence": 0.97,
                            "probabilities": {"yes": 0.97, "no": 0.03},
                        }
                    },
                }
            )

        classifier = LiveJevClassifier(
            api_key="classifier-secret",
            endpoint="https://api.typesafe.ai/v1/systemone",
            model="jev-latest",
            opener=open_request,
        )
        result = classifier.classify_update(
            ReleaseNote("2.5.0", "Retry defaults changed for background jobs.")
        )

        request, timeout = requests[0]
        payload = json.loads(request.data)
        self.assertEqual(request.full_url, "https://api.typesafe.ai/v1/systemone")
        self.assertEqual(timeout, 20)
        self.assertEqual(payload["model"], "jev-latest")
        self.assertEqual(list(payload["questions"]), ["warrants_report"])
        self.assertEqual(result, MockClassification(True, "Jev selected yes with confidence 0.97"))

    def test_authentication_failure_is_non_retryable(self) -> None:
        def reject(*args: object, **kwargs: object):
            raise HTTPError("https://jev.invalid", 401, "Unauthorized", {}, None)

        classifier = LiveJevClassifier(
            api_key="classifier-secret",
            endpoint="https://api.typesafe.ai/v1/systemone",
            model="jev-latest",
            opener=reject,
        )

        with self.assertRaises(LiveAuthenticationError):
            classifier.classify_update(ReleaseNote("2.5.0", "Retry change."))


if __name__ == "__main__":
    unittest.main()
