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
        query_workflow=AsyncMock(),
        signal_workflow=AsyncMock(),
        close=AsyncMock(),
    )


class CommandTests(unittest.IsolatedAsyncioTestCase):
    def test_cli_contract_and_defaults(self) -> None:
        worker = main.parser().parse_args(["worker", "--mode", "mock"])
        start = main.parser().parse_args(["start", "--mode", "live"])
        self.assertEqual(worker.task_list, "auto-research-google-adk")
        self.assertEqual(start.workflow_id, "auto-research-google-adk-demo")
        self.assertEqual(start.text, main.DEFAULT_TEXT)
        self.assertEqual(worker.model_id, "llama3.2-local")
        for command in ("status", "approve", "revise", "expand"):
            argv = [command]
            if command != "status":
                argv += ["--expected-revision", "2"]
            if command == "revise":
                argv += ["--feedback", "shorten it"]
            if command == "expand":
                argv += ["--topic", "operations"]
            self.assertEqual(main.parser().parse_args(argv).command, command)

    async def test_non_worker_commands_do_not_load_catalog(self) -> None:
        with (
            patch("main.load_model", side_effect=AssertionError("loaded catalog")) as load,
            patch("main.run_worker", new=AsyncMock()),
            patch("main.start", new=AsyncMock()),
            patch("main.status", new=AsyncMock()),
            patch("main.review", new=AsyncMock()),
        ):
            await main.main(["start", "--mode", "mock"])
            await main.main(["start", "--mode", "live"])
            await main.main(["status"])
            await main.main(["approve", "--expected-revision", "0"])
        load.assert_not_called()

    async def test_live_worker_requires_confirmation_before_catalog(self) -> None:
        args = main.parser().parse_args(["worker", "--mode", "live"])
        with patch("main.load_model") as load, self.assertRaisesRegex(
            ValueError, "--confirm-live"
        ):
            await main.run_worker(args)
        load.assert_not_called()

    async def test_live_worker_owns_catalog_and_provider_configuration(self) -> None:
        args = main.parser().parse_args(
            ["worker", "--mode", "live", "--confirm-live", "--model-id", "openai-nano"]
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

    async def test_start_and_typed_review_payloads(self) -> None:
        cadence_client = fake_client(
            return_value=SimpleNamespace(
                workflow_id="auto-research-google-adk-demo", run_id="run-1"
            )
        )
        args = main.parser().parse_args(["start", "--mode", "live"])
        with patch("main.client", return_value=cadence_client), patch("builtins.print"):
            await main.start(args)
        recipe_input = cadence_client.start_workflow.call_args.args[1]
        self.assertEqual(vars(recipe_input), {"text": main.DEFAULT_TEXT, "mode": "live"})
        self.assertEqual(
            cadence_client.start_workflow.call_args.kwargs["workflow_id_reuse_policy"],
            workflow_pb2.WORKFLOW_ID_REUSE_POLICY_ALLOW_DUPLICATE,
        )

        for argv, expected in (
            (["approve", "--expected-revision", "2"], ("approve_report", 2)),
            (
                ["revise", "--expected-revision", "2", "--feedback", "shorten"],
                ("revise_report", main.ReviseReportRequest(2, "shorten")),
            ),
            (
                ["expand", "--expected-revision", "3", "--topic", "operations"],
                ("expand_report", main.ExpandReportRequest(3, "operations")),
            ),
        ):
            client = fake_client()
            with patch("main.client", return_value=client), patch("builtins.print"):
                await main.review(main.parser().parse_args(argv))
            self.assertEqual(client.signal_workflow.await_args.args[2:], expected)

    async def test_duplicate_open_run_is_a_command_error(self) -> None:
        duplicate = WorkflowExecutionAlreadyStartedError(
            "already started", StatusCode.ALREADY_EXISTS, "request", "open-run"
        )
        cadence_client = fake_client(side_effect=duplicate)
        with (
            patch("main.client", return_value=cadence_client),
            patch("sys.stderr", new_callable=io.StringIO) as stderr,
            self.assertRaises(SystemExit),
        ):
            await main.main(["start", "--mode", "mock"])
        self.assertIn("already has an open Run", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
