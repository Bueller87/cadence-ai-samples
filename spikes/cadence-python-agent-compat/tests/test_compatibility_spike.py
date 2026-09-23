from __future__ import annotations

import asyncio
import io
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1]))

from compatibility_spike import (
    ADK_ACTIVITY,
    ECHO_ACTIVITY,
    OPENAI_ACTIVITY,
    OPENAI_COMPAT_ACTIVITY,
    RESUME_SIGNAL,
    ActivityObservation,
    HistoryEvidence,
    OpenAICompatibleCadenceModel,
    OpenAICompatibleChatCompletionsActivities,
    SpikeCase,
    agent_instruction,
    analyze_history_events,
    build_worker_client,
    expected_model_activity,
    fetch_complete_history_events,
    observations_from_history_events,
    openai_compatible_provider_config,
    parser,
    run_cli,
    summarize_observations,
    validate_start_request,
    verification_errors,
    workflow_type_for,
)


class CompatibilitySpikeTests(unittest.TestCase):
    def test_ctrl_c_exits_cli_quietly_after_asyncio_cleanup(self) -> None:
        def interrupted_run(coroutine):
            coroutine.close()
            raise KeyboardInterrupt

        output = io.StringIO()
        with patch("compatibility_spike.asyncio.run", side_effect=interrupted_run):
            with redirect_stdout(output):
                run_cli()

        self.assertEqual(output.getvalue().strip(), "worker stopped")

    def test_cloud_cases_require_explicit_confirmation(self) -> None:
        with self.assertRaisesRegex(ValueError, "confirm-live"):
            validate_start_request(SpikeCase.OPENAI_OPENAI, "gpt-test", False)
        with self.assertRaisesRegex(ValueError, "confirm-live"):
            validate_start_request(SpikeCase.ADK_GEMINI, "gemini-test", False)
        with self.assertRaisesRegex(ValueError, "confirm-live"):
            validate_start_request(SpikeCase.OPENAI_GEMINI, "gemini-test", False)

    def test_openai_compatible_case_selection_and_no_tool_scope(self) -> None:
        parsed = parser().parse_args(
            [
                "start",
                "--case",
                "openai-gemini",
                "--model",
                "gemini-test",
                "--workflow-id",
                "test-id",
                "--confirm-live",
            ]
        )
        self.assertEqual(parsed.case, SpikeCase.OPENAI_GEMINI.value)
        self.assertEqual(
            expected_model_activity(SpikeCase.OPENAI_GEMINI),
            OPENAI_COMPAT_ACTIVITY,
        )
        self.assertEqual(
            expected_model_activity(SpikeCase.OPENAI_OLLAMA),
            OPENAI_COMPAT_ACTIVITY,
        )
        self.assertEqual(
            workflow_type_for(SpikeCase.OPENAI_GEMINI),
            workflow_type_for(SpikeCase.OPENAI_OPENAI),
        )
        with self.assertRaisesRegex(ValueError, "does not support --with-tool"):
            validate_start_request(
                SpikeCase.OPENAI_GEMINI,
                "gemini-test",
                True,
                use_tool=True,
            )
        with self.assertRaisesRegex(ValueError, "llama3.2:latest"):
            validate_start_request(SpikeCase.OPENAI_OLLAMA, "llama3.2", False)
        validate_start_request(
            SpikeCase.OPENAI_OLLAMA,
            "llama3.2:latest",
            False,
        )

    def test_openai_compatible_worker_configuration(self) -> None:
        with self.assertRaisesRegex(ValueError, "GEMINI_API_KEY"):
            openai_compatible_provider_config(SpikeCase.OPENAI_GEMINI, {})

        gemini = openai_compatible_provider_config(
            SpikeCase.OPENAI_GEMINI,
            {"GEMINI_API_KEY": "secret-value"},
        )
        self.assertEqual(
            gemini.base_url,
            "https://generativelanguage.googleapis.com/v1beta/openai/",
        )
        self.assertNotIn("secret-value", repr(gemini))

        ollama = openai_compatible_provider_config(SpikeCase.OPENAI_OLLAMA, {})
        self.assertEqual(ollama.base_url, "http://localhost:11434/v1/")
        self.assertEqual(ollama.api_key, "ollama")
        with self.assertRaisesRegex(ValueError, "loopback"):
            openai_compatible_provider_config(
                SpikeCase.OPENAI_OLLAMA,
                {"OLLAMA_OPENAI_BASE_URL": "https://example.com/v1/"},
            )

    def test_openai_claude_is_blocked_before_any_client_is_created(self) -> None:
        with self.assertRaisesRegex(ValueError, "hardcodes OpenAIProvider"):
            validate_start_request(SpikeCase.OPENAI_CLAUDE, "claude-test", True)

    def test_ollama_requires_the_adk_registry_compatible_model_string(self) -> None:
        with self.assertRaisesRegex(ValueError, "ollama_chat"):
            validate_start_request(SpikeCase.ADK_OLLAMA, "llama3.2", False)
        validate_start_request(SpikeCase.ADK_OLLAMA, "ollama_chat/llama3.2", False)

    def test_native_cases_map_to_released_integration_activity_names(self) -> None:
        self.assertEqual(expected_model_activity(SpikeCase.OPENAI_OPENAI), OPENAI_ACTIVITY)
        self.assertEqual(expected_model_activity(SpikeCase.ADK_GEMINI), ADK_ACTIVITY)
        self.assertNotEqual(
            workflow_type_for(SpikeCase.OPENAI_OPENAI),
            workflow_type_for(SpikeCase.ADK_GEMINI),
        )

    def test_registry_can_be_built_without_a_provider_call(self) -> None:
        try:
            import cadence  # noqa: F401
        except ModuleNotFoundError:
            self.skipTest("released SDK dependencies are not installed")

        from compatibility_spike import build_registry

        with patch.dict("os.environ", {"OPENAI_API_KEY": "not-a-real-key"}):
            self.assertEqual(
                type(build_registry(SpikeCase.OPENAI_OPENAI)).__name__, "Registry"
            )
        self.assertEqual(
            type(build_registry(SpikeCase.ADK_GEMINI)).__name__, "Registry"
        )
        with patch("httpx.AsyncClient.send", side_effect=AssertionError("network call")):
            with patch.dict(
                "os.environ",
                {"GEMINI_API_KEY": "not-a-real-key"},
                clear=True,
            ):
                gemini_registry = build_registry(SpikeCase.OPENAI_GEMINI)
            ollama_registry = build_registry(SpikeCase.OPENAI_OLLAMA)
        self.assertIn(OPENAI_COMPAT_ACTIVITY, gemini_registry._activities)
        self.assertIn(OPENAI_COMPAT_ACTIVITY, ollama_registry._activities)

    def test_openai_compatible_model_dispatches_to_activity(self) -> None:
        try:
            from agents import (
                Agent,
                ModelResponse,
                ModelSettings,
                ModelTracing,
                RunConfig,
                Runner,
            )
            from agents.usage import Usage, serialize_usage
            from openai.types.responses import ResponseOutputMessage, ResponseOutputText
        except ModuleNotFoundError:
            self.skipTest("OpenAI Agents SDK dependency is not installed")

        expected = ModelResponse(
            output=[
                ResponseOutputMessage(
                    id="message-test",
                    content=[
                        ResponseOutputText(
                            annotations=[],
                            text="READY",
                            type="output_text",
                            logprobs=[],
                        )
                    ],
                    role="assistant",
                    status="completed",
                    type="message",
                )
            ],
            usage=Usage(),
            response_id=None,
        )
        expected_transport = {
            "output": [item.model_dump(mode="json") for item in expected.output],
            "usage": serialize_usage(expected.usage),
            "response_id": None,
            "request_id": None,
        }

        class FakeActivities:
            def __init__(self) -> None:
                self.arguments = None

            async def invoke_model(self, **kwargs):
                self.arguments = kwargs
                return expected_transport

        activities = FakeActivities()
        model = OpenAICompatibleCadenceModel("provider-model", activities)
        actual = asyncio.run(
            model.get_response(
                system_instructions="Be concise.",
                input="Return READY.",
                model_settings=ModelSettings(),
                tools=[],
                output_schema=None,
                handoffs=[],
                tracing=ModelTracing.DISABLED,
                previous_response_id=None,
                conversation_id=None,
                prompt=None,
            )
        )
        self.assertEqual(actual.output[0].content[0].text, "READY")
        self.assertEqual(activities.arguments["model_name"], "provider-model")
        self.assertEqual(activities.arguments["input"], "Return READY.")
        self.assertIsInstance(activities.arguments["model_settings"], dict)
        self.assertEqual(activities.arguments["tracing"], ModelTracing.DISABLED.value)

        async def run_agent():
            return await Runner.run(
                Agent(
                    name="offline-test",
                    model=OpenAICompatibleCadenceModel("provider-model", activities),
                    instructions="Return a short response.",
                ),
                "Return READY.",
                run_config=RunConfig(tracing_disabled=True),
            )

        run_result = asyncio.run(run_agent())
        self.assertEqual(run_result.final_output, "READY")

    def test_openai_compatible_activity_uses_mocked_chat_completions(self) -> None:
        try:
            import httpx
            from agents import ModelSettings, ModelTracing, OpenAIProvider
            from openai import AsyncOpenAI
        except ModuleNotFoundError:
            self.skipTest("OpenAI client dependencies are not installed")

        requests = []

        def respond(request):
            requests.append(request)
            return httpx.Response(
                200,
                json={
                    "id": "chatcmpl-test",
                    "object": "chat.completion",
                    "created": 1,
                    "model": "mock-model",
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": "READY"},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 3,
                        "completion_tokens": 1,
                        "total_tokens": 4,
                    },
                },
            )

        async def invoke():
            http_client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
            client = AsyncOpenAI(
                api_key="not-a-real-key",
                base_url="https://mock.invalid/v1/",
                http_client=http_client,
                max_retries=0,
            )
            activity = OpenAICompatibleChatCompletionsActivities(
                OpenAIProvider(openai_client=client, use_responses=False)
            )
            try:
                return await activity.invoke_model(
                    model_name="mock-model",
                    system_instructions="Be concise.",
                    input="Return READY.",
                    model_settings=ModelSettings().to_json_dict(),
                    tracing=ModelTracing.DISABLED.value,
                    previous_response_id=None,
                    conversation_id=None,
                )
            finally:
                await client.close()

        response = asyncio.run(invoke())
        self.assertEqual(
            OPENAI_COMPAT_ACTIVITY,
            OpenAICompatibleChatCompletionsActivities.invoke_model.name,
        )
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0].url.path, "/v1/chat/completions")
        self.assertEqual(response["usage"]["input_tokens"], 3)
        self.assertEqual(response["usage"]["output_tokens"], 1)

    def test_openai_compatible_activity_signature_is_converter_safe(self) -> None:
        try:
            from agents import ModelSettings, ModelTracing
            from cadence.contrib.pydantic import PydanticDataConverter
        except ModuleNotFoundError:
            self.skipTest("released SDK dependencies are not installed")

        converter = PydanticDataConverter()
        payload = converter.to_data(
            [
                "provider-model",
                None,
                "Return READY.",
                ModelSettings().to_json_dict(),
                ModelTracing.DISABLED.value,
                None,
                None,
            ]
        )
        parameters = (
            OpenAICompatibleChatCompletionsActivities(None)
            .invoke_model.signature.params_from_payload(converter, payload)
        )
        self.assertEqual(parameters[2], "Return READY.")
        self.assertIsInstance(parameters[3], dict)
        self.assertEqual(parameters[4], ModelTracing.DISABLED.value)

    def test_openai_compatible_history_uses_distinct_activity_name(self) -> None:
        before = HistoryEvidence(
            activities=summarize_observations(
                [
                    ActivityObservation("scheduled", OPENAI_COMPAT_ACTIVITY),
                    ActivityObservation("completed", OPENAI_COMPAT_ACTIVITY),
                ]
            ),
            signals=(),
            terminal_status=None,
        )
        self.assertEqual(
            verification_errors(
                "before-restart",
                before,
                OPENAI_COMPAT_ACTIVITY,
                expect_tool=False,
            ),
            [],
        )
        after = HistoryEvidence(
            activities=before.activities,
            signals=(RESUME_SIGNAL,),
            terminal_status="COMPLETED",
        )
        self.assertEqual(
            verification_errors(
                "after-resume",
                after,
                OPENAI_COMPAT_ACTIVITY,
                expect_tool=False,
                baseline_model_scheduled=1,
            ),
            [],
        )

    def test_worker_client_uses_pydantic_data_converter(self) -> None:
        try:
            from cadence.contrib.pydantic import PydanticDataConverter
        except ModuleNotFoundError:
            self.skipTest("released SDK dependencies are not installed")

        async def verify_client() -> None:
            client = build_worker_client("localhost:7833", "default")
            try:
                self.assertIsInstance(
                    client.data_converter, PydanticDataConverter
                )
            finally:
                await client.close()

        asyncio.run(verify_client())

    def test_history_observations_use_released_cadence_event_shapes(self) -> None:
        try:
            from cadence.api.v1.common_pb2 import ActivityType
            from cadence.api.v1.history_pb2 import (
                ActivityTaskCompletedEventAttributes,
                ActivityTaskScheduledEventAttributes,
                HistoryEvent,
            )
        except ModuleNotFoundError:
            self.skipTest("released Cadence SDK dependency is not installed")

        observations = observations_from_history_events(
            [
                HistoryEvent(
                    event_id=11,
                    activity_task_scheduled_event_attributes=(
                        ActivityTaskScheduledEventAttributes(
                            activity_type=ActivityType(name=OPENAI_ACTIVITY)
                        )
                    ),
                ),
                HistoryEvent(
                    event_id=12,
                    activity_task_completed_event_attributes=(
                        ActivityTaskCompletedEventAttributes(scheduled_event_id=11)
                    ),
                ),
            ]
        )
        self.assertEqual(
            observations,
            [
                ActivityObservation("scheduled", OPENAI_ACTIVITY),
                ActivityObservation("completed", OPENAI_ACTIVITY),
            ],
        )

    def test_staged_replay_verification_requires_full_evidence(self) -> None:
        try:
            from cadence.api.v1.common_pb2 import ActivityType
            from cadence.api.v1.history_pb2 import (
                ActivityTaskCompletedEventAttributes,
                ActivityTaskScheduledEventAttributes,
                HistoryEvent,
                WorkflowExecutionCompletedEventAttributes,
                WorkflowExecutionSignaledEventAttributes,
            )
        except ModuleNotFoundError:
            self.skipTest("released Cadence SDK dependency is not installed")

        model_events = [
            HistoryEvent(
                event_id=11,
                activity_task_scheduled_event_attributes=(
                    ActivityTaskScheduledEventAttributes(
                        activity_type=ActivityType(name=OPENAI_ACTIVITY)
                    )
                ),
            ),
            HistoryEvent(
                event_id=12,
                activity_task_completed_event_attributes=(
                    ActivityTaskCompletedEventAttributes(scheduled_event_id=11)
                ),
            ),
        ]
        before = analyze_history_events(model_events)
        self.assertNotIn("Return the word READY", repr(before))
        self.assertEqual(
            verification_errors(
                "before-restart", before, OPENAI_ACTIVITY, expect_tool=False
            ),
            [],
        )
        incomplete_after_errors = verification_errors(
            "after-resume",
            before,
            OPENAI_ACTIVITY,
            expect_tool=False,
            baseline_model_scheduled=1,
        )
        self.assertIn(
            f"Signal {RESUME_SIGNAL!r} is absent from history",
            incomplete_after_errors,
        )
        self.assertIn(
            "the Workflow did not complete successfully after the Signal",
            incomplete_after_errors,
        )

        after = analyze_history_events(
            model_events
            + [
                HistoryEvent(
                    event_id=13,
                    workflow_execution_signaled_event_attributes=(
                        WorkflowExecutionSignaledEventAttributes(
                            signal_name=RESUME_SIGNAL
                        )
                    ),
                ),
                HistoryEvent(
                    event_id=14,
                    workflow_execution_completed_event_attributes=(
                        WorkflowExecutionCompletedEventAttributes()
                    ),
                ),
            ]
        )
        self.assertEqual(
            verification_errors(
                "after-resume",
                after,
                OPENAI_ACTIVITY,
                expect_tool=False,
                baseline_model_scheduled=1,
            ),
            [],
        )
        repeated_model_call = HistoryEvidence(
            activities=summarize_observations(
                [
                    ActivityObservation("scheduled", OPENAI_ACTIVITY),
                    ActivityObservation("completed", OPENAI_ACTIVITY),
                    ActivityObservation("scheduled", OPENAI_ACTIVITY),
                    ActivityObservation("completed", OPENAI_ACTIVITY),
                ]
            ),
            signals=(RESUME_SIGNAL,),
            terminal_status="COMPLETED",
        )
        self.assertIn(
            "an additional model Activity was scheduled after restart",
            verification_errors(
                "after-resume",
                repeated_model_call,
                OPENAI_ACTIVITY,
                expect_tool=False,
                baseline_model_scheduled=1,
            ),
        )

    def test_tool_mode_explicitly_requests_and_verifies_echo_activity(self) -> None:
        instruction = agent_instruction(True)
        self.assertIn("Call echo_token exactly once", instruction)
        self.assertIn("compatibility-check", instruction)

        evidence_without_tool = analyze_history_events([])
        errors = verification_errors(
            "before-restart",
            evidence_without_tool,
            OPENAI_ACTIVITY,
            expect_tool=True,
        )
        self.assertIn("the requested echo_token Activity did not complete", errors)

        summary = summarize_observations(
            [
                ActivityObservation("scheduled", OPENAI_ACTIVITY),
                ActivityObservation("completed", OPENAI_ACTIVITY),
                ActivityObservation("scheduled", ECHO_ACTIVITY),
                ActivityObservation("completed", ECHO_ACTIVITY),
            ]
        )
        evidence_with_tool = HistoryEvidence(
            activities=summary,
            signals=(),
            terminal_status=None,
        )
        self.assertEqual(
            verification_errors(
                "before-restart",
                evidence_with_tool,
                OPENAI_ACTIVITY,
                expect_tool=True,
            ),
            [],
        )

    def test_complete_history_reader_follows_pagination(self) -> None:
        try:
            from cadence.api.v1.history_pb2 import History, HistoryEvent
            from cadence.api.v1.service_workflow_pb2 import (
                GetWorkflowExecutionHistoryResponse,
            )
        except ModuleNotFoundError:
            self.skipTest("released Cadence SDK dependency is not installed")

        responses = [
            GetWorkflowExecutionHistoryResponse(
                history=History(events=[HistoryEvent(event_id=1)]),
                next_page_token=b"page-two",
            ),
            GetWorkflowExecutionHistoryResponse(
                history=History(events=[HistoryEvent(event_id=2)]),
            ),
        ]

        class Stub:
            def __init__(self) -> None:
                self.requests = []

            async def GetWorkflowExecutionHistory(self, request):
                self.requests.append(request)
                return responses[len(self.requests) - 1]

        stub = Stub()
        client = SimpleNamespace(workflow_stub=stub)
        events = asyncio.run(
            fetch_complete_history_events(client, "default", "workflow-id")
        )
        self.assertEqual([event.event_id for event in events], [1, 2])
        self.assertEqual(stub.requests[0].next_page_token, b"")
        self.assertEqual(stub.requests[1].next_page_token, b"page-two")
