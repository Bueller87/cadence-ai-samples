import tempfile
import unittest
from collections import Counter
from pathlib import Path
from unittest.mock import patch

from cadence.contrib.pydantic import PydanticDataConverter
from cadence.testing import TestActivityEnvironment, TestWorkflowEnvironment

from activities import (
    MAX_EXPANSION_TOPIC_LENGTH,
    MAX_FEEDBACK_LENGTH,
    MAX_QUESTIONS,
    MAX_TOPIC_LENGTH,
    MOCK_EDIT_ACTIVITY,
    MOCK_PLAN_ACTIVITY,
    MOCK_SPECIALIST_ACTIVITY,
    MOCK_SYNTHESIS_ACTIVITY,
    PUBLISH_ACTIVITY,
    PublicationReceipt,
    PublicationRequest,
    mock_edit,
    mock_plan,
    mock_specialist,
    mock_synthesis,
    publish_report,
    read_research_source,
    search_research_sources,
)
from workflow import (
    APPROVE_SIGNAL,
    EXPAND_SIGNAL,
    MAX_EDIT_ROUNDS,
    REVISE_SIGNAL,
    STATUS_QUERY,
    WORKFLOW,
    AutoResearchWorkflow,
    ExpandReportRequest,
    RecipeInput,
    RecipeResult,
    ReviewAction,
    ReviseReportRequest,
    WorkflowStatus,
    _parse_questions,
    build_registry,
)


class ActivityTests(unittest.IsolatedAsyncioTestCase):
    async def test_mock_plan_and_sources_are_deterministic(self) -> None:
        with TestActivityEnvironment() as environment:
            plan = await environment.execute_activity(
                mock_plan, "  Durable AI systems  "
            )
            results = await environment.execute_activity(
                search_research_sources, plan.questions[0]
            )
            brief = await environment.execute_activity(
                read_research_source, results.urls[0]
            )
        self.assertEqual(len(plan.questions), MAX_QUESTIONS)
        self.assertEqual(len(results.urls), 3)
        self.assertIn("Synthetic source brief", brief)

    async def test_publication_is_atomic_idempotent_and_portable(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            "os.environ", {"AUTO_RESEARCH_OUTPUT_DIR": directory}
        ):
            request = PublicationRequest("a" * 24, "Durable research", 2, "# Approved")
            with TestActivityEnvironment() as environment:
                first = await environment.execute_activity(publish_report, request)
                second = await environment.execute_activity(publish_report, request)
            files = [path.name for path in Path(directory).iterdir()]
        self.assertEqual(first, second)
        self.assertNotIn(directory, repr(first))
        self.assertEqual(files, [first.filename])

    async def test_source_reader_rejects_non_synthetic_urls(self) -> None:
        with TestActivityEnvironment() as environment:
            with self.assertRaisesRegex(ValueError, "synthetic"):
                await environment.execute_activity(
                    read_research_source, "https://example.com/private"
                )


class WorkflowTests(unittest.IsolatedAsyncioTestCase):
    async def test_mock_revision_expansion_and_approval_publish_once(self) -> None:
        publications: list[PublicationRequest] = []

        def record(request: PublicationRequest) -> PublicationReceipt:
            publications.append(request)
            return PublicationReceipt(
                request.publication_id, "portable-report.md", request.revision, "f" * 64
            )

        with TestWorkflowEnvironment(
            build_registry(), data_converter=PydanticDataConverter()
        ) as environment:
            environment.on_activity(PUBLISH_ACTIVITY, fn=record)
            execution = await environment.client.start_workflow(
                WORKFLOW,
                RecipeInput("Durable AI research"),
                workflow_id="mock-reviewed",
                task_list="test-task-list",
            )
            status = await environment.client.query_workflow(
                execution.workflow_id,
                execution.run_id,
                STATUS_QUERY,
                result_type=WorkflowStatus,
            )
            self.assertEqual((status.state, status.revision), ("WAITING_FOR_REVIEW", 0))
            self.assertEqual(status.question_count, MAX_QUESTIONS)

            await environment.client.signal_workflow(
                execution.workflow_id,
                execution.run_id,
                REVISE_SIGNAL,
                ReviseReportRequest(-1, "stale feedback"),
            )
            await environment.client.signal_workflow(
                execution.workflow_id,
                execution.run_id,
                REVISE_SIGNAL,
                ReviseReportRequest(0, "Shorten the conclusion."),
            )
            await environment.client.signal_workflow(
                execution.workflow_id, execution.run_id, APPROVE_SIGNAL, 0
            )
            status = await environment.client.query_workflow(
                execution.workflow_id,
                execution.run_id,
                STATUS_QUERY,
                result_type=WorkflowStatus,
            )
            self.assertEqual((status.state, status.revision), ("WAITING_FOR_REVIEW", 1))
            self.assertIn("Shorten the conclusion", status.draft)

            await environment.client.signal_workflow(
                execution.workflow_id,
                execution.run_id,
                EXPAND_SIGNAL,
                ExpandReportRequest(1, "Operational recovery"),
            )
            status = await environment.client.query_workflow(
                execution.workflow_id,
                execution.run_id,
                STATUS_QUERY,
                result_type=WorkflowStatus,
            )
            self.assertEqual((status.state, status.revision), ("WAITING_FOR_REVIEW", 2))
            await environment.client.signal_workflow(
                execution.workflow_id, execution.run_id, APPROVE_SIGNAL, 2
            )
            result = environment.get_workflow_result(
                RecipeResult, execution.workflow_id, execution.run_id
            )

        self.assertEqual(result.revision, 2)
        self.assertEqual(len(publications), 1)
        self.assertNotIn("/", result.publication.filename)

    def test_invalid_conflicting_and_over_limit_actions_are_noops(self) -> None:
        instance = AutoResearchWorkflow()
        instance._state = "WAITING_FOR_REVIEW"
        instance._revision = 2
        instance.approve(True)
        instance.revise(ReviseReportRequest(1, "stale"))
        instance.revise(ReviseReportRequest(2, " "))
        self.assertIsNone(instance._review_action)

        instance.expand(ExpandReportRequest(2, "Additional evidence"))
        instance.approve(2)
        self.assertEqual(
            instance._review_action, ReviewAction("expand", "Additional evidence")
        )
        instance._review_action = None
        instance._edit_rounds = MAX_EDIT_ROUNDS
        instance.revise(ReviseReportRequest(2, "one more"))
        instance.expand(ExpandReportRequest(2, "one more"))
        self.assertIsNone(instance._review_action)
        instance.approve(2)
        self.assertEqual(instance._review_action, ReviewAction("approve"))

    def test_early_malformed_and_over_length_signals_are_noops(self) -> None:
        instance = AutoResearchWorkflow()
        instance.revise(ReviseReportRequest(0, "early"))
        instance.expand(ExpandReportRequest(0, "early"))
        self.assertIsNone(instance._review_action)

        instance._state = "WAITING_FOR_REVIEW"
        instance.revise({"expected_revision": 0, "feedback": "wrong type"})  # type: ignore[arg-type]
        instance.expand({"expected_revision": 0, "topic": "wrong type"})  # type: ignore[arg-type]
        instance.revise(ReviseReportRequest(0, object()))  # type: ignore[arg-type]
        instance.expand(ExpandReportRequest(0, object()))  # type: ignore[arg-type]
        instance.revise(
            ReviseReportRequest(0, "x" * (MAX_FEEDBACK_LENGTH + 1))
        )
        instance.expand(
            ExpandReportRequest(0, "x" * (MAX_EXPANSION_TOPIC_LENGTH + 1))
        )
        self.assertIsNone(instance._review_action)

    async def test_invalid_and_duplicate_signals_add_no_activity_side_effects(
        self,
    ) -> None:
        counts: Counter[str] = Counter()

        def counted(name: str, fn):
            def invoke(*args):
                counts[name] += 1
                return fn(*args)

            return invoke

        def record_publication(request: PublicationRequest) -> PublicationReceipt:
            counts[PUBLISH_ACTIVITY] += 1
            return PublicationReceipt(
                request.publication_id,
                "signal-side-effects.md",
                request.revision,
                "a" * 64,
            )

        with TestWorkflowEnvironment(
            build_registry(), data_converter=PydanticDataConverter()
        ) as environment:
            for name, fn in (
                (MOCK_PLAN_ACTIVITY, mock_plan),
                (MOCK_SPECIALIST_ACTIVITY, mock_specialist),
                (MOCK_SYNTHESIS_ACTIVITY, mock_synthesis),
                (MOCK_EDIT_ACTIVITY, mock_edit),
            ):
                environment.on_activity(name, fn=counted(name, fn))
            environment.on_activity(PUBLISH_ACTIVITY, fn=record_publication)
            execution = await environment.client.start_workflow(
                WORKFLOW,
                RecipeInput("Signal side effects"),
                workflow_id="invalid-signal-side-effects",
                task_list="test-task-list",
            )
            await environment.client.signal_workflow(
                execution.workflow_id,
                execution.run_id,
                REVISE_SIGNAL,
                ReviseReportRequest(0, "x" * (MAX_FEEDBACK_LENGTH + 1)),
            )
            await environment.client.signal_workflow(
                execution.workflow_id,
                execution.run_id,
                EXPAND_SIGNAL,
                ExpandReportRequest(
                    0, "x" * (MAX_EXPANSION_TOPIC_LENGTH + 1)
                ),
            )
            status = await environment.client.query_workflow(
                execution.workflow_id,
                execution.run_id,
                STATUS_QUERY,
                result_type=WorkflowStatus,
            )
            self.assertEqual((status.state, status.revision), ("WAITING_FOR_REVIEW", 0))

            request = ReviseReportRequest(0, "One accepted revision.")
            await environment.client.signal_workflow(
                execution.workflow_id, execution.run_id, REVISE_SIGNAL, request
            )
            await environment.client.signal_workflow(
                execution.workflow_id, execution.run_id, REVISE_SIGNAL, request
            )
            status = await environment.client.query_workflow(
                execution.workflow_id,
                execution.run_id,
                STATUS_QUERY,
                result_type=WorkflowStatus,
            )
            self.assertEqual((status.state, status.revision), ("WAITING_FOR_REVIEW", 1))
            await environment.client.signal_workflow(
                execution.workflow_id, execution.run_id, APPROVE_SIGNAL, 1
            )
            environment.get_workflow_result(
                RecipeResult, execution.workflow_id, execution.run_id
            )

        self.assertEqual(counts[MOCK_PLAN_ACTIVITY], 1)
        self.assertEqual(counts[MOCK_SPECIALIST_ACTIVITY], MAX_QUESTIONS)
        self.assertEqual(counts[MOCK_SYNTHESIS_ACTIVITY], 1)
        self.assertEqual(counts[MOCK_EDIT_ACTIVITY], 1)
        self.assertEqual(counts[PUBLISH_ACTIVITY], 1)

    def test_planner_fails_closed_for_missing_output(self) -> None:
        with self.assertRaisesRegex(ValueError, "no bounded"):
            _parse_questions("unstructured prose")

    def test_planner_rejects_more_than_four_unique_questions(self) -> None:
        output = "\n".join(
            f"{index}. Question {index}?" for index in range(1, MAX_QUESTIONS + 2)
        )
        with self.assertRaisesRegex(ValueError, "more than 4 unique"):
            _parse_questions(output)

    def test_planner_rejects_an_over_limit_question_without_truncating(self) -> None:
        output = "1. " + ("x" * (MAX_TOPIC_LENGTH + 1))
        with self.assertRaisesRegex(ValueError, "over-limit"):
            _parse_questions(output)


if __name__ == "__main__":
    unittest.main()
