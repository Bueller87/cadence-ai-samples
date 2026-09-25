from __future__ import annotations

from datetime import timedelta
import unittest
from unittest.mock import patch

from cadence.testing import TestActivityEnvironment, TestWorkflowEnvironment
from cadence.error import ActivityFailure

from workflow import (
    CHECKS_PER_RUN,
    CHECK_NOW_SIGNAL,
    CLASSIFY_ACTIVITY,
    MOCK_REPORT_ACTIVITY,
    STOP_WATCH_SIGNAL,
    WATCH_STATUS_QUERY,
    WATCH_WORKFLOW,
    MockClassification,
    RecurringAIWatchWorkflow,
    ReleaseNote,
    WatchInput,
    WatchStatus,
    build_registry,
    mock_classify,
)


class MockActivityTests(unittest.IsolatedAsyncioTestCase):
    async def test_mock_classification_skips_and_selects_relevant_updates(self) -> None:
        with TestActivityEnvironment() as environment:
            skipped = await environment.execute_activity(
                mock_classify,
                ReleaseNote("2.4.0", "Documentation corrections."),
            )
            relevant = await environment.execute_activity(
                mock_classify,
                ReleaseNote("2.5.0", "Retry defaults changed for background jobs."),
            )

        self.assertEqual(
            skipped,
            MockClassification(False, "No background-job impact."),
        )
        self.assertEqual(
            relevant,
            MockClassification(True, "Changes background-job retry behavior."),
        )


class WatchWorkflowTests(unittest.IsolatedAsyncioTestCase):
    async def test_continuation_input_preserves_watch_state(self) -> None:
        watch = RecurringAIWatchWorkflow()
        watch._check_count = CHECKS_PER_RUN
        watch._next_update_index = 2
        watch._latest_update = ReleaseNote("2.5.0", "Retry defaults changed.")
        watch._latest_report = "keep this report"

        continued = watch._next_run(
            WatchInput(interval=timedelta(seconds=7), max_checks=25)
        )

        self.assertEqual(continued.interval, timedelta(seconds=7))
        self.assertEqual(continued.max_checks, 25)
        self.assertEqual(continued.next_update_index, 2)
        self.assertEqual(continued.latest_update.version, "2.5.0")
        self.assertEqual(continued.latest_report, "keep this report")
        self.assertEqual(continued.check_count, CHECKS_PER_RUN)
        self.assertTrue(continued.wait_before_first_check)

    async def test_continue_as_new_after_twenty_checks_carries_state(self) -> None:
        watch = RecurringAIWatchWorkflow()
        continued: list[WatchInput] = []

        class Continued(Exception):
            pass

        async def perform_check() -> None:
            watch._check_count += 1
            watch._next_update_index += 1

        async def skip_wait(_interval: timedelta) -> None:
            return None

        def capture_continuation(next_input: WatchInput) -> None:
            continued.append(next_input)
            raise Continued()

        watch._check = perform_check  # type: ignore[method-assign]
        watch._wait = skip_wait  # type: ignore[method-assign]
        with patch("workflow.workflow.continue_as_new", capture_continuation):
            with self.assertRaises(Continued):
                await watch.run(WatchInput(interval=timedelta(seconds=1)))

        self.assertEqual(len(continued), 1)
        self.assertEqual(continued[0].check_count, CHECKS_PER_RUN)
        self.assertEqual(continued[0].next_update_index, CHECKS_PER_RUN)
        self.assertTrue(continued[0].wait_before_first_check)

    async def test_continued_run_does_not_repeat_analysis_for_exhausted_updates(self) -> None:
        classifications = 0
        reports = 0

        def count_classification(*args: object) -> MockClassification:
            nonlocal classifications
            classifications += 1
            return MockClassification(False, "mocked")

        def count_report(*args: object) -> str:
            nonlocal reports
            reports += 1
            return "mocked"

        with TestWorkflowEnvironment(build_registry()) as environment:
            environment.on_activity(CLASSIFY_ACTIVITY, fn=count_classification)
            environment.on_activity(MOCK_REPORT_ACTIVITY, fn=count_report)
            started_at = environment.now()
            execution = await environment.client.start_workflow(
                WATCH_WORKFLOW,
                WatchInput(
                    interval=timedelta(seconds=1),
                    max_checks=CHECKS_PER_RUN + 1,
                    next_update_index=3,
                    latest_update=ReleaseNote("2.5.1", "Corrected documentation."),
                    latest_report="report from an earlier run",
                    check_count=CHECKS_PER_RUN,
                    wait_before_first_check=True,
                ),
                workflow_id="continued-exhausted-updates",
                task_list="test-task-list",
            )
            result = environment.get_workflow_result(
                WatchStatus, execution.workflow_id, execution.run_id
            )
            elapsed = environment.now() - started_at

        self.assertEqual(result.check_count, CHECKS_PER_RUN + 1)
        self.assertEqual(result.latest_update.version, "2.5.1")
        self.assertEqual(result.latest_report, "report from an earlier run")
        self.assertEqual(classifications, 0)
        self.assertEqual(reports, 0)
        self.assertEqual(elapsed, timedelta(seconds=1))

    async def test_stop_takes_precedence_at_continue_as_new_boundary(self) -> None:
        # Test loop ordering, not Signal/timer delivery (covered on a local server).
        watch = RecurringAIWatchWorkflow()

        async def check() -> None:
            watch._check_count += 1
            if watch._check_count == CHECKS_PER_RUN:
                watch.stop_watch()

        async def wait(_interval: timedelta) -> None:
            pass

        with (
            patch.object(watch, "_check", check),
            patch.object(watch, "_wait", wait),
            patch("workflow.workflow.continue_as_new") as continue_as_new,
        ):
            result = await watch.run(WatchInput())
        self.assertEqual(result.state, "STOPPED")
        self.assertEqual(result.check_count, CHECKS_PER_RUN)
        continue_as_new.assert_not_called()

    async def test_classification_failure_recovers_pending_update_next_check(self) -> None:
        classification_attempts = 0

        def classify_then_recover(*args: object) -> MockClassification:
            nonlocal classification_attempts
            classification_attempts += 1
            if classification_attempts == 1:
                raise RuntimeError("temporary classifier outage")
            return MockClassification(False, "recovered")

        with TestWorkflowEnvironment(build_registry()) as environment:
            environment.on_activity(CLASSIFY_ACTIVITY, fn=classify_then_recover)
            execution = await environment.client.start_workflow(
                WATCH_WORKFLOW,
                WatchInput(interval=timedelta(seconds=1), max_checks=2),
                workflow_id="classification-recovery",
                task_list="test-task-list",
            )
            result = environment.get_workflow_result(
                WatchStatus, execution.workflow_id, execution.run_id
            )

        self.assertEqual(result.check_count, 2)
        self.assertEqual(classification_attempts, 2)

    async def test_non_retryable_classification_failure_terminates_watch(self) -> None:
        classification_attempts = 0

        def reject_configuration(*args: object) -> MockClassification:
            nonlocal classification_attempts
            classification_attempts += 1
            raise ActivityFailure("LiveAuthenticationError")

        with TestWorkflowEnvironment(build_registry()) as environment:
            environment.on_activity(
                CLASSIFY_ACTIVITY, fn=reject_configuration
            )
            execution = await environment.client.start_workflow(
                WATCH_WORKFLOW,
                WatchInput(interval=timedelta(seconds=1), max_checks=2),
                workflow_id="fatal-classification-error",
                task_list="test-task-list",
            )

            with self.assertRaisesRegex(ActivityFailure, "LiveAuthenticationError"):
                environment.get_workflow_result(
                    WatchStatus, execution.workflow_id, execution.run_id
                )

        self.assertEqual(classification_attempts, 1)

    async def test_live_path_classifies_before_generating_report(self) -> None:
        calls: list[str] = []

        def classify_for_live(*args: object) -> MockClassification:
            calls.append("jev")
            return MockClassification(True, "needs report")

        async def generate_report(*args: object) -> str:
            calls.append("gemini")
            return "live report"

        with TestWorkflowEnvironment(build_registry()) as environment:
            environment.on_activity(CLASSIFY_ACTIVITY, fn=classify_for_live)
            with patch("live.generate_live_report", generate_report):
                execution = await environment.client.start_workflow(
                    WATCH_WORKFLOW,
                    WatchInput(
                        max_checks=1,
                        mode="live",
                        model_name="gemini-3.5-flash-lite",
                    ),
                    workflow_id="live-order",
                    task_list="test-task-list",
                )
                result = environment.get_workflow_result(
                    WatchStatus, execution.workflow_id, execution.run_id
                )

        self.assertEqual(calls, ["jev", "gemini"])
        self.assertEqual(result.latest_report, "live report")

    async def test_report_failure_retries_release_from_classification(self) -> None:
        classification_attempts = 0
        report_attempts = 0

        def classify(*args: object) -> MockClassification:
            nonlocal classification_attempts
            classification_attempts += 1
            return MockClassification(True, "needs report")

        def report_then_recover(*args: object) -> str:
            nonlocal report_attempts
            report_attempts += 1
            if report_attempts == 1:
                raise RuntimeError("temporary report outage")
            return "recovered report"

        with TestWorkflowEnvironment(build_registry()) as environment:
            environment.on_activity(CLASSIFY_ACTIVITY, fn=classify)
            environment.on_activity(MOCK_REPORT_ACTIVITY, fn=report_then_recover)
            execution = await environment.client.start_workflow(
                WATCH_WORKFLOW,
                WatchInput(
                    interval=timedelta(seconds=1),
                    max_checks=2,
                    next_update_index=1,
                ),
                workflow_id="report-recovery",
                task_list="test-task-list",
            )
            result = environment.get_workflow_result(
                WatchStatus, execution.workflow_id, execution.run_id
            )

        self.assertEqual(result.check_count, 2)
        self.assertEqual(classification_attempts, 2)
        self.assertEqual(report_attempts, 2)
        self.assertEqual(result.latest_report, "recovered report")

    async def test_immediate_check_and_status_query(self) -> None:
        with TestWorkflowEnvironment(build_registry()) as environment:
            execution = await environment.client.start_workflow(
                WATCH_WORKFLOW,
                WatchInput(max_checks=1),
                workflow_id="immediate-check",
                task_list="test-task-list",
            )
            status = await environment.client.query_workflow(
                execution.workflow_id,
                execution.run_id,
                WATCH_STATUS_QUERY,
                result_type=WatchStatus,
            )

        self.assertEqual(status.check_count, 1)
        self.assertEqual(status.latest_update.version, "2.4.0")
        self.assertIsNone(status.latest_report)
        self.assertEqual(status.state, "COMPLETED")

    async def test_timer_starts_the_next_check(self) -> None:
        with TestWorkflowEnvironment(build_registry()) as environment:
            started_at = environment.now()
            execution = await environment.client.start_workflow(
                WATCH_WORKFLOW,
                WatchInput(interval=timedelta(seconds=15), max_checks=2),
                workflow_id="timer-check",
                task_list="test-task-list",
            )
            result = environment.get_workflow_result(
                WatchStatus, execution.workflow_id, execution.run_id
            )

            elapsed = environment.now() - started_at

        self.assertEqual(result.check_count, 2)
        self.assertEqual(result.latest_update.version, "2.5.0")
        self.assertIn("outbound HTTP retries changed", result.latest_report)
        self.assertEqual(elapsed, timedelta(seconds=15))

    async def test_check_now_signal_skips_the_wait(self) -> None:
        with TestWorkflowEnvironment(build_registry()) as environment:
            started_at = environment.now()
            execution = await environment.client.signal_with_start_workflow(
                WATCH_WORKFLOW,
                CHECK_NOW_SIGNAL,
                [],
                WatchInput(interval=timedelta(seconds=15), max_checks=2),
                workflow_id="check-now",
                task_list="test-task-list",
            )
            result = environment.get_workflow_result(
                WatchStatus, execution.workflow_id, execution.run_id
            )

            elapsed = environment.now() - started_at

        self.assertEqual(result.check_count, 2)
        self.assertEqual(elapsed, timedelta(0))

    async def test_stop_signal_finishes_the_current_initial_check(self) -> None:
        with TestWorkflowEnvironment(build_registry()) as environment:
            execution = await environment.client.signal_with_start_workflow(
                WATCH_WORKFLOW,
                STOP_WATCH_SIGNAL,
                [],
                WatchInput(),
                workflow_id="graceful-stop",
                task_list="test-task-list",
            )
            result = environment.get_workflow_result(
                WatchStatus, execution.workflow_id, execution.run_id
            )

        self.assertEqual(result.check_count, 1)
        self.assertEqual(result.latest_update.version, "2.4.0")
        self.assertEqual(result.state, "STOPPED")

    async def test_exhausted_updates_do_not_repeat_mock_ai_work(self) -> None:
        classifications = 0
        reports = 0

        def count_classification(*args: object) -> MockClassification:
            nonlocal classifications
            classifications += 1
            return MockClassification(False, "mocked")

        def count_report(*args: object) -> str:
            nonlocal reports
            reports += 1
            return "unexpected"

        with TestWorkflowEnvironment(build_registry()) as environment:
            environment.on_activity(CLASSIFY_ACTIVITY, fn=count_classification)
            environment.on_activity(MOCK_REPORT_ACTIVITY, fn=count_report)
            execution = await environment.client.start_workflow(
                WATCH_WORKFLOW,
                WatchInput(interval=timedelta(seconds=1), max_checks=5),
                workflow_id="exhausted-updates",
                task_list="test-task-list",
            )
            result = environment.get_workflow_result(
                WatchStatus, execution.workflow_id, execution.run_id
            )

        self.assertEqual(result.check_count, 5)
        self.assertEqual(result.latest_update.version, "2.5.1")
        self.assertEqual(classifications, 3)
        self.assertEqual(reports, 0)


if __name__ == "__main__":
    unittest.main()
