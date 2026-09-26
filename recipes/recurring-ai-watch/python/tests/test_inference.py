"""Offline dispatch, typed Activity boundaries, and agent-loop tests."""

import os
import json
import unittest
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import httpx

# Prevent optional LiteLLM metadata downloads during registry imports.
os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
os.environ["LITELLM_TELEMETRY"] = "False"

from agents import ModelResponse, ModelSettings, ModelTracing
from agents.usage import Usage
from cadence.contrib.pydantic import PydanticDataConverter
from cadence.testing import TestWorkflowEnvironment
from google.adk.models import LLMRegistry
from openai.types.responses import ResponseOutputMessage, ResponseOutputText

from config import CatalogError, load_selection
from inference import (
    CHAT_ACTIVITY, NATIVE_ACTIVITY, ChatCompletionsActivities,
    NativeOpenAIActivities, generate_live_report, runtime_model,
)
from live import ADKActivities, LiveConfigurationError, live_activities, validate_live
from main import parser, start
from workflow import CLASSIFY_ACTIVITY, WATCH_WORKFLOW, ClassificationDecision, WatchInput, WatchStatus, build_registry


ROOT = Path(__file__).resolve().parents[4]
PAIRS = [(agent, model) for agent in ("google-adk", "openai-agents")
         for model in ("gemini-flash-lite", "llama3.2-local", "openai-nano")]


def selected(agent, model, classifier="jev-default"):
    return load_selection(ROOT, agent_id=agent, model_id=model, classifier_id=classifier)


def response():
    return ModelResponse(output=[ResponseOutputMessage(
        id="offline-message", type="message", role="assistant", status="completed",
        content=[ResponseOutputText(type="output_text", text="Review retry limits.",
                                    annotations=[], logprobs=[])],
    )], usage=Usage(), response_id=None)


class InferenceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        # Guard HTTP transports, not low-level sockets used by SDK async internals.
        for target in ("httpx.AsyncClient.send", "httpx.Client.send"):
            guard = patch(target, side_effect=AssertionError("offline test attempted HTTP"))
            guard.start()
            self.addCleanup(guard.stop)

    def test_all_six_worker_paths_and_credentials(self):
        for agent, model in PAIRS:
            with self.subTest(agent=agent, model=model):
                selection = selected(agent, model)
                environment = {"CLASSIFIER_AI_KEY": "test-classifier"}
                if selection.model.provider != "ollama":
                    environment["MODEL_AI_KEY"] = "test-model"
                with patch("inference.AsyncOpenAI") as client:
                    classifier, activities = live_activities(selection, environment)
                expected = (ADKActivities if agent == "google-adk" else
                            NativeOpenAIActivities if model == "openai-nano" else
                            ChatCompletionsActivities)
                self.assertIsInstance(activities, expected)
                self.assertNotIn("test-classifier", repr(classifier))
                if selection.model.provider == "google":
                    self.assertEqual(environment["GOOGLE_API_KEY"], "test-model")
                elif selection.model.provider == "openai":
                    self.assertEqual(environment["OPENAI_API_KEY"], "test-model")
                    self.assertEqual(environment["OPENAI_BASE_URL"], selection.model.endpoint)
                else:
                    self.assertEqual(environment["OLLAMA_API_BASE"], selection.model.endpoint)
                    self.assertNotIn("MODEL_AI_KEY", environment)
                if agent == "google-adk":
                    client.assert_not_called()
                else:
                    expected_url = {"google": selection.model.endpoint + "/v1beta/openai/",
                                    "ollama": selection.model.endpoint + "/v1/",
                                    "openai": selection.model.endpoint}[selection.model.provider]
                    self.assertEqual(client.call_args.kwargs["base_url"], expected_url)
                    self.assertEqual(client.call_args.kwargs["max_retries"], 0)

    def test_adk_registry_translations(self):
        for model_id, expected, cls in (
            ("gemini-flash-lite", "gemini-3.5-flash-lite", "Gemini"),
            ("llama3.2-local", "ollama_chat/llama3.2:latest", "LiteLlm"),
            ("openai-nano", "openai/gpt-5-nano", "LiteLlm"),
        ):
            selection = selected("google-adk", model_id)
            actual = runtime_model("google-adk", selection.model.provider, selection.model.model)
            self.assertEqual(actual, expected)
            self.assertEqual(LLMRegistry.resolve(actual).__name__, cls)

    def test_unsupported_classifiers_and_unknown_ids(self):
        for classifier in ("laya-local", "von-local", "reflex-local", "kev-local"):
            with self.assertRaisesRegex(LiveConfigurationError, "only jev-default"):
                validate_live(selected("google-adk", "gemini-flash-lite", classifier))
        with self.assertRaises(CatalogError):
            selected("unknown", "gemini-flash-lite")
        with self.assertRaises(CatalogError):
            selected("google-adk", "unknown")

    async def test_adk_openai_transport_with_mock_http(self):
        from google.adk.models.llm_request import LlmRequest
        from google.genai import types
        requests = []
        async def respond(client, request, **kwargs):
            requests.append(request)
            return httpx.Response(200, request=request, json={
                "id": "offline", "object": "chat.completion", "created": 1,
                "model": "gpt-5-nano", "choices": [{"index": 0, "finish_reason": "stop",
                    "message": {"role": "assistant", "content": "Review retries."}}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
            })
        with patch.dict(os.environ, {"MODEL_AI_KEY": "test-model", "CLASSIFIER_AI_KEY": "test-jev"}), \
                patch.object(httpx.AsyncClient, "send", respond):
            _, activities = live_activities(selected("google-adk", "openai-nano"))
            result = await activities.generate_content_async("openai/gpt-5-nano", LlmRequest(
                model="openai/gpt-5-nano", contents=[types.Content(role="user", parts=[types.Part(text="Report")])],
            ))
        self.assertEqual(len(requests), 1)
        self.assertEqual(str(requests[0].url), "https://api.openai.com/v1/chat/completions")
        self.assertEqual(json.loads(requests[0].content)["model"], "gpt-5-nano")
        self.assertEqual(result[0].content.parts[0].text, "Review retries.")

    async def test_openai_errors_are_sanitized_and_keep_retry_category(self):
        from live import LiveAuthenticationError, LiveSchemaError
        for status, expected in ((401, LiveAuthenticationError), (400, LiveSchemaError),
                                 (429, RuntimeError), (503, RuntimeError)):
            from openai import APIStatusError
            error = APIStatusError("do not expose test-secret", response=httpx.Response(status,
                request=httpx.Request("POST", "https://example.invalid")), body=None)
            provider = SimpleNamespace(get_model=lambda name: SimpleNamespace(
                get_response=AsyncMock(side_effect=error)))
            with self.assertRaises(expected) as caught:
                await ChatCompletionsActivities(provider).invoke_model("test", None, "report", {})
            self.assertNotIn("test-secret", str(caught.exception))

    async def test_all_six_workflows_classify_then_schedule_the_selected_model(self):
        from google.adk.models.llm_response import LlmResponse
        from google.genai import types
        for agent, model in PAIRS:
            selection = selected(agent, model)
            expected = ("GoogleADKActivities.generate_content_async" if agent == "google-adk"
                        else NATIVE_ACTIVITY if model == "openai-nano" else CHAT_ACTIVITY)
            calls = []
            def classify(*args):
                calls.append("jev")
                return ClassificationDecision(True, "relevant")
            def model_response(*args):
                calls.append(expected)
                if agent == "google-adk":
                    return [LlmResponse(content=types.Content(role="model", parts=[types.Part(text="Review retries.")]))]
                if expected == NATIVE_ACTIVITY:
                    return response()
                from agents.usage import serialize_usage
                reply = response()
                return {"output": [item.model_dump(mode="json") for item in reply.output],
                        "usage": serialize_usage(reply.usage), "response_id": None}
            with self.subTest(agent=agent, model=model), TestWorkflowEnvironment(
                build_registry(), data_converter=PydanticDataConverter()
            ) as env:
                env.on_activity(CLASSIFY_ACTIVITY, fn=classify)
                env.on_activity(expected, fn=model_response)
                execution = await env.client.start_workflow(
                    WATCH_WORKFLOW, WatchInput(mode="live", max_checks=1,
                        agent_framework=agent, model_provider=selection.model.provider,
                        model_name=selection.model.model),
                    workflow_id=f"offline-{agent}-{model}", task_list="test-task-list",
                )
                result = env.get_workflow_result(WatchStatus, execution.workflow_id, execution.run_id)
                self.assertEqual(calls, ["jev", expected])
                self.assertEqual(result.state, "COMPLETED")
                self.assertTrue(result.latest_report)

    def test_native_activity_keeps_real_typed_boundary(self):
        signature = NativeOpenAIActivities.invoke_model.signature
        converter = PydanticDataConverter()
        arguments = ["gpt-5-nano", None, [{"role": "user", "content": "report"}],
                     ModelSettings(), [], None, [], ModelTracing.DISABLED, None, None, None]
        decoded = signature.params_from_payload(converter, converter.to_data(arguments))
        self.assertEqual(decoded[2], arguments[2])
        self.assertIsInstance(decoded[3], ModelSettings)
        self.assertIs(decoded[7], ModelTracing.DISABLED)
        restored = converter.from_data(converter.to_data([response()]), [signature.return_type])[0]
        self.assertIsInstance(restored, ModelResponse)
        self.assertEqual(NativeOpenAIActivities.invoke_model.name, NATIVE_ACTIVITY)

    async def test_openai_agent_calls_only_expected_activity(self):
        from workflow import ReleaseNote, AI_ACTIVITY_OPTIONS
        from agents.usage import serialize_usage
        for provider, expected in (("openai", NATIVE_ACTIVITY), ("google", CHAT_ACTIVITY),
                                    ("ollama", CHAT_ACTIVITY)):
            reply = response()
            result = reply if provider == "openai" else {
                "output": [item.model_dump(mode="json") for item in reply.output],
                "usage": serialize_usage(reply.usage), "response_id": None,
            }
            with patch("inference.workflow.execute_activity", new=AsyncMock(return_value=result)) as execute:
                report = await generate_live_report(ReleaseNote("2.5", "Retry changes"),
                                                    "test-model", "openai-agents", provider)
            self.assertEqual(report, "Review retry limits.")
            self.assertEqual(execute.call_args.args[0], expected)
            self.assertEqual(execute.call_args.kwargs, AI_ACTIVITY_OPTIONS)

    async def test_native_activity_uses_sdk_body_and_chat_bridge_uses_chat_provider(self):
        provider = SimpleNamespace(get_model=lambda name: SimpleNamespace(
            get_response=AsyncMock(return_value=response())))
        native = NativeOpenAIActivities(provider)
        native_result = await native.invoke_model("gpt-5-nano", None, "report", ModelSettings(), [],
                                                 None, [], ModelTracing.DISABLED, None, None, None)
        self.assertIsInstance(native_result, ModelResponse)
        self.assertEqual(native_result.output[0].content[0].text, "Review retry limits.")
        result = await ChatCompletionsActivities(provider).invoke_model("test", None, "report", {})
        self.assertEqual(result["output"][0]["content"][0]["text"], "Review retry limits.")

    async def test_start_carries_selection_but_no_endpoints_or_credentials(self):
        for agent, model in PAIRS:
            args = parser().parse_args(["--agent-id", agent, "--model-id", model, "start", "--mode", "live"])
            selection = selected(agent, model)
            client = SimpleNamespace(start_workflow=AsyncMock(return_value=SimpleNamespace(workflow_id="offline")),
                                     close=AsyncMock())
            with patch("main.client", return_value=client):
                await start(args, selection)
            watch_input = client.start_workflow.call_args.args[1]
            self.assertEqual(watch_input.agent_framework, agent)
            self.assertEqual(watch_input.model_provider, selection.model.provider)
            self.assertEqual(watch_input.model_name, selection.model.model)
            self.assertFalse(any("key" in field or "endpoint" in field for field in asdict(watch_input)))


if __name__ == "__main__":
    unittest.main()
