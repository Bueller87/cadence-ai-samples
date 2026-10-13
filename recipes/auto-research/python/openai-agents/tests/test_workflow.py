import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cadence.contrib.pydantic import PydanticDataConverter
from cadence.testing import TestActivityEnvironment, TestWorkflowEnvironment

from activities import (
    LIVE_AGENT_ACTIVITY,
    MAX_QUESTIONS,
    PUBLISH_ACTIVITY,
    PublicationReceipt,
    PublicationRequest,
    mock_plan,
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
    RecipeInput,
    RecipeResult,
    ReviewAction,
    WorkflowStatus,
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
        self.assertIn("Durable AI systems", plan.questions[0])
        self.assertEqual(len(results.urls), 3)
        self.assertIn("Synthetic source brief", brief)

    async def test_publication_is_atomic_idempotent_and_portable(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            "os.environ", {"AUTO_RESEARCH_OUTPUT_DIR": directory}
        ):
            request = PublicationRequest(
                "a" * 24, "Durable research", 2, "# Approved\n"
            )
            with TestActivityEnvironment() as environment:
                first = await environment.execute_activity(
                    publish_report, request
                )
                second = await environment.execute_activity(
                    publish_report, request
                )
            files = [path.name for path in Path(directory).iterdir()]
        self.assertEqual(first, second)
        self.assertEqual(first.revision, 2)
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

        def record_publication(request: PublicationRequest) -> PublicationReceipt:
            publications.append(request)
            return PublicationReceipt(
                request.publication_id,
                "portable-report.md",
                request.revision,
                "f" * 64,
            )

        with TestWorkflowEnvironment(
            build_registry(), data_converter=PydanticDataConverter()
        ) as environment:
            environment.on_activity(PUBLISH_ACTIVITY, fn=record_publication)
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
                -1,
                "stale feedback",
            )
            await environment.client.signal_workflow(
                execution.workflow_id,
                execution.run_id,
                REVISE_SIGNAL,
                0,
                "Shorten the conclusion.",
            )
            await environment.client.signal_workflow(
                execution.workflow_id,
                execution.run_id,
                APPROVE_SIGNAL,
                0,
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
                1,
                "Operational recovery",
            )
            status = await environment.client.query_workflow(
                execution.workflow_id,
                execution.run_id,
                STATUS_QUERY,
                result_type=WorkflowStatus,
            )
            self.assertEqual((status.state, status.revision), ("WAITING_FOR_REVIEW", 2))
            self.assertIn("Operational recovery", status.draft)

            await environment.client.signal_workflow(
                execution.workflow_id,
                execution.run_id,
                APPROVE_SIGNAL,
                2,
            )
            result = environment.get_workflow_result(
                RecipeResult, execution.workflow_id, execution.run_id
            )

        self.assertEqual(result.outcome, "APPROVED_PUBLISHED")
        self.assertEqual(result.revision, 2)
        self.assertEqual(len(publications), 1)
        self.assertEqual(publications[0].revision, 2)
        self.assertNotIn("/", result.publication.filename)

    async def test_live_mode_uses_full_production_activity_graph(self) -> None:
        roles: list[str] = []
        publications: list[PublicationRequest] = []

        def live_agent(request: object) -> str:
            role = request.role
            roles.append(role)
            if role == "planner":
                return "1. What evidence supports durable orchestration?"
            if role == "specialist":
                return (
                    "### Durable orchestration\n\n"
                    "A bounded summary based on both simulated source briefs."
                )
            if role == "synthesizer":
                return (
                    "# Durable orchestration\n\n"
                    "## Executive summary\n\nSynthetic evidence.\n\n"
                    "## Key findings\n\nBounded finding.\n\n"
                    "## Cross-cutting challenges\n\nVerification.\n\n"
                    "## Outlook\n\nFurther validation."
                )
            raise AssertionError(f"unexpected role {role}")

        def record_publication(request: PublicationRequest) -> PublicationReceipt:
            publications.append(request)
            return PublicationReceipt(
                request.publication_id,
                "live-production-report.md",
                request.revision,
                "e" * 64,
            )

        with TestWorkflowEnvironment(
            build_registry(), data_converter=PydanticDataConverter()
        ) as environment:
            environment.on_activity(LIVE_AGENT_ACTIVITY, fn=live_agent)
            environment.on_activity(PUBLISH_ACTIVITY, fn=record_publication)
            execution = await environment.client.start_workflow(
                WORKFLOW,
                RecipeInput("Durable orchestration", "live"),
                workflow_id="live-production-graph",
                task_list="test-task-list",
            )
            status = await environment.client.query_workflow(
                execution.workflow_id,
                execution.run_id,
                STATUS_QUERY,
                result_type=WorkflowStatus,
            )
            self.assertEqual(status.state, "WAITING_FOR_REVIEW")
            self.assertEqual(status.question_count, 1)
            await environment.client.signal_workflow(
                execution.workflow_id,
                execution.run_id,
                APPROVE_SIGNAL,
                0,
            )
            result = environment.get_workflow_result(
                RecipeResult, execution.workflow_id, execution.run_id
            )

        self.assertEqual(roles, ["planner", "specialist", "synthesizer"])
        self.assertEqual(result.outcome, "APPROVED_PUBLISHED")
        self.assertEqual(len(publications), 1)

    def test_invalid_conflicting_and_over_limit_actions_are_noops(self) -> None:
        instance = AutoResearchWorkflow()
        instance._state = "WAITING_FOR_REVIEW"
        instance._revision = 2
        instance.approve(True)
        instance.revise(1, "stale")
        instance.revise(2, " ")
        self.assertIsNone(instance._review_action)

        instance.expand(2, "Additional evidence")
        instance.approve(2)
        self.assertEqual(
            instance._review_action,
            ReviewAction("expand", "Additional evidence"),
        )

        instance._review_action = None
        instance._edit_rounds = MAX_EDIT_ROUNDS
        instance.revise(2, "one more")
        instance.expand(2, "one more")
        self.assertIsNone(instance._review_action)
        instance.approve(2)
        self.assertEqual(instance._review_action, ReviewAction("approve"))


if __name__ == "__main__":
    unittest.main()
