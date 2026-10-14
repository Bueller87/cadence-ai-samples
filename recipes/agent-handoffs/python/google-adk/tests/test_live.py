import os
import unittest
from pathlib import Path
from unittest.mock import patch

from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.genai import types

from config import ModelConfig, load_model
from inference import ADKActivities, build_model_activities, runtime_model
from live import LiveConfigurationError, live_model_activities, validate_live


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


class ModelActivityTests(unittest.IsolatedAsyncioTestCase):
    async def test_changed_google_endpoint_reaches_gemini_transport(self) -> None:
        selection = ModelConfig(
            "gemini",
            "google",
            "gemini-test",
            "https://google-gateway.example/v1beta",
        )
        activities = build_model_activities(
            selection, {"MODEL_AI_KEY": "test-key"}
        )
        captured: dict[str, object] = {}

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

        request = LlmRequest(model="logical")
        with patch("inference.Gemini", FakeGemini):
            result = await activities.generate_content_async("logical", request)
        self.assertEqual(captured["model"], "gemini-test")
        self.assertEqual(
            captured["base_url"], "https://google-gateway.example/v1beta"
        )
        self.assertEqual(result[0].content.parts[0].text, "ok")

    def test_provider_environment_is_worker_local(self) -> None:
        for model_id, key in (
            ("gemini-flash-lite", "GOOGLE_API_KEY"),
            ("openai-nano", "OPENAI_API_KEY"),
            ("llama3.2-local", "OLLAMA_API_BASE"),
        ):
            selection = load_model(ROOT, model_id)
            environment = {"MODEL_AI_KEY": "test-key"}
            with self.subTest(model_id=model_id), patch.dict(
                os.environ, {}, clear=True
            ):
                activities = build_model_activities(selection, environment)
            self.assertIsInstance(activities, ADKActivities)
            self.assertIn(key, environment)


if __name__ == "__main__":
    unittest.main()
