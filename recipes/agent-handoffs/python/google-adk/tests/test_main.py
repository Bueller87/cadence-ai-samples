import io
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

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
    def test_worker_and_start_use_google_adk_defaults(self) -> None:
        worker = main.parser().parse_args(["worker", "--mode", "mock"])
        start = main.parser().parse_args(["start", "--mode", "live"])
        for args in (worker, start):
            self.assertEqual(args.domain, "cadence-ai-samples")
            self.assertEqual(args.task_list, "agent-handoffs-google-adk")
            self.assertEqual(
                args.workflow_id, "agent-handoffs-google-adk-demo"
            )
        self.assertEqual(start.text, main.DEFAULT_TEXT)

    def test_start_has_no_worker_configuration_flags(self) -> None:
        args = main.parser().parse_args(
            ["--task-list", "custom", "start", "--mode", "live", "--text", "hello"]
        )
        self.assertEqual((args.task_list, args.text), ("custom", "hello"))
        for argv in (
            ["worker"],
            ["start"],
            ["start", "--mode", "live", "--model-id", "openai-nano"],
            ["start", "--mode", "live", "--confirm-live"],
            ["--classifier-id", "anything", "worker", "--mode", "mock"],
        ):
            with self.subTest(argv=argv), patch("sys.stderr"), self.assertRaises(
                SystemExit
            ):
                main.parser().parse_args(argv)

    async def test_mock_and_start_paths_do_not_load_catalog(self) -> None:
        with (
            patch(
                "main.load_model",
                side_effect=AssertionError("catalog loaded"),
            ) as load,
            patch("main.run_worker", new=AsyncMock()) as worker,
            patch("main.start", new=AsyncMock()) as start,
        ):
            await main.main(["worker", "--mode", "mock"])
            await main.main(["start", "--mode", "mock"])
            await main.main(["start", "--mode", "live"])
        load.assert_not_called()
        worker.assert_awaited_once()
        self.assertEqual(start.await_count, 2)

    async def test_live_worker_requires_confirmation_before_catalog(self) -> None:
        args = main.parser().parse_args(["worker", "--mode", "live"])
        with (
            patch("main.load_model", side_effect=AssertionError("catalog loaded")) as load,
            self.assertRaisesRegex(ValueError, "--confirm-live"),
        ):
            await main.run_worker(args)
        load.assert_not_called()

    async def test_live_worker_owns_model_configuration(self) -> None:
        args = main.parser().parse_args(
            [
                "worker",
                "--mode",
                "live",
                "--confirm-live",
                "--model-id",
                "openai-nano",
            ]
        )
        selection = SimpleNamespace(id="openai-nano")
        event = MagicMock()
        event.wait = AsyncMock()
        with (
            patch("main.load_model", return_value=selection) as load,
            patch("live.live_model_activities", return_value=object()) as activities,
            patch("main.build_registry", return_value=MagicMock()),
            patch("main.client"),
            patch("main.Worker", return_value=MagicMock()),
            patch("main.asyncio.Event", return_value=event),
        ):
            await main.run_worker(args)
        load.assert_called_once_with(args.catalog_dir, "openai-nano")
        activities.assert_called_once_with(selection, unittest.mock.ANY)
        event.wait.assert_awaited_once()

    async def test_start_uses_typed_input_and_allow_duplicate(self) -> None:
        cadence_client = fake_client(
            return_value=SimpleNamespace(
                workflow_id="agent-handoffs-google-adk-demo", run_id="run-1"
            )
        )
        args = main.parser().parse_args(["start", "--mode", "live"])
        with patch("main.client", return_value=cadence_client), patch(
            "builtins.print"
        ):
            await main.start(args)
        recipe_input = cadence_client.start_workflow.call_args.args[1]
        self.assertEqual(
            vars(recipe_input), {"text": main.DEFAULT_TEXT, "mode": "live"}
        )
        self.assertEqual(
            cadence_client.start_workflow.call_args.kwargs[
                "workflow_id_reuse_policy"
            ],
            workflow_pb2.WORKFLOW_ID_REUSE_POLICY_ALLOW_DUPLICATE,
        )

    async def test_duplicate_open_run_is_a_command_error(self) -> None:
        duplicate = WorkflowExecutionAlreadyStartedError(
            "already started", StatusCode.ALREADY_EXISTS, "request", "open-run"
        )
        cadence_client = fake_client(side_effect=duplicate)
        with (
            patch("main.client", return_value=cadence_client),
            patch("sys.stderr", new_callable=io.StringIO) as stderr,
            self.assertRaises(SystemExit) as exit_info,
        ):
            await main.main(["start", "--mode", "mock"])
        self.assertEqual(exit_info.exception.code, 2)
        self.assertIn("already has an open Run", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
