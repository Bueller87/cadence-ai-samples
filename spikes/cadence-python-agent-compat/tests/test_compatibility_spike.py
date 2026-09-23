from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1]))

from compatibility_spike import (
    ADK_ACTIVITY,
    ECHO_ACTIVITY,
    OPENAI_ACTIVITY,
    RESUME_SIGNAL,
    ActivityObservation,
    HistoryEvidence,
    SpikeCase,
    agent_instruction,
    analyze_history_events,
    build_worker_client,
    expected_model_activity,
    fetch_complete_history_events,
    observations_from_history_events,
    summarize_observations,
    validate_start_request,
    verification_errors,
    workflow_type_for,
)


class CompatibilitySpikeTests(unittest.TestCase):
    def test_cloud_cases_require_explicit_confirmation(self) -> None:
        with self.assertRaisesRegex(ValueError, "confirm-live"):
            validate_start_request(SpikeCase.OPENAI_OPENAI, "gpt-test", False)
        with self.assertRaisesRegex(ValueError, "confirm-live"):
            validate_start_request(SpikeCase.ADK_GEMINI, "gemini-test", False)

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
