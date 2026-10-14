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

    def test_open_catalog_and_safe_endpoints(self) -> None:
        validate_live(
            ModelConfig(
                "future-openai", "openai", "future", "https://gateway.example/v1"
            )
        )
        with self.assertRaisesRegex(LiveConfigurationError, "provider"):
            validate_live(
                ModelConfig("future", "future", "future", "https://example.com")
            )
        with self.assertRaisesRegex(LiveConfigurationError, "loopback"):
            validate_live(
                ModelConfig("local", "ollama", "llama", "https://ollama.example")
            )
        with self.assertRaisesRegex(LiveConfigurationError, "HTTPS"):
            validate_live(
                ModelConfig("remote", "openai", "gpt", "http://example.com/v1")
            )

    def test_worker_configuration_maps_models_and_credentials(self) -> None:
        for model_id in MODEL_IDS:
            selection = load_model(ROOT, model_id)
            environ = {"MODEL_AI_KEY": "test-key"}
            activities = build_model_activities(selection, environ)
            self.assertIsInstance(activities, ADKActivities)
            if selection.provider == "google":
                self.assertEqual(environ["GOOGLE_API_KEY"], "test-key")
            elif selection.provider == "openai":
                self.assertEqual(environ["OPENAI_BASE_URL"], selection.endpoint)
            else:
                self.assertEqual(environ["OLLAMA_API_BASE"], selection.endpoint)
        self.assertEqual(runtime_model("ollama", "llama"), "ollama_chat/llama")

    def test_live_worker_requires_key_except_for_ollama(self) -> None:
        with self.assertRaisesRegex(LiveConfigurationError, "MODEL_AI_KEY"):
            live_model_activities(load_model(ROOT, "gemini-flash-lite"), {})
        with patch("inference.build_model_activities", return_value=object()) as build:
            live_model_activities(load_model(ROOT, "llama3.2-local"), {})
        build.assert_called_once()

    def test_sanitized_error_classification(self) -> None:
        class ProviderError(Exception):
            status_code = 401

        with self.assertRaises(LiveAuthenticationError):
            raise_model_error(ProviderError("secret body"))
        ProviderError.status_code = 400
        with self.assertRaises(LiveSchemaError):
            raise_model_error(ProviderError("secret body"))
        ProviderError.status_code = 503
        with self.assertRaisesRegex(RuntimeError, "temporary"):
            raise_model_error(ProviderError("secret body"))


class WorkerModelRewriteTests(unittest.IsolatedAsyncioTestCase):
    async def test_worker_rewrites_logical_model_in_request_for_every_provider(
        self,
    ) -> None:
        for model_id in MODEL_IDS:
            selection = load_model(ROOT, model_id)
            expected = runtime_model(selection.provider, selection.model)
            request = LlmRequest(
                model="auto-research-worker-model",
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
                        "auto-research-worker-model", request
                    )
                runtime_request = captured["request"]
            else:
                generate = AsyncMock(return_value=[])
                with patch.object(
                    GoogleADKActivities,
                    "generate_content_async",
                    new=generate,
                ):
                    await activities.generate_content_async(
                        "auto-research-worker-model", request
                    )
                self.assertEqual(generate.await_args.args[0], expected)
                runtime_request = generate.await_args.args[1]

            with self.subTest(model_id=model_id):
                self.assertEqual(runtime_request.model, expected)
                self.assertEqual(
                    runtime_request.contents[0].parts[0].text,
                    "Synthetic request",
                )
                self.assertEqual(request.model, "auto-research-worker-model")


if __name__ == "__main__":
    unittest.main()
