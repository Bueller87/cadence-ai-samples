import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from agents import ModelResponse
from agents.usage import Usage, serialize_usage
from openai.types.responses import ResponseOutputMessage, ResponseOutputText

from config import ModelConfig, load_model
from inference import (
    CHAT_ACTIVITY,
    NATIVE_ACTIVITY,
    build_model_activities,
    generate_live_answer,
)
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


def response() -> ModelResponse:
    return ModelResponse(
        output=[
            ResponseOutputMessage(
                id="offline-message",
                type="message",
                role="assistant",
                status="completed",
                content=[
                    ResponseOutputText(
                        type="output_text",
                        text="Review retry limits.",
                        annotations=[],
                        logprobs=[],
                    )
                ],
            )
        ],
        usage=Usage(),
        response_id=None,
    )


def chat_result() -> dict[str, object]:
    reply = response()
    return {
        "output": [item.model_dump(mode="json") for item in reply.output],
        "usage": serialize_usage(reply.usage),
        "response_id": None,
    }


class LiveValidationTests(unittest.TestCase):
    def test_root_catalog_supported_models_validate_without_provider_calls(self) -> None:
        for model_id in MODEL_IDS:
            with self.subTest(model_id=model_id):
                validate_live(load_model(ROOT, model_id))

    def test_rejects_catalog_candidates_and_unsafe_endpoints(self) -> None:
        with self.assertRaisesRegex(LiveConfigurationError, "supported models"):
            validate_live(
                ModelConfig(
                    "future-model",
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
    async def test_agent_model_requests_use_only_cadence_activities(self) -> None:
        for model_id in MODEL_IDS:
            selection = load_model(ROOT, model_id)
            expected_activity = (
                NATIVE_ACTIVITY
                if selection.provider == "openai"
                else CHAT_ACTIVITY
            )
            result = (
                response()
                if selection.provider == "openai"
                else chat_result()
            )
            with self.subTest(model_id=model_id), patch(
                "inference.workflow.execute_activity",
                new=AsyncMock(return_value=result),
            ) as execute:
                answer = await generate_live_answer(
                    "Review retries.",
                    selection.model,
                    selection.provider,
                )
            self.assertEqual(answer, "Review retry limits.")
            self.assertEqual(execute.call_args.args[0], expected_activity)


if __name__ == "__main__":
    unittest.main()
