import io
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cadence.api.v1 import workflow_pb2
from cadence.contrib.pydantic import PydanticDataConverter
from cadence.error import WorkflowExecutionAlreadyStartedError
from cadence.testing import TestWorkflowEnvironment
from grpc import StatusCode

import activities
import main
import story
from durability_manifest import validate_manifest
from raw_agent import RawAgent
from run_compare import cadence_metrics, raw_metrics
from workflow import (
    APPROVAL_SIGNAL,
    GUIDANCE_SIGNAL,
    QUICK_ACTIVITY,
    REVISE_SIGNAL,
    STATUS_QUERY,
    WORKFLOW,
    RecipeInput,
    build_registry,
)


def environment() -> TestWorkflowEnvironment:
    return TestWorkflowEnvironment(
        build_registry(), data_converter=PydanticDataConverter()
    )


def fake_client(**start_workflow: object) -> SimpleNamespace:
    return SimpleNamespace(
        start_workflow=AsyncMock(**start_workflow), close=AsyncMock()
    )


class StoryTests(unittest.TestCase):
    def test_scripted_planner_takes_wrong_then_correct_paths(self) -> None:
        checkout = story.logs_for("checkout", "4bf92f3577b34da6")
        wrong = story.next_hop(checkout, [])
        right = story.next_hop(checkout, [story.GUIDANCE])
        self.assertEqual((wrong, right), ("inventory", "payments"))

        hops = [
            story.logs_for(service, "4bf92f3577b34da6")
            for service in ("checkout", "payments", "ledger")
        ]
        conclusion = story.conclusion(hops, True)
        self.assertEqual(conclusion["root_cause"], story.RIGHT_ROOT_CAUSE)
        self.assertEqual(conclusion["call_path"], story.CORRECT_PATH)

    def test_fixtures_are_synthetic_and_complete(self) -> None:
        trace = json.loads(story.trace_text())
        self.assertTrue(trace["synthetic"])
        self.assertEqual(story.dependency_graph()["depth1"], ["checkout"])
        for service in ("api-gateway", "checkout", "inventory", "payments", "ledger"):
            self.assertTrue(story.logs_for(service, trace["trace_id"])["lines"])


class RawAgentTests(unittest.IsolatedAsyncioTestCase):
    async def test_capable_raw_baseline_handles_retry_and_guidance(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            agent = RawAgent("raw-test", "first", Path(directory))
            wrong = await agent.run_pass("initial", [], inject_429=True)
            self.assertEqual(wrong.conclusion["root_cause"], story.WRONG_ROOT_CAUSE)
            right = await agent.run_pass(
                "manual-rerun", [story.GUIDANCE], inject_429=False
            )
            self.assertEqual(right.conclusion["root_cause"], story.RIGHT_ROOT_CAUSE)
            entries = [
                json.loads(line)
                for line in agent.ledger.read_text().splitlines()
            ]
            gateway = [
                (entry["attempt"], entry["status"])
                for entry in entries
                if entry["tool"] == "get_logs_by_trace_id"
                and entry["service"] == "api-gateway"
            ]
            self.assertEqual(gateway[:2], [(0, 429), (1, 200)])

    def test_raw_and_cadence_share_turn_and_repeat_limits(self) -> None:
        recipe = Path(__file__).resolve().parents[1]
        workflow_source = (recipe / "workflow.py").read_text(encoding="utf-8")
        raw_source = (recipe / "raw_agent.py").read_text(encoding="utf-8")
        self.assertEqual((story.MAX_STEPS, story.REPEAT_LIMIT), (6, 2))
        self.assertIn('"max_steps": 6', workflow_source)
        self.assertIn('"repeat_limit": 2', workflow_source)
        self.assertIn("range(1, story.MAX_STEPS + 1)", raw_source)
        self.assertIn("story.REPEAT_LIMIT", raw_source)
        self.assertNotIn("MAX_TURNS", raw_source)


class WorkflowTests(unittest.IsolatedAsyncioTestCase):
    async def test_guidance_reuses_setup_before_durable_timer_expires(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ, {"INCIDENT_TRIAGE_STATE": directory}
        ):
            workflow_id = "offline-guided"
            with environment() as env:
                execution = await env.client.start_workflow(
                    WORKFLOW,
                    RecipeInput(
                        story.trace_text(),
                        {
                            "approval_window_seconds": 1000,
                            "scan_seconds": 0,
                            "rescan_seconds": 0,
                        },
                    ),
                    workflow_id=workflow_id,
                    task_list="test-task-list",
                )
                status = await env.client.query_workflow(
                    workflow_id,
                    execution.run_id,
                    STATUS_QUERY,
                    result_type=dict,
                )
                self.assertEqual(
                    status["phase"], "AWAITING_GUIDANCE", status
                )
                self.assertEqual(
                    status["conclusion"]["root_cause"], story.WRONG_ROOT_CAUSE
                )
                await env.client.signal_workflow(
                    workflow_id, execution.run_id, REVISE_SIGNAL, story.REVISION
                )
                revised = await env.client.query_workflow(
                    workflow_id,
                    execution.run_id,
                    STATUS_QUERY,
                    result_type=dict,
                )
                self.assertEqual(revised["revision"], 1)
                await env.client.signal_workflow(
                    workflow_id, execution.run_id, GUIDANCE_SIGNAL, story.GUIDANCE
                )
                result = env.get_workflow_result(
                    dict, workflow_id, execution.run_id
                )
            self.assertEqual(result["outcome"], "approval_expired")
            self.assertEqual(
                result["conclusion"]["root_cause"], story.RIGHT_ROOT_CAUSE
            )
            entries = [
                json.loads(line)
                for line in (Path(directory) / "ledger.jsonl").read_text().splitlines()
            ]
            names = [entry["tool"] for entry in entries]
            self.assertEqual(names.count("identify_input_params"), 1)
            self.assertEqual(names.count("store_trace"), 1)
            self.assertEqual(names.count("fetch_dependency_graph"), 1)
            self.assertEqual(names.count("upload_conclusion"), 0)

    async def test_late_approval_does_not_win_after_the_timer(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ, {"INCIDENT_TRIAGE_STATE": directory}
        ):
            workflow_id = "late-approval"
            with environment() as env:
                execution = await env.client.start_workflow(
                    WORKFLOW,
                    RecipeInput(
                        story.trace_text(),
                        {
                            "approval_window_seconds": 5,
                            "scan_seconds": 0,
                            "rescan_seconds": 0,
                        },
                    ),
                    workflow_id=workflow_id,
                    task_list="test-task-list",
                )
                await env.client.signal_workflow(
                    workflow_id, execution.run_id, GUIDANCE_SIGNAL, story.GUIDANCE
                )
                self.assertTrue(env.is_workflow_completed(workflow_id))
                await env.client.signal_workflow(
                    workflow_id, execution.run_id, APPROVAL_SIGNAL, "approve"
                )
                result = env.get_workflow_result(
                    dict, workflow_id, execution.run_id
                )
            self.assertEqual(result["outcome"], "approval_expired")
            database = Path(directory) / "store.db"
            rows = sqlite3.connect(database).execute(
                "select name from sqlite_master where name = 'conclusions'"
            ).fetchall()
            if rows:
                receipts = sqlite3.connect(database).execute(
                    "select receipt from conclusions"
                ).fetchall()
                self.assertEqual(receipts, [])

    async def test_second_run_keeps_its_own_stored_input(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ, {"INCIDENT_TRIAGE_STATE": directory}
        ):
            workflow_id = "reused-workflow"
            limits = {
                "approval_window_seconds": 5,
                "scan_seconds": 0,
                "rescan_seconds": 0,
            }
            with environment() as env:
                first = await env.client.start_workflow(
                    WORKFLOW,
                    RecipeInput(story.trace_text(), limits),
                    workflow_id=workflow_id,
                    task_list="test-task-list",
                )
                await env.client.signal_workflow(
                    workflow_id, first.run_id, GUIDANCE_SIGNAL, story.GUIDANCE
                )
                first_result = env.get_workflow_result(
                    dict, workflow_id, first.run_id
                )
                second = await env.client.start_workflow(
                    WORKFLOW,
                    RecipeInput(story.trace_text(), limits),
                    workflow_id=workflow_id,
                    task_list="test-task-list",
                )
                await env.client.signal_workflow(
                    workflow_id, second.run_id, GUIDANCE_SIGNAL, story.GUIDANCE
                )
                second_result = env.get_workflow_result(
                    dict, workflow_id, second.run_id
                )
            self.assertEqual(first_result["outcome"], "approval_expired")
            self.assertEqual(second_result["outcome"], "approval_expired")
            self.assertNotEqual(first.run_id, second.run_id)
            keys = {
                row[0]
                for row in sqlite3.connect(Path(directory) / "store.db").execute(
                    "select key from traces"
                )
            }
            self.assertEqual(
                keys,
                {
                    f"{workflow_id}/{first.run_id}/input",
                    f"{workflow_id}/{second.run_id}/input",
                },
            )

    def test_retry_policy_is_bounded_and_strict(self) -> None:
        retry = QUICK_ACTIVITY["retry_policy"]
        self.assertEqual(retry["maximum_attempts"], 3)
        self.assertIn(
            activities.NonRetryableHttpError.__name__,
            retry["non_retryable_error_reasons"],
        )


class ActivityTests(unittest.TestCase):
    def test_upload_is_idempotent_after_a_lost_reply(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ, {"INCIDENT_TRIAGE_STATE": directory}
        ), patch(
            "activities.activity.info",
            return_value=SimpleNamespace(
                attempt=0, workflow_id="activity-test"
            ),
        ):
            request = {"key": "activity-test/conclusion", "conclusion": {"ok": True}}
            first = activities.upload_conclusion(request)
            second = activities.upload_conclusion(request)
        self.assertTrue(first["committed"])
        self.assertFalse(second["committed"])
        self.assertEqual(first["receipt"], second["receipt"])

    def test_runs_do_not_share_a_conclusion_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ, {"INCIDENT_TRIAGE_STATE": directory}
        ), patch(
            "activities.activity.info",
            return_value=SimpleNamespace(attempt=0, workflow_id="same-workflow"),
        ):
            first = activities.upload_conclusion(
                {"key": "same-workflow/run-a/conclusion", "conclusion": {"n": 1}}
            )
            second = activities.upload_conclusion(
                {"key": "same-workflow/run-b/conclusion", "conclusion": {"n": 2}}
            )
            replay = activities.upload_conclusion(
                {"key": "same-workflow/run-a/conclusion", "conclusion": {"n": 3}}
            )
            rows = sqlite3.connect(Path(directory) / "store.db").execute(
                "select key, receipt from conclusions order by key"
            ).fetchall()
        self.assertTrue(first["committed"])
        self.assertTrue(second["committed"])
        self.assertNotEqual(first["receipt"], second["receipt"])
        self.assertFalse(replay["committed"])
        self.assertEqual(replay["receipt"], first["receipt"])
        self.assertEqual(
            rows,
            [
                ("same-workflow/run-a/conclusion", first["receipt"]),
                ("same-workflow/run-b/conclusion", second["receipt"]),
            ],
        )


class EvidenceTests(unittest.TestCase):
    def test_manifest_is_symmetric_and_has_no_overlaps(self) -> None:
        counts = validate_manifest()
        self.assertEqual(set(counts), {"raw", "cadence"})
        for side in counts.values():
            self.assertGreater(side["retry"], 0)
            self.assertGreater(side["state"], 0)
            self.assertGreater(side["deduplication"], 0)

    def test_comparison_reports_observed_calls_not_tokens(self) -> None:
        raw = raw_metrics(
            {
                "wall_seconds": 1.0,
                "process_seconds_waiting_for_human": 0.5,
                "root_cause": story.RIGHT_ROOT_CAUSE,
                "call_path": story.CORRECT_PATH,
                "outcome": "conclusion_uploaded",
            },
            [
                {"tool": "plan_next", "pass_id": "initial"},
                {"tool": "upload_conclusion", "pass_id": "restart"},
            ],
        )
        cadence = cadence_metrics(
            {
                "ledger": {"plan_next": 1, "upload_conclusion": 1},
                "final": {
                    "outcome": "conclusion_uploaded",
                    "conclusion": {
                        "root_cause": story.RIGHT_ROOT_CAUSE,
                        "call_path": story.CORRECT_PATH,
                    },
                },
                "zero_worker_seconds": 0.5,
                "history_events_at_zero_workers": 10,
                "upload_attempts": 1,
                "committed_writes": 1,
            },
            1.2,
        )
        for metrics in (raw, cadence):
            self.assertIn("observed_model_calls", metrics)
            self.assertNotIn("tokens", metrics)
            self.assertFalse(metrics["provider_called"])
            self.assertFalse(metrics["tokenizer_run"])

    def test_lost_reply_counts_one_committed_write(self) -> None:
        entries = [
            {"tool": "upload_conclusion", "committed": True, "attempt": 0},
            {"tool": "upload_conclusion", "committed": False, "attempt": 1},
        ]
        raw = raw_metrics(
            {
                "wall_seconds": 1.0,
                "process_seconds_waiting_for_human": 0.2,
                "root_cause": story.RIGHT_ROOT_CAUSE,
                "call_path": story.CORRECT_PATH,
                "outcome": "conclusion_uploaded",
            },
            entries,
        )
        cadence = cadence_metrics(
            {
                "ledger": {"upload_conclusion": 2},
                "upload_attempts": 2,
                "committed_writes": 1,
                "final": {
                    "outcome": "conclusion_uploaded",
                    "conclusion": {
                        "root_cause": story.RIGHT_ROOT_CAUSE,
                        "call_path": story.CORRECT_PATH,
                    },
                },
                "zero_worker_seconds": 0.5,
                "history_events_at_zero_workers": 10,
            },
            1.2,
        )
        for metrics in (raw, cadence):
            self.assertEqual(metrics["upload_attempts"], 2)
            self.assertEqual(metrics["final_writes"], 1)
            self.assertEqual(metrics["duplicate_uploads"], 0)


class CommandTests(unittest.IsolatedAsyncioTestCase):
    def test_worker_and_start_share_defaults(self) -> None:
        worker = main.parser().parse_args(["worker", "--mode", "mock"])
        start = main.parser().parse_args(["start", "--mode", "live"])
        for args in (worker, start):
            self.assertEqual(args.domain, "cadence-ai-samples")
            self.assertEqual(args.task_list, "incident-triage")
            self.assertEqual(args.workflow_id, "incident-triage-demo")
        self.assertEqual(worker.mode, "mock")
        self.assertEqual(start.mode, "live")
        self.assertTrue(json.loads(start.text)["synthetic"])

    def test_overrides_and_invalid_commands(self) -> None:
        args = main.parser().parse_args(
            [
                "--task-list",
                "tl",
                "--workflow-id",
                "wf",
                "start",
                "--mode",
                "mock",
                "--text",
                story.trace_text(),
            ]
        )
        self.assertEqual((args.task_list, args.workflow_id), ("tl", "wf"))
        for argv in (
            [],
            ["stop"],
            ["worker"],
            ["start"],
            ["worker", "--mode", "other"],
            ["worker", "--mode", "mock", "--text", "hi"],
        ):
            with self.subTest(argv=argv), self.assertRaises(SystemExit), patch(
                "sys.stderr"
            ):
                main.parser().parse_args(argv)

    async def test_start_uses_allow_duplicate_after_closed_runs(self) -> None:
        cadence_client = fake_client(
            return_value=SimpleNamespace(
                workflow_id="incident-triage-demo", run_id="r1"
            )
        )
        with patch("main.client", return_value=cadence_client), patch(
            "builtins.print"
        ):
            await main.start(
                main.parser().parse_args(["start", "--mode", "mock"])
            )
        options = cadence_client.start_workflow.call_args.kwargs
        self.assertEqual(options["workflow_id"], "incident-triage-demo")
        self.assertEqual(
            options["workflow_id_reuse_policy"],
            workflow_pb2.WORKFLOW_ID_REUSE_POLICY_ALLOW_DUPLICATE,
        )
        self.assertEqual(
            options["execution_start_to_close_timeout"],
            main.WORKFLOW_EXECUTION_TIMEOUT,
        )
        self.assertGreater(
            options["execution_start_to_close_timeout"], timedelta(days=3)
        )
        cadence_client.close.assert_awaited_once()

    async def test_start_reports_an_open_duplicate(self) -> None:
        duplicate = WorkflowExecutionAlreadyStartedError(
            "already started", StatusCode.ALREADY_EXISTS, "request", "open-run"
        )
        cadence_client = fake_client(side_effect=duplicate)
        with patch("main.client", return_value=cadence_client), patch(
            "sys.stderr", new_callable=io.StringIO
        ) as stderr, self.assertRaises(SystemExit) as exit_info:
            await main.main(["start", "--mode", "mock"])
        self.assertEqual(exit_info.exception.code, 2)
        self.assertIn(
            "workflow-id=incident-triage-demo already has an open Run",
            stderr.getvalue(),
        )


if __name__ == "__main__":
    unittest.main()
