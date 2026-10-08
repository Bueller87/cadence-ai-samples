"""Offline dispatch, typed Activity boundaries, and Google ADK agent-loop tests."""

import os
import json
import traceback
import unittest
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import httpx

# Prevent optional LiteLLM metadata downloads during registry imports.
os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
os.environ["LITELLM_TELEMETRY"] = "False"

from cadence.api.v1 import workflow_pb2
from cadence.contrib.google_adk import GoogleADKActivities
from cadence.contrib.pydantic import PydanticDataConverter
from cadence.testing import TestWorkflowEnvironment
from google.adk.models import LLMRegistry
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.genai import types
from google.genai.errors import ClientError

from config import CatalogError, load_selection
from inference import ADKActivities, runtime_model
from live import (
    LiveAuthenticationError, LiveConfigurationError, LiveSchemaError,
    live_activities, validate_live,
)
from main import parser, start
from workflow import CLASSIFY_ACTIVITY, WATCH_WORKFLOW, ClassificationDecision, WatchInput, WatchStatus, build_registry


ROOT = Path(__file__).resolve().parents[5]
MODELS = ("gemini-flash-lite", "llama3.2-local", "openai-nano")
ADK_ACTIVITY = "GoogleADKActivities.generate_content_async"


def selected(model, classifier="jev-default"):
    return load_selection(ROOT, model_id=model, classifier_id=classifier)


class InferenceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        # Guard HTTP transports, not low-level sockets used by SDK async internals.
        for target in ("httpx.AsyncClient.send", "httpx.Client.send"):
            guard = patch(target, side_effect=AssertionError("offline test attempted HTTP"))
            guard.start()
            self.addCleanup(guard.stop)

    def test_all_three_worker_paths_and_credentials(self):
        for model in MODELS:
            with self.subTest(model=model):
                selection = selected(model)
                environment = {"CLASSIFIER_AI_KEY": "test-classifier"}
                if selection.model.provider != "ollama":
                    environment["MODEL_AI_KEY"] = "test-model"
                classifier, activities = live_activities(selection, environment)
                self.assertIsInstance(activities, ADKActivities)
                self.assertNotIn("test-classifier", repr(classifier))
                if selection.model.provider == "google":
                    self.assertEqual(environment["GOOGLE_API_KEY"], "test-model")
                elif selection.model.provider == "openai":
                    self.assertEqual(environment["OPENAI_API_KEY"], "test-model")
                    self.assertEqual(environment["OPENAI_BASE_URL"], selection.model.endpoint)
                else:
                    self.assertEqual(environment["OLLAMA_API_BASE"], selection.model.endpoint)
                    self.assertNotIn("MODEL_AI_KEY", environment)

    def test_adk_registry_translations(self):
        for model_id, expected, cls in (
            ("gemini-flash-lite", "gemini-3.5-flash-lite", "Gemini"),
            ("llama3.2-local", "ollama_chat/llama3.2:latest", "LiteLlm"),
            ("openai-nano", "openai/gpt-5-nano", "LiteLlm"),
        ):
            selection = selected(model_id)
            actual = runtime_model(selection.model.provider, selection.model.model)
            self.assertEqual(actual, expected)
            self.assertEqual(LLMRegistry.resolve(actual).__name__, cls)
        with self.assertRaises(LiveConfigurationError):
            runtime_model("anthropic", "model")

    def test_supported_classifiers_and_unknown_ids(self):
        validate_live(selected("gemini-flash-lite", "jev-default"))
        validate_live(selected("gemini-flash-lite", "laya-local"))
        for classifier in ("von-local", "reflex-local", "kev-local"):
            with self.assertRaises(CatalogError):
                selected("gemini-flash-lite", classifier)
        with self.assertRaises(CatalogError):
            selected("unknown")

    async def test_adk_openai_transport_with_mock_http(self):
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
            _, activities = live_activities(selected("openai-nano"))
            result = await activities.generate_content_async("openai/gpt-5-nano", LlmRequest(
                model="openai/gpt-5-nano", contents=[types.Content(role="user", parts=[types.Part(text="Report")])],
            ))
        self.assertEqual(len(requests), 1)
        self.assertEqual(str(requests[0].url), "https://api.openai.com/v1/chat/completions")
        self.assertEqual(json.loads(requests[0].content)["model"], "gpt-5-nano")
        self.assertEqual(result[0].content.parts[0].text, "Review retries.")

    async def test_all_three_workflows_classify_then_schedule_the_adk_activity(self):
        for model in MODELS:
            selection = selected(model)
            calls = []
            def classify(*args):
                calls.append("jev")
                return ClassificationDecision(True, "relevant")
            def model_response(*args):
                calls.append(ADK_ACTIVITY)
                return [LlmResponse(content=types.Content(role="model", parts=[types.Part(text="Review retries.")]))]
            with self.subTest(model=model), TestWorkflowEnvironment(
                build_registry(), data_converter=PydanticDataConverter()
            ) as env:
                env.on_activity(CLASSIFY_ACTIVITY, fn=classify)
                env.on_activity(ADK_ACTIVITY, fn=model_response)
                execution = await env.client.start_workflow(
                    WATCH_WORKFLOW, WatchInput(mode="live", max_checks=1,
                        model_provider=selection.model.provider,
                        model_name=selection.model.model),
                    workflow_id=f"offline-google-adk-{model}", task_list="test-task-list",
                )
                result = env.get_workflow_result(WatchStatus, execution.workflow_id, execution.run_id)
                self.assertEqual(calls, ["jev", ADK_ACTIVITY])
                self.assertEqual(result.state, "COMPLETED")
                self.assertTrue(result.latest_report)

    async def test_start_carries_selection_but_no_endpoints_or_credentials(self):
        for model in MODELS:
            args = parser().parse_args(["--model-id", model, "start", "--mode", "live"])
            selection = selected(model)
            client = SimpleNamespace(start_workflow=AsyncMock(return_value=SimpleNamespace(workflow_id="offline")),
                                     close=AsyncMock())
            with patch("main.client", return_value=client):
                await start(args, selection)
            watch_input = client.start_workflow.call_args.args[1]
            self.assertEqual(
                client.start_workflow.call_args.kwargs["workflow_id_reuse_policy"],
                workflow_pb2.WORKFLOW_ID_REUSE_POLICY_ALLOW_DUPLICATE,
            )
            self.assertEqual(watch_input.model_provider, selection.model.provider)
            self.assertEqual(watch_input.model_name, selection.model.model)
            self.assertFalse(any("key" in field or "endpoint" in field for field in asdict(watch_input)))


class GeminiFailureTests(unittest.IsolatedAsyncioTestCase):
    async def test_google_invalid_key_is_fatal_and_provider_details_are_hidden(self):
        error = ClientError(400, {"error": {
            "message": "provider detail must not be logged",
            "details": [{"reason": "API_KEY_INVALID"}],
        }})
        with patch.object(GoogleADKActivities, "generate_content_async",
                          new=AsyncMock(side_effect=error)):
            try:
                await ADKActivities().generate_content_async("gemini-3.5-flash-lite", LlmRequest())
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
                    await ADKActivities().generate_content_async("gemini-3.5-flash-lite", LlmRequest())


if __name__ == "__main__":
    unittest.main()
