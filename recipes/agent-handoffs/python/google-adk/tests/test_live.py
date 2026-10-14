import os
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from cadence.contrib.google_adk import GoogleADKActivities
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.genai import types

from config import ModelConfig, load_model
from inference import ADKActivities, build_model_activities, runtime_model
from live import (
    LiveAuthenticationError,
    LiveConfigurationError,
    LiveSchemaError,
    live_model_activities,
    raise_model_error,
    validate_live,
)


ROOT = next(
    parent
    for parent in Path(__file__).resolve().parents
    if (parent / "models.yaml").is_file()
)
MODEL_IDS = ("gemini-flash-lite", "llama3.2-local", "openai-nano")


class LiveValidationTests(unittest.TestCase):
    def test_root_catalog_models_validate_without_provider_calls(self) -> None:
        for model_id in MODEL_IDS:
            with self.subTest(model_id=model_id):
                validate_live(load_model(ROOT, model_id))

    def test_rejects_unsupported_providers_and_unsafe_endpoints(self) -> None:
        with self.assertRaisesRegex(LiveConfigurationError, "provider"):
            validate_live(
                ModelConfig("future", "custom", "future", "https://example.com")
            )
        with self.assertRaisesRegex(LiveConfigurationError, "loopback"):
            validate_live(
                ModelConfig(
                    "local", "ollama", "model", "https://ollama.example"
                )
            )
        with self.assertRaisesRegex(LiveConfigurationError, "HTTPS"):
            validate_live(
                ModelConfig(
                    "remote", "openai", "model", "http://example.com/v1"
                )
            )

    def test_live_worker_requires_key_except_for_ollama(self) -> None:
        with self.assertRaisesRegex(LiveConfigurationError, "MODEL_AI_KEY"):
            live_model_activities(load_model(ROOT, "gemini-flash-lite"), {})
        environment: dict[str, str] = {}
        activities = live_model_activities(
            load_model(ROOT, "llama3.2-local"), environment
        )
        self.assertIsInstance(activities, ADKActivities)
        self.assertEqual(environment["OLLAMA_API_BASE"], "http://localhost:11434")

    def test_runtime_model_mapping(self) -> None:
        self.assertEqual(runtime_model("google", "gemini"), "gemini")
        self.assertEqual(runtime_model("ollama", "llama"), "ollama_chat/llama")
        self.assertEqual(runtime_model("openai", "gpt"), "openai/gpt")
        with self.assertRaises(LiveConfigurationError):
            runtime_model("other", "model")

    def test_provider_errors_are_sanitized_and_classified(self) -> None:
        class ProviderError(Exception):
            status_code = 401

        with self.assertRaisesRegex(
            LiveAuthenticationError, "model rejected MODEL_AI_KEY"
        ):
            raise_model_error(ProviderError("secret provider body"))
        ProviderError.status_code = 400
        with self.assertRaisesRegex(
            LiveSchemaError, "model rejected request: HTTP 400"
        ):
            raise_model_error(ProviderError("secret provider body"))
        ProviderError.status_code = 503
        with self.assertRaisesRegex(
            RuntimeError, "^temporary model request failure$"
        ):
            raise_model_error(ProviderError("secret provider body"))


class ModelActivityTests(unittest.IsolatedAsyncioTestCase):
    async def test_worker_rewrites_a_request_copy_for_every_provider(self) -> None:
        for model_id in MODEL_IDS:
            selection = load_model(ROOT, model_id)
            expected = runtime_model(selection.provider, selection.model)
            request = LlmRequest(
                model="agent-handoffs-worker-model",
                contents=[
                    types.Content(
                        role="user", parts=[types.Part(text="Synthetic request")]
                    )
                ],
            )
            activities = build_model_activities(
                selection, {"MODEL_AI_KEY": "test-key"}
            )
            captured: dict[str, object] = {}

            if selection.provider == "google":
                class FakeGemini:
                    def __init__(self, *, model: str, base_url: str) -> None:
                        captured.update(model=model, base_url=base_url)

                    async def generate_content_async(
                        self, *, llm_request: LlmRequest, stream: bool
                    ):
                        captured.update(request=llm_request, stream=stream)
                        yield LlmResponse(
                            content=types.Content(
                                role="model", parts=[types.Part(text="ok")]
                            )
                        )

                with patch("inference.Gemini", FakeGemini):
                    await activities.generate_content_async(
                        "agent-handoffs-worker-model", request
                    )
                runtime_request = captured["request"]
                self.assertEqual(captured["model"], selection.model)
                self.assertEqual(captured["base_url"], selection.endpoint)
            else:
                generate = AsyncMock(return_value=[])
                with patch.object(
                    GoogleADKActivities,
                    "generate_content_async",
                    new=generate,
                ):
                    await activities.generate_content_async(
                        "agent-handoffs-worker-model", request
                    )
                self.assertEqual(generate.await_args.args[0], expected)
                runtime_request = generate.await_args.args[1]

            with self.subTest(model_id=model_id):
                self.assertEqual(runtime_request.model, expected)
                self.assertEqual(
                    runtime_request.contents[0].parts[0].text,
                    "Synthetic request",
                )
                self.assertEqual(request.model, "agent-handoffs-worker-model")

    def test_provider_environment_is_worker_local(self) -> None:
        for model_id, expected in (
            (
                "gemini-flash-lite",
                {
                    "GOOGLE_API_KEY": "test-key",
                    "GOOGLE_GENAI_USE_VERTEXAI": "FALSE",
                },
            ),
            (
                "openai-nano",
                {
                    "OPENAI_API_KEY": "test-key",
                    "OPENAI_BASE_URL": "https://api.openai.com/v1",
                },
            ),
            (
                "llama3.2-local",
                {"OLLAMA_API_BASE": "http://localhost:11434"},
            ),
        ):
            selection = load_model(ROOT, model_id)
            environment = {"MODEL_AI_KEY": "test-key"}
            with self.subTest(model_id=model_id), patch.dict(
                os.environ, {}, clear=True
            ):
                activities = build_model_activities(selection, environment)
            self.assertIsInstance(activities, ADKActivities)
            for key, value in expected.items():
                self.assertEqual(environment[key], value)


if __name__ == "__main__":
    unittest.main()
