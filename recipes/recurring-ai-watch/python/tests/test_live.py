from __future__ import annotations

import json
import unittest
import traceback
from unittest.mock import AsyncMock, patch
from cadence.contrib.google_adk import GoogleADKActivities
from google.adk.models.llm_request import LlmRequest
from google.genai.errors import ClientError
from urllib.error import HTTPError

from config import AgentConfig, CatalogSelection, ClassifierConfig, ModelConfig
from live import (
    JevClassifier,
    GeminiActivities,
    LiveAuthenticationError,
    LiveConfigurationError,
    LiveSchemaError,
    live_activities,
    retryable,
    validate_live,
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
        self.assertTrue(retryable(429))
        self.assertTrue(retryable(503))
        self.assertFalse(retryable(401))
        self.assertFalse(retryable(422))

    def test_accepts_only_verified_tuple_and_official_google_endpoint(self) -> None:
        validate_live(selection())

        with self.assertRaisesRegex(LiveConfigurationError, "official endpoint"):
            validate_live(selection(model_endpoint="https://gateway.example/v1"))

    def test_worker_requires_both_application_credentials(self) -> None:
        with self.assertRaisesRegex(LiveConfigurationError, "MODEL_AI_KEY"):
            live_activities(selection(), {})
        with self.assertRaisesRegex(LiveConfigurationError, "CLASSIFIER_AI_KEY"):
            live_activities(selection(), {"MODEL_AI_KEY": "model-secret"})

    def test_worker_maps_model_key_without_changing_catalog_data(self) -> None:
        environment = {
            "MODEL_AI_KEY": "model-secret",
            "CLASSIFIER_AI_KEY": "classifier-secret",
        }

        classifier, model_activities = live_activities(selection(), environment)

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
                        "relevant": {
                            "type": "choice",
                            "choice": "yes",
                            "confidence": 0.97,
                            "probabilities": {"yes": 0.97, "no": 0.03},
                        }
                    },
                }
            )

        classifier = JevClassifier(
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
        self.assertEqual(result, MockClassification(True, "Jev selected yes (0.97)"))

    def test_authentication_failure_is_non_retryable(self) -> None:
        def reject(*args: object, **kwargs: object):
            raise HTTPError("https://jev.invalid", 401, "Unauthorized", {}, None)

        classifier = JevClassifier(
            "classifier-secret",
            "https://api.typesafe.ai/v1/systemone",
            "jev-latest",
            opener=reject,
        )

        with self.assertRaises(LiveAuthenticationError):
            classifier.classify(ReleaseNote("2.5.0", "Retry change."))

    def test_malformed_relevant_answer_is_a_schema_error(self) -> None:
        classifier = JevClassifier(
            "classifier-secret",
            "https://api.typesafe.ai/v1/systemone",
            "jev-latest",
            opener=lambda *args, **kwargs: FakeResponse({"answers": {"relevant": []}}),
        )

        with self.assertRaises(LiveSchemaError):
            classifier.classify(ReleaseNote("2.5.0", "Retry change."))


class GeminiFailureTests(unittest.IsolatedAsyncioTestCase):
    async def test_google_invalid_key_is_fatal_and_provider_details_are_hidden(self):
        error = ClientError(400, {"error": {
            "message": "provider detail must not be logged",
            "details": [{"reason": "API_KEY_INVALID"}],
        }})
        with patch.object(GoogleADKActivities, "generate_content_async",
                          new=AsyncMock(side_effect=error)):
            try:
                await GeminiActivities().generate_content_async("gemini-3.5-flash-lite", LlmRequest())
            except LiveAuthenticationError as failure:
                from workflow import AI_ACTIVITY_OPTIONS, _fatal
                self.assertTrue(_fatal(failure))
                self.assertIn("LiveAuthenticationError",
                              AI_ACTIVITY_OPTIONS["retry_policy"]["non_retryable_error_reasons"])
                self.assertNotIn("provider detail", "".join(traceback.format_exception(failure)))
            else:
                self.fail("invalid key was not surfaced as an authentication failure")

    async def test_other_google_errors_keep_their_failure_category(self):
        for status, expected in ((400, LiveSchemaError), (401, LiveAuthenticationError),
                                 (429, RuntimeError), (503, RuntimeError)):
            with self.subTest(status=status), patch.object(
                GoogleADKActivities, "generate_content_async",
                new=AsyncMock(side_effect=ClientError(status, {})),
            ):
                with self.assertRaises(expected):
                    await GeminiActivities().generate_content_async("gemini-3.5-flash-lite", LlmRequest())


if __name__ == "__main__":
    unittest.main()
