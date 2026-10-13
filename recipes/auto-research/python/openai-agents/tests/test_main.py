import asyncio
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
    def test_worker_and_start_require_explicit_supported_modes(self) -> None:
        worker = main.parser().parse_args(["worker", "--mode", "mock"])
        start = main.parser().parse_args(["start", "--mode", "live"])
        for args in (worker, start):
            self.assertEqual(args.domain, "cadence-ai-samples")
            self.assertEqual(args.task_list, "auto-research-openai-agents")
            self.assertEqual(
                args.workflow_id, "auto-research-openai-agents-demo"
            )
        self.assertEqual((worker.mode, start.mode), ("mock", "live"))
        self.assertEqual(start.text, main.DEFAULT_TEXT)
        self.assertEqual(worker.model_id, "llama3.2-local")
        for command in ("status", "approve", "revise", "expand"):
            args = [command]
            if command != "status":
                args.extend(["--expected-revision", "2"])
            if command == "revise":
                args.extend(["--feedback", "shorten it"])
            if command == "expand":
                args.extend(["--topic", "operations"])
            self.assertEqual(main.parser().parse_args(args).command, command)

    def test_literal_arguments_accept_overrides_and_reject_classifier_flags(
        self,
    ) -> None:
        args = main.parser().parse_args(
            [
                "--task-list",
                "custom",
                "start",
                "--mode",
                "live",
                "--text",
                "hello",
            ]
        )
        self.assertEqual(
            (args.task_list, args.mode, args.text),
            ("custom", "live", "hello"),
        )
        for argv in (
            ["worker"],
            ["start"],
            ["worker", "--mode", "other"],
            ["start", "--mode", "live", "--model-id", "openai-nano"],
            ["start", "--mode", "live", "--confirm-live"],
            ["--classifier-id", "anything", "worker", "--mode", "mock"],
        ):
            with self.subTest(argv=argv), patch("sys.stderr"), self.assertRaises(
                SystemExit
            ):
                main.parser().parse_args(argv)

    async def test_non_worker_commands_do_not_load_catalog(self) -> None:
        with (
            patch(
                "main.load_model",
                side_effect=AssertionError("mock loaded models.yaml"),
            ) as load,
            patch("main.run_worker", new=AsyncMock()) as worker,
            patch("main.start", new=AsyncMock()) as start,
            patch("main.status", new=AsyncMock()) as status,
            patch("main.review", new=AsyncMock()) as review,
        ):
            await main.main(["worker", "--mode", "mock"])
            await main.main(["start", "--mode", "mock"])
            await main.main(["start", "--mode", "live"])
            await main.main(["status"])
            await main.main(
                ["approve", "--expected-revision", "0"]
            )
            await main.main(
                [
                    "revise",
                    "--expected-revision",
                    "0",
                    "--feedback",
                    "shorten",
                ]
            )
            await main.main(
                [
                    "expand",
                    "--expected-revision",
                    "1",
                    "--topic",
                    "operations",
                ]
            )
        load.assert_not_called()
        worker.assert_awaited_once_with(unittest.mock.ANY)
        self.assertEqual(start.await_count, 2)
        status.assert_awaited_once()
        self.assertEqual(review.await_count, 3)

    async def test_live_worker_requires_confirmation_before_catalog_load(self) -> None:
        args = main.parser().parse_args(["worker", "--mode", "live"])
        with (
            patch("main.load_model", side_effect=AssertionError("catalog loaded")) as load,
            self.assertRaisesRegex(ValueError, "--confirm-live"),
        ):
            await main.run_worker(args)
        load.assert_not_called()

    async def test_live_worker_owns_catalog_and_provider_configuration(self) -> None:
        args = main.parser().parse_args(
            [
                "worker", "--mode", "live", "--confirm-live",
                "--model-id", "openai-nano",
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

    async def test_start_allows_closed_run_reuse(self) -> None:
        cadence_client = fake_client(
            return_value=SimpleNamespace(
                workflow_id="auto-research-openai-agents-demo",
                run_id="run-1",
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
        options = cadence_client.start_workflow.call_args.kwargs
        self.assertEqual(
            options["workflow_id_reuse_policy"],
            workflow_pb2.WORKFLOW_ID_REUSE_POLICY_ALLOW_DUPLICATE,
        )
        cadence_client.close.assert_awaited_once()

    async def test_review_commands_send_typed_positional_arguments(self) -> None:
        cases = (
            (
                ["approve", "--expected-revision", "2"],
                ("approve_report", 2),
            ),
            (
                [
                    "revise",
                    "--expected-revision",
                    "2",
                    "--feedback",
                    "shorten",
                ],
                ("revise_report", 2, "shorten"),
            ),
            (
                [
                    "expand",
                    "--expected-revision",
                    "3",
                    "--topic",
                    "operations",
                ],
                ("expand_report", 3, "operations"),
            ),
        )
        for argv, expected in cases:
            with self.subTest(command=argv[0]):
                cadence_client = fake_client()
                args = main.parser().parse_args(argv)
                with patch("main.client", return_value=cadence_client), patch(
                    "builtins.print"
                ):
                    await main.review(args)
                call = cadence_client.signal_workflow.await_args.args
                self.assertEqual(call[:2], (args.workflow_id, ""))
                self.assertEqual(call[2:], expected)

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
            await main.main(["start", "--mode", "mock"])
        self.assertEqual(exit_info.exception.code, 2)
        self.assertIn("already has an open Run", stderr.getvalue())
        cadence_client.close.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
