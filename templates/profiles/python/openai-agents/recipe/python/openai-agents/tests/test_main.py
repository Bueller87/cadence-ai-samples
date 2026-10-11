import asyncio
import io
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from cadence.api.v1 import workflow_pb2
from cadence.error import WorkflowExecutionAlreadyStartedError
from grpc import StatusCode

import main


def fake_client(**start_workflow: object) -> SimpleNamespace:
    return SimpleNamespace(
        start_workflow=AsyncMock(**start_workflow),
        close=AsyncMock(),
    )


class CommandTests(unittest.IsolatedAsyncioTestCase):
    def test_worker_and_start_default_to_offline_mock(self) -> None:
        worker = main.parser().parse_args(["worker"])
        start = main.parser().parse_args(["start"])
        for args in (worker, start):
            self.assertEqual(args.mode, "mock")
            self.assertEqual(args.domain, "cadence-ai-samples")
            self.assertEqual(args.task_list, "__RECIPE_SLUG__-openai-agents")
            self.assertEqual(
                args.workflow_id, "__RECIPE_SLUG__-openai-agents-demo"
            )
        self.assertEqual(start.text, main.DEFAULT_TEXT)

    def test_literal_arguments_accept_overrides_and_reject_classifier_flags(
        self,
    ) -> None:
        args = main.parser().parse_args(
            [
                "--task-list",
                "custom",
                "--model-id",
                "openai-nano",
                "start",
                "--mode",
                "live",
                "--text",
                "hello",
            ]
        )
        self.assertEqual(
            (args.task_list, args.model_id, args.mode, args.text),
            ("custom", "openai-nano", "live", "hello"),
        )
        with patch("sys.stderr"), self.assertRaises(SystemExit):
            main.parser().parse_args(["--classifier-id", "anything", "worker"])

    async def test_mock_commands_do_not_load_catalog_or_credentials(self) -> None:
        with (
            patch(
                "main.load_model",
                side_effect=AssertionError("mock loaded models.yaml"),
            ) as load,
            patch("main.run_worker", new=AsyncMock()) as worker,
            patch("main.start", new=AsyncMock()) as start,
        ):
            await main.main(["worker"])
            await main.main(["start"])
        load.assert_not_called()
        self.assertIsNone(worker.call_args.args[1])
        self.assertIsNone(start.call_args.args[1])

    async def test_live_worker_requires_explicit_confirmation(self) -> None:
        args = main.parser().parse_args(["worker", "--mode", "live"])
        with self.assertRaisesRegex(ValueError, "--confirm-live"):
            await main.run_worker(args, SimpleNamespace(id="model"))

    async def test_start_allows_closed_run_reuse(self) -> None:
        cadence_client = fake_client(
            return_value=SimpleNamespace(
                workflow_id="__RECIPE_SLUG__-openai-agents-demo",
                run_id="run-1",
            )
        )
        args = main.parser().parse_args(["start"])
        with patch("main.client", return_value=cadence_client), patch(
            "builtins.print"
        ):
            await main.start(args, None)
        options = cadence_client.start_workflow.call_args.kwargs
        self.assertEqual(
            options["workflow_id_reuse_policy"],
            workflow_pb2.WORKFLOW_ID_REUSE_POLICY_ALLOW_DUPLICATE,
        )
        cadence_client.close.assert_awaited_once()

    async def test_duplicate_open_run_is_a_command_error(self) -> None:
        duplicate = WorkflowExecutionAlreadyStartedError(
            "already started",
            StatusCode.ALREADY_EXISTS,
            "request",
            "open-run",
        )
        cadence_client = fake_client(side_effect=duplicate)
        with (
            patch("main.client", return_value=cadence_client),
            patch("sys.stderr", new_callable=io.StringIO) as stderr,
            self.assertRaises(SystemExit) as exit_info,
        ):
            await main.main(["start"])
        self.assertEqual(exit_info.exception.code, 2)
        self.assertIn("already has an open Run", stderr.getvalue())
        cadence_client.close.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
