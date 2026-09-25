from __future__ import annotations

import asyncio
import tempfile
import unittest
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path
from unittest.mock import AsyncMock, patch

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


if __name__ == "__main__":
    unittest.main()
