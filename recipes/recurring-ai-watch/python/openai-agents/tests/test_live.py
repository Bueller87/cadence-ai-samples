from __future__ import annotations

import json
import unittest
from urllib.error import HTTPError

from config import CatalogSelection, ClassifierConfig, ModelConfig
from live import (
    LiveAuthenticationError,
    LiveConfigurationError,
    LiveSchemaError,
    SystemOneClassifier,
    live_activities,
    retryable,
    validate_live,
)
from workflow import ClassificationDecision, ReleaseNote


def selection(
    *,
    model_endpoint: str = "https://generativelanguage.googleapis.com",
    classifier_id: str = "jev-default",
    classifier_provider: str = "typesafe",
    classifier_model: str = "jev-latest",
    classifier_endpoint: str = "https://api.typesafe.ai/v1/systemone",
):
    return CatalogSelection(
        model=ModelConfig(
            id="gemini-flash-lite",
            provider="google",
            model="gemini-3.5-flash-lite",
            endpoint=model_endpoint,
        ),
        classifier=ClassifierConfig(
            id=classifier_id,
            provider=classifier_provider,
            model=classifier_model,
            endpoint=classifier_endpoint,
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
        self.assertTrue(retryable(429))
        self.assertTrue(retryable(503))
        self.assertFalse(retryable(401))
        self.assertFalse(retryable(422))

    def test_requires_official_google_endpoint(self) -> None:
        validate_live(selection())

        with self.assertRaisesRegex(LiveConfigurationError, "official endpoint"):
            validate_live(selection(model_endpoint="https://gateway.example/v1"))

    def test_jev_worker_requires_both_application_credentials(self) -> None:
        with self.assertRaisesRegex(LiveConfigurationError, "MODEL_AI_KEY"):
            live_activities(selection(), {})
        with self.assertRaisesRegex(LiveConfigurationError, "CLASSIFIER_AI_KEY"):
            live_activities(selection(), {"MODEL_AI_KEY": "model-secret"})

    def test_worker_maps_model_key_without_changing_catalog_data(self) -> None:
        environment = {
            "MODEL_AI_KEY": "model-secret",
            "CLASSIFIER_AI_KEY": "classifier-secret",
        }

        classifier, _ = live_activities(selection(), environment)

        self.assertEqual(environment["GOOGLE_API_KEY"], "model-secret")
        self.assertNotIn("secret", repr(classifier).lower())

    def test_laya_needs_no_classifier_key_and_requires_loopback(self) -> None:
        laya = selection(
            classifier_id="laya-local",
            classifier_provider="laya",
            classifier_model="convaiinnovations/laya",
            classifier_endpoint="http://localhost:8000/v1/systemone",
        )
        classifier, _ = live_activities(laya, {"MODEL_AI_KEY": "model-secret"})
        self.assertIsInstance(classifier, SystemOneClassifier)

        with self.assertRaisesRegex(LiveConfigurationError, "loopback"):
            validate_live(selection(
                classifier_id="laya-local",
                classifier_provider="laya",
                classifier_model="convaiinnovations/laya",
                classifier_endpoint="https://laya.example/v1/systemone",
            ))


class SystemOneClassifierTests(unittest.TestCase):
    def test_uses_proven_one_question_contract(self) -> None:
        requests: list[tuple[object, int]] = []

        def open_request(request: object, timeout: int):
            requests.append((request, timeout))
            return FakeResponse(
                {
                    "model": "jev-1.13.0",
                    "answers": {
                        "relevant": {
                            "type": "choice",
                            "choice": "yes",
                            "confidence": 0.97,
                            "probabilities": {"yes": 0.97, "no": 0.03},
                        }
                    },
                }
            )

        classifier = SystemOneClassifier(
            "classifier-secret",
            "https://api.typesafe.ai/v1/systemone",
            "jev-latest",
            opener=open_request,
        )
        result = classifier.classify(
            ReleaseNote("2.5.0", "Retry defaults changed for background jobs.")
        )

        request, timeout = requests[0]
        payload = json.loads(request.data)
        self.assertEqual(request.full_url, "https://api.typesafe.ai/v1/systemone")
        self.assertEqual(timeout, 20)
        self.assertEqual(payload["model"], "jev-latest")
        self.assertEqual(list(payload["questions"]), ["relevant"])
        self.assertEqual(request.get_header("Authorization"), "Bearer classifier-secret")
        self.assertEqual(
            result, ClassificationDecision(True, "System One selected yes (0.97)")
        )

    def test_laya_uses_catalog_endpoint_and_model_without_authorization(self) -> None:
        requests = []

        def open_request(request: object, timeout: int):
            requests.append(request)
            return FakeResponse({
                "answers": {"relevant": {
                    "type": "choice", "choice": "no", "confidence": 0.88,
                }}
            })

        classifier = SystemOneClassifier(
            None,
            "http://localhost:8000/v1/systemone",
            "convaiinnovations/laya",
            opener=open_request,
        )
        classifier.classify(ReleaseNote("2.4.0", "Documentation update."))

        request = requests[0]
        self.assertEqual(request.full_url, "http://localhost:8000/v1/systemone")
        self.assertEqual(json.loads(request.data)["model"], "convaiinnovations/laya")
        self.assertIsNone(request.get_header("Authorization"))

    def test_authentication_failure_is_non_retryable(self) -> None:
        def reject(*args: object, **kwargs: object):
            raise HTTPError("https://jev.invalid", 401, "Unauthorized", {}, None)

        classifier = SystemOneClassifier(
            "classifier-secret",
            "https://api.typesafe.ai/v1/systemone",
            "jev-latest",
            opener=reject,
        )

        with self.assertRaises(LiveAuthenticationError):
            classifier.classify(ReleaseNote("2.5.0", "Retry change."))

    def test_malformed_relevant_answer_is_a_schema_error(self) -> None:
        classifier = SystemOneClassifier(
            "classifier-secret",
            "https://api.typesafe.ai/v1/systemone",
            "jev-latest",
            opener=lambda *args, **kwargs: FakeResponse({"answers": {"relevant": []}}),
        )

        with self.assertRaises(LiveSchemaError):
            classifier.classify(ReleaseNote("2.5.0", "Retry change."))


if __name__ == "__main__":
    unittest.main()
