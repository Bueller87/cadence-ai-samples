"""Offline checks for the live retry-policy exercise starter."""

import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import workflow

RECIPE = Path(__file__).resolve().parents[1]
EXERCISE = RECIPE / "exercises" / "retry_policy_live"


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise AssertionError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RetryPolicyExerciseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.options = load("exercise_options_under_test", EXERCISE / "exercise_options.py")
        cls.runner = load(
            "retry_policy_live_runner_under_test", EXERCISE / "run_exercise.py"
        )

    def test_starter_omits_retry_policy_and_keeps_scan_timeouts(self) -> None:
        self.assertNotIn("retry_policy", self.options.SCAN_ACTIVITY)
        for key in ("schedule_to_close_timeout", "heartbeat_timeout"):
            self.assertEqual(
                self.options.SCAN_ACTIVITY[key], workflow.SCAN_ACTIVITY[key]
            )

    def test_unknown_model_exits_with_setup_failed(self) -> None:
        completed = subprocess.run(
            [
                sys.executable,
                str(EXERCISE / "run_exercise.py"),
                "--model-id",
                "no-such-model",
            ],
            cwd=RECIPE,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        self.assertEqual(completed.returncode, 2, completed.stderr)
        self.assertIn("SETUP FAILED", completed.stdout)

    def test_retried_invalid_plan_still_counts_as_model_use(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            spend = [
                {"call": "plan_next", "input_tokens": 10, "output_tokens": 2},
                {"call": "plan_next", "input_tokens": 11, "output_tokens": 3},
            ]
            decisions = [
                {
                    "step": 1,
                    "attempt": 1,
                    "source": "model",
                    "action": {"tool": "get_logs_by_trace_id"},
                }
            ]
            errors = [
                {
                    "step": 1,
                    "attempt": 0,
                    "error": "ModelDecisionError: invalid plan",
                }
            ]
            (state / "model-spend.jsonl").write_text(
                "".join(json.dumps(row) + "\n" for row in spend)
            )
            (state / "decisions.jsonl").write_text(
                "".join(json.dumps(row) + "\n" for row in decisions)
            )
            (state / "model-errors.jsonl").write_text(
                "".join(json.dumps(row) + "\n" for row in errors)
            )
            receipts = self.runner.model_receipts(state)

        self.assertGreater(receipts["plan_receipts"], receipts["plan_decisions"])
        self.assertTrue(
            self.runner.model_was_used(
                receipts,
                ["get_logs_by_trace_id api-gateway"],
                {"root_cause": "ledger pool exhausted"},
            )
        )
        self.assertFalse(
            self.runner.model_was_used(receipts, [], {"root_cause": "unused"})
        )

    def test_revision_failure_prints_model_call_failed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                self.runner.report_model_failure(
                    state,
                    ValueError("schema rejected the revision"),
                    attempt=0,
                    call="revise_draft",
                )
            recorded = json.loads((state / "model-errors.jsonl").read_text())
        self.assertEqual(recorded["call"], "revise_draft")
        self.assertEqual(recorded["attempt"], 0)
        self.assertIn("ValueError: schema rejected the revision", recorded["error"])
        self.assertIn(
            "MODEL CALL FAILED revise attempt 0: ValueError: schema rejected the revision",
            stdout.getvalue(),
        )


if __name__ == "__main__":
    unittest.main()
