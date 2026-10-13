import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from activities import AgentRequest
from config import ModelConfig, load_model
from inference import build_model_activities
from live import (
    LiveConfigurationError,
    live_model_activities,
    validate_live,
)


ROOT = next(
    parent
    for parent in Path(__file__).resolve().parents
    if (parent / "models.yaml").is_file()
)
MODEL_IDS = ("gemini-flash-lite", "llama3.2-local", "openai-nano")

class LiveValidationTests(unittest.TestCase):
    def test_root_catalog_supported_models_validate_without_provider_calls(self) -> None:
        for model_id in MODEL_IDS:
            with self.subTest(model_id=model_id):
                validate_live(load_model(ROOT, model_id))

    def test_rejects_unsupported_providers_and_unsafe_endpoints(self) -> None:
        with self.assertRaisesRegex(LiveConfigurationError, "provider"):
            validate_live(
                ModelConfig(
                    "future-model",
                    "future-provider",
                    "future",
                    "https://provider.example/v1",
                )
            )
        validate_live(
            ModelConfig(
                "future-openai-model",
                "openai",
                "future",
                "https://api.openai.com/v1",
            )
        )
        with self.assertRaisesRegex(LiveConfigurationError, "loopback"):
            validate_live(
                ModelConfig(
                    "llama3.2-local",
                    "ollama",
                    "llama3.2:latest",
                    "https://ollama.example",
                )
            )
        for selection in (
            ModelConfig(
                "gemini-flash-lite",
                "google",
                "gemini-3.5-flash-lite",
                "https://google-gateway.example",
            ),
            ModelConfig(
                "openai-nano",
                "openai",
                "gpt-5-nano",
                "https://openai-gateway.example/v1",
            ),
        ):
            validate_live(selection)
        with self.assertRaisesRegex(LiveConfigurationError, "HTTPS"):
            validate_live(
                ModelConfig(
                    "openai-nano",
                    "openai",
                    "gpt-5-nano",
                    "http://example.com/v1",
                )
            )

    def test_changed_catalog_endpoints_reach_async_openai(self) -> None:
        endpoints = {
            "gemini-flash-lite": "https://google-gateway.example",
            "llama3.2-local": "http://127.0.0.1:22434",
            "openai-nano": "https://openai-gateway.example/v1",
        }
        expected_base_urls = {
            "gemini-flash-lite": endpoints["gemini-flash-lite"]
            + "/v1beta/openai/",
            "llama3.2-local": endpoints["llama3.2-local"] + "/v1/",
            "openai-nano": endpoints["openai-nano"],
        }
        catalog = f"""models:
  - id: gemini-flash-lite
    provider: google
    model: gemini-3.5-flash-lite
    endpoint: {endpoints["gemini-flash-lite"]}
  - id: llama3.2-local
    provider: ollama
    model: llama3.2:latest
    endpoint: {endpoints["llama3.2-local"]}
  - id: openai-nano
    provider: openai
    model: gpt-5-nano
    endpoint: {endpoints["openai-nano"]}
"""
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / "models.yaml").write_text(catalog, encoding="utf-8")
            with patch("inference.AsyncOpenAI") as client:
                for model_id in MODEL_IDS:
                    with self.subTest(model_id=model_id):
                        selection = load_model(directory, model_id)
                        build_model_activities(
                            selection, {"MODEL_AI_KEY": "test-key"}
                        )
                        self.assertEqual(
                            client.call_args.kwargs["base_url"],
                            expected_base_urls[model_id],
                        )
                        client.reset_mock()

    def test_live_worker_requires_key_except_for_local_ollama(self) -> None:
        google = load_model(ROOT, "gemini-flash-lite")
        with self.assertRaisesRegex(LiveConfigurationError, "MODEL_AI_KEY"):
            live_model_activities(google, {})

        ollama = load_model(ROOT, "llama3.2-local")
        with patch(
            "inference.build_model_activities", return_value=object()
        ) as build:
            live_model_activities(ollama, {})
        build.assert_called_once()


class AgentBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_worker_activity_owns_agent_model_selection(self) -> None:
        for model_id in MODEL_IDS:
            selection = load_model(ROOT, model_id)
            with (
                self.subTest(model_id=model_id),
                patch("inference.AsyncOpenAI"),
                patch(
                    "inference.Runner.run",
                    new=AsyncMock(
                        return_value=SimpleNamespace(
                            final_output="Review retry limits."
                        )
                    ),
                ) as run,
            ):
                activities = build_model_activities(
                    selection, {"MODEL_AI_KEY": "test-key"}
                )
                answer = await activities.run_agent(
                    AgentRequest(
                        "planner",
                        "Return two bounded research questions.",
                    )
                )
            self.assertEqual(answer, "Review retry limits.")
            run.assert_awaited_once()
            self.assertEqual(activities._selection, selection)


if __name__ == "__main__":
    unittest.main()
