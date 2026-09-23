from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1]))

from compatibility_spike import (
    ADK_ACTIVITY,
    OPENAI_ACTIVITY,
    ActivityObservation,
    SpikeCase,
    expected_model_activity,
    observations_from_history_events,
    replay_reused_completed_activities,
    summarize_observations,
    validate_start_request,
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

    def test_history_summary_omits_payloads_and_proves_replay_did_not_schedule_again(self) -> None:
        before_restart = summarize_observations(
            [
                ActivityObservation("scheduled", OPENAI_ACTIVITY),
                ActivityObservation("completed", OPENAI_ACTIVITY),
            ]
        )
        after_resume = summarize_observations(
            [
                ActivityObservation("scheduled", OPENAI_ACTIVITY),
                ActivityObservation("completed", OPENAI_ACTIVITY),
            ]
        )
        repeated_call = summarize_observations(
            [
                ActivityObservation("scheduled", OPENAI_ACTIVITY),
                ActivityObservation("completed", OPENAI_ACTIVITY),
                ActivityObservation("scheduled", OPENAI_ACTIVITY),
                ActivityObservation("completed", OPENAI_ACTIVITY),
            ]
        )

        self.assertTrue(
            replay_reused_completed_activities(
                before_restart, after_resume, OPENAI_ACTIVITY
            )
        )
        self.assertFalse(
            replay_reused_completed_activities(
                before_restart, repeated_call, OPENAI_ACTIVITY
            )
        )
        self.assertNotIn("Return the word READY", repr(before_restart))

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
