from __future__ import annotations

import asyncio
import tempfile
import unittest
import os
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from main import (
    DEFAULT_TASK_LIST,
    DEFAULT_WORKFLOW_ID,
    format_status,
    main,
    parser,
    run_worker,
)
from config import load_selection
from workflow import ReleaseNote, WatchStatus


CATALOGS = {
    "agents.yaml": """agents:
  - id: google-adk
    framework: google-adk
""",
    "models.yaml": """models:
  - id: gemini-flash-lite
    provider: google
    model: gemini-3.5-flash-lite
    endpoint: https://generativelanguage.googleapis.com
""",
    "classifiers.yaml": """classifiers:
  - id: jev-default
    provider: typesafe
    model: jev-latest
    endpoint: https://api.typesafe.ai/v1/systemone
""",
}


class MainTests(unittest.TestCase):
    def test_start_defaults_are_mock_watch_defaults(self) -> None:
        args = parser().parse_args(["start"])

        self.assertEqual(args.task_list, DEFAULT_TASK_LIST)
        self.assertEqual(args.domain, "cadence-ai-samples")
        self.assertEqual(args.workflow_id, DEFAULT_WORKFLOW_ID)
        self.assertEqual(args.interval, 15)
        self.assertEqual(args.mode, "mock")

    def test_live_worker_requires_explicit_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            for filename, contents in CATALOGS.items():
                (directory / filename).write_text(contents, encoding="utf-8")
            args = parser().parse_args(
                ["--catalog-dir", str(directory), "worker", "--mode", "live"]
            )
            selected = load_selection(
                directory,
                agent_id=args.agent_id,
                model_id=args.model_id,
                classifier_id=args.classifier_id,
            )

            with self.assertRaisesRegex(ValueError, "--confirm-live"):
                asyncio.run(run_worker(args, selected))

    def test_rejects_nonpositive_interval(self) -> None:
        with redirect_stderr(StringIO()):
            with self.assertRaises(SystemExit):
                parser().parse_args(["start", "--interval", "0"])

    def test_resolves_catalogs_from_explicit_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            for filename, contents in CATALOGS.items():
                (directory / filename).write_text(contents, encoding="utf-8")

            args = parser().parse_args(["--catalog-dir", str(directory), "worker"])
            selection = load_selection(
                directory,
                agent_id=args.agent_id,
                model_id=args.model_id,
                classifier_id=args.classifier_id,
            )

        self.assertEqual(selection.agent.id, "google-adk")
        self.assertEqual(selection.model.model, "gemini-3.5-flash-lite")
        self.assertEqual(selection.classifier.endpoint, "https://api.typesafe.ai/v1/systemone")

    def test_formats_status_without_model_output_assumptions(self) -> None:
        status = WatchStatus(
            check_count=2,
            latest_update=ReleaseNote("2.5.0", "Retry defaults changed."),
            latest_report="Review retries.",
            state="WAITING",
        )

        self.assertEqual(
            format_status(status),
            "state: WAITING\n"
            "check count: 2\n"
            "latest update: 2.5.0\n"
            "latest report: Review retries.",
        )

    def test_status_and_signals_do_not_load_catalogs(self) -> None:
        with (
            patch("main.load_selection", side_effect=AssertionError("unexpected catalog load")) as load,
            patch("main.status", new=AsyncMock()) as show_status,
            patch("main.signal", new=AsyncMock()) as send_signal,
        ):
            asyncio.run(main(["status"]))
            asyncio.run(main(["check-now"]))
            asyncio.run(main(["stop"]))

        load.assert_not_called()
        show_status.assert_awaited_once()
        self.assertEqual(send_signal.await_count, 2)

    def test_worker_and_start_load_catalogs_and_mock_needs_no_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            for filename, contents in CATALOGS.items():
                (directory / filename).write_text(contents, encoding="utf-8")
            with (
                patch.dict(os.environ, {}, clear=True),
                patch("main.load_selection", wraps=load_selection) as load,
                patch("main.run_worker", new=AsyncMock()) as worker,
                patch("main.start", new=AsyncMock()) as start,
            ):
                asyncio.run(main(["--catalog-dir", str(directory), "worker"]))
                asyncio.run(main(["--catalog-dir", str(directory), "start"]))
            self.assertEqual(load.call_count, 2)
            worker.assert_awaited_once()
            start.assert_awaited_once()
            selection = worker.call_args.args[1]
            with (
                patch.dict(os.environ, {}, clear=True),
                patch("main.Worker", return_value=MagicMock()),
                patch("main.client"),
                patch("main.asyncio.Event") as event,
                patch("live.live_activities", side_effect=AssertionError("mock selected AI")),
            ):
                event.return_value.wait = AsyncMock()
                asyncio.run(run_worker(parser().parse_args(["worker"]), selection))

    def test_catalog_candidate_classifier_is_rejected_before_worker_or_start(self) -> None:
        for command in ("worker", "start"):
            with redirect_stderr(StringIO()) as errors, self.assertRaises(SystemExit):
                asyncio.run(main(["--classifier-id", "kev-local", command]))
            self.assertIn("only jev-default", errors.getvalue())


if __name__ == "__main__":
    unittest.main()
