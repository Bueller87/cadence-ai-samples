import io
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cadence.api.v1 import workflow_pb2
from cadence.contrib.pydantic import PydanticDataConverter
from cadence.error import WorkflowExecutionAlreadyStartedError
from cadence.testing import TestWorkflowEnvironment
from grpc import StatusCode

import main
from workflow import (
    ACTIVITY_OPTIONS,
    ASSESS_ACTIVITY,
    FOLLOW_UP_ACTIVITY,
    WORKFLOW,
    Assessment,
    InvalidRequestError,
    RecipeInput,
    RecipeResult,
    assess,
    build_registry,
    follow_up,
)


def environment() -> TestWorkflowEnvironment:
    return TestWorkflowEnvironment(build_registry(), data_converter=PydanticDataConverter())


def fake_client(**start_workflow) -> SimpleNamespace:
    return SimpleNamespace(start_workflow=AsyncMock(**start_workflow), close=AsyncMock())


class WorkflowTests(unittest.IsolatedAsyncioTestCase):
    async def test_follow_up_runs_only_when_assessment_needs_it(self) -> None:
        with environment() as env:
            for workflow_id, text, expected in (
                ("skip", "Routine request.", None),
                ("run", "Investigate this request.",
                 "Follow-up recorded for: Investigate this request."),
            ):
                execution = await env.client.start_workflow(
                    WORKFLOW, RecipeInput(text), workflow_id=workflow_id,
                    task_list="test-task-list",
                )
                result = env.get_workflow_result(
                    RecipeResult, execution.workflow_id, execution.run_id
                )
                self.assertEqual(result.follow_up, expected)
                self.assertEqual(result.assessment.needs_follow_up, expected is not None)

    async def test_activity_failure_fails_the_workflow(self) -> None:
        def unavailable(text: str) -> str:
            raise RuntimeError("downstream unavailable")

        with environment() as env:
            env.on_activity(FOLLOW_UP_ACTIVITY, fn=unavailable)
            execution = await env.client.start_workflow(
                WORKFLOW, RecipeInput("Investigate this."), workflow_id="failure",
                task_list="test-task-list",
            )
            self.assertIsNotNone(env.get_workflow_error(execution.workflow_id))


class ActivityTests(unittest.TestCase):
    def test_assess_and_follow_up_are_synthetic_and_deterministic(self) -> None:
        self.assertEqual(assess("Please INVESTIGATE."), Assessment(True, "keyword match"))
        self.assertEqual(assess("Routine."), Assessment(False, "no follow-up keyword"))
        self.assertEqual(follow_up("x"), "Follow-up recorded for: x")

    def test_empty_input_is_not_retried(self) -> None:
        with self.assertRaises(InvalidRequestError):
            assess("  ")
        policy = ACTIVITY_OPTIONS["retry_policy"]
        self.assertIn(InvalidRequestError.__name__, policy["non_retryable_error_reasons"])
        self.assertEqual(policy["maximum_attempts"], 3)

    def test_activity_names_are_recipe_scoped(self) -> None:
        self.assertEqual(ASSESS_ACTIVITY, "__RECIPE_SLUG__.assess")
        self.assertEqual(FOLLOW_UP_ACTIVITY, "__RECIPE_SLUG__.follow-up")


class CommandTests(unittest.IsolatedAsyncioTestCase):
    def test_worker_and_start_share_defaults(self) -> None:
        worker = main.parser().parse_args(["worker"])
        start = main.parser().parse_args(["start"])
        for args in (worker, start):
            self.assertEqual(args.domain, "cadence-ai-samples")
            self.assertEqual(args.task_list, "__RECIPE_SLUG__")
            self.assertEqual(args.workflow_id, "__RECIPE_SLUG__-demo")
        self.assertEqual(start.text, main.DEFAULT_TEXT)

    def test_overrides_and_invalid_commands(self) -> None:
        args = main.parser().parse_args(
            ["--task-list", "tl", "--workflow-id", "wf", "start", "--text", "hi"]
        )
        self.assertEqual((args.task_list, args.workflow_id, args.text), ("tl", "wf", "hi"))
        for argv in ([], ["stop"], ["worker", "--text", "hi"]):
            with self.subTest(argv=argv), self.assertRaises(SystemExit), \
                    patch("sys.stderr"):
                main.parser().parse_args(argv)

    async def test_start_allows_a_new_run_after_the_demo_run_closes(self) -> None:
        cadence_client = fake_client(
            return_value=SimpleNamespace(workflow_id="__RECIPE_SLUG__-demo", run_id="r1")
        )
        with patch("main.client", return_value=cadence_client), patch("builtins.print"):
            await main.start(main.parser().parse_args(["start"]))
        options = cadence_client.start_workflow.call_args.kwargs
        self.assertEqual(options["workflow_id"], "__RECIPE_SLUG__-demo")
        self.assertEqual(options["task_list"], "__RECIPE_SLUG__")
        self.assertEqual(options["workflow_id_reuse_policy"],
                         workflow_pb2.WORKFLOW_ID_REUSE_POLICY_ALLOW_DUPLICATE)
        cadence_client.close.assert_awaited_once()

    async def test_start_reports_a_duplicate_while_a_run_is_open(self) -> None:
        duplicate = WorkflowExecutionAlreadyStartedError(
            "already started", StatusCode.ALREADY_EXISTS, "request", "open-run"
        )
        cadence_client = fake_client(side_effect=duplicate)
        with patch("main.client", return_value=cadence_client), \
                patch("sys.stderr", new_callable=io.StringIO) as stderr, \
                self.assertRaises(SystemExit) as exit_info:
            await main.main(["start"])
        self.assertEqual(exit_info.exception.code, 2)
        self.assertIn("workflow-id=__RECIPE_SLUG__-demo already has an open Run",
                      stderr.getvalue())
        cadence_client.close.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
