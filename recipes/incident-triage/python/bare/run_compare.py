"""Run the same synthetic incident raw first, then on local Cadence."""

from __future__ import annotations

import argparse
import collections
import json
import os
import shlex
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import story
from durability_manifest import report as durability_report
from run_rehearsal import run_rehearsal


HERE = Path(__file__).resolve().parent
PYTHON = sys.executable
COMPRESSED_HUMAN_WAIT_SECONDS = 0.75
REAL_HUMAN_WAIT_SECONDS = 3 * 24 * 60 * 60
RAW_CRASH_AFTER_SECONDS = 0.3


def command_text(command: list[str]) -> str:
    return shlex.join(["python" if item == PYTHON else item for item in command])


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def counts(entries: list[dict[str, Any]]) -> dict[str, int]:
    result: collections.Counter[str] = collections.Counter()
    for entry in entries:
        key = entry["tool"]
        if key == "get_logs_by_trace_id":
            key += f"({entry['service']})"
        result[key] += 1
    return dict(result)


def repeated_calls(
    ledger: dict[str, int], excluded: set[str] | None = None
) -> int:
    excluded = excluded or set()
    return sum(
        max(0, value - 1)
        for key, value in ledger.items()
        if key not in excluded
    )


def terminate_processes(processes: list[subprocess.Popen[str]]) -> None:
    for process in processes:
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait(timeout=5)


def upload_totals(entries: list[dict[str, Any]]) -> tuple[int, int]:
    attempts = 0
    committed = 0
    for entry in entries:
        if entry.get("tool") != "upload_conclusion":
            continue
        attempts += 1
        if entry.get("committed") is True:
            committed += 1
    return attempts, committed


def run_raw(
    run_dir: Path, run_id: str
) -> tuple[dict[str, Any], list[dict[str, Any]], list[str]]:
    state = run_dir / "raw-state"
    state.mkdir()
    environment = {**os.environ, "INCIDENT_TRIAGE_STATE": str(state)}
    first_command = [
        PYTHON,
        "raw_agent.py",
        "--run-id",
        run_id,
        "--phase",
        "first",
    ]
    commands = [command_text(first_command)]
    output: list[str] = []
    processes: list[subprocess.Popen[str]] = []
    started = time.perf_counter()
    try:
        process = subprocess.Popen(
            first_command,
            cwd=HERE,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            start_new_session=True,
        )
        processes.append(process)
        if process.stdout is None:
            raise RuntimeError("raw process did not expose stdout")
        wait_started: float | None = None
        for line in process.stdout:
            output.append(line)
            print(line, end="")
            if line.startswith("RAW WAITING FOR APPROVAL:"):
                wait_started = time.perf_counter()
                break
        if wait_started is None:
            remainder = process.stdout.read()
            raise RuntimeError(
                "raw process exited before approval wait "
                f"({process.wait()}): {remainder}"
            )

        time.sleep(RAW_CRASH_AFTER_SECONDS)
        os.killpg(process.pid, signal.SIGKILL)
        process.wait()
        first_wait = time.perf_counter() - wait_started
        lost = (
            "RAW LOST WORK: process killed during approval wait; "
            "in-memory state vanished\n"
        )
        print(lost, end="")
        output.append(lost)

        remaining = max(0.0, COMPRESSED_HUMAN_WAIT_SECONDS - first_wait)
        restart_command = [
            PYTHON,
            "raw_agent.py",
            "--run-id",
            run_id,
            "--phase",
            "restart",
            "--approve-at",
            f"{time.time() + remaining:.9f}",
        ]
        commands.append(command_text(restart_command))
        restarted = subprocess.Popen(
            restart_command,
            cwd=HERE,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
        )
        processes.append(restarted)
        try:
            restarted_output, _ = restarted.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            raise RuntimeError("raw restart timed out") from None
        output.append(restarted_output)
        print(restarted_output, end="")
        if restarted.returncode:
            raise RuntimeError(restarted_output)
    finally:
        terminate_processes(processes)
    wall_seconds = time.perf_counter() - started
    line = next(
        item
        for item in restarted_output.splitlines()
        if item.startswith("RAW RESULT ")
    )
    result = json.loads(line.removeprefix("RAW RESULT "))
    result.update(
        {
            "wall_seconds": wall_seconds,
            "process_seconds_waiting_for_human": (
                first_wait + result.pop("wait_seconds")
            ),
        }
    )
    (run_dir / "raw-output.txt").write_text(
        "".join(output), encoding="utf-8"
    )
    ledger = read_jsonl(state / "ledger-raw.jsonl")
    (run_dir / "ledger-raw.jsonl").write_text(
        (state / "ledger-raw.jsonl").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    return result, ledger, commands


def raw_metrics(
    result: dict[str, Any], entries: list[dict[str, Any]]
) -> dict[str, Any]:
    ledger = counts(entries)
    model_entries = [
        entry
        for entry in entries
        if entry["tool"] in ("plan_next", "revise_draft")
    ]
    setup = (
        "identify_input_params",
        "store_trace",
        "fetch_dependency_graph",
    )
    gateway_successes = sum(
        1
        for entry in entries
        if entry["tool"] == "get_logs_by_trace_id"
        and entry.get("service") == "api-gateway"
        and entry.get("status") == 200
    )
    attempts, committed = upload_totals(entries)
    return {
        "wall_seconds": result["wall_seconds"],
        "observed_model_calls": len(model_entries),
        "model_call_source": "plan_next and revise_draft ledger entries",
        "provider_called": False,
        "tokenizer_run": False,
        "repeated_tool_calls": repeated_calls(
            ledger, {"plan_next", "revise_draft"}
        ),
        "completed_setup_steps_redone": sum(
            max(0, ledger.get(key, 0) - 1) for key in setup
        )
        + max(0, gateway_successes - 1),
        "upload_attempts": attempts,
        "duplicate_uploads": max(0, committed - 1),
        "final_writes": committed,
        "process_seconds_waiting_for_human": result[
            "process_seconds_waiting_for_human"
        ],
        "zero_worker_seconds_during_wait": 0.0,
        "final_correct": (
            result["root_cause"] == story.RIGHT_ROOT_CAUSE
            and result["call_path"] == story.CORRECT_PATH
        ),
        "outcome": result["outcome"],
        "ledger": ledger,
    }


def cadence_metrics(
    manifest: dict[str, Any], wall_seconds: float
) -> dict[str, Any]:
    ledger = manifest["ledger"]
    model_calls = ledger.get("plan_next", 0) + ledger.get("revise_draft", 0)
    attempts = manifest["upload_attempts"]
    committed = manifest["committed_writes"]
    final = manifest["final"]["conclusion"]
    return {
        "wall_seconds": wall_seconds,
        "observed_model_calls": model_calls,
        "model_call_source": "plan_next and revise_draft ledger entries",
        "provider_called": False,
        "tokenizer_run": False,
        "repeated_tool_calls": repeated_calls(
            ledger, {"plan_next", "revise_draft"}
        ),
        "completed_setup_steps_redone": sum(
            max(0, ledger.get(key, 0) - 1)
            for key in (
                "identify_input_params",
                "store_trace",
                "fetch_dependency_graph",
            )
        ),
        "upload_attempts": attempts,
        "duplicate_uploads": max(0, committed - 1),
        "final_writes": committed,
        "worker_seconds_during_zero_worker_wait": 0.0,
        "zero_worker_seconds_during_wait": manifest["zero_worker_seconds"],
        "history_events_at_zero_workers": manifest[
            "history_events_at_zero_workers"
        ],
        "final_correct": (
            final["root_cause"] == story.RIGHT_ROOT_CAUSE
            and final["call_path"] == story.CORRECT_PATH
        ),
        "outcome": manifest["final"]["outcome"],
        "ledger": ledger,
    }


def print_table(raw: dict[str, Any], cadence_run: dict[str, Any]) -> None:
    line_counts = durability_report()["counts"]
    rows = [
        (
            "Wall clock to correct write",
            f"{raw['wall_seconds']:.3f}s",
            f"{cadence_run['wall_seconds']:.3f}s",
        ),
        (
            "Observed scripted model calls",
            str(raw["observed_model_calls"]),
            str(cadence_run["observed_model_calls"]),
        ),
        (
            "Repeated tool calls",
            str(raw["repeated_tool_calls"]),
            str(cadence_run["repeated_tool_calls"]),
        ),
        (
            "Completed setup steps redone",
            str(raw["completed_setup_steps_redone"]),
            str(cadence_run["completed_setup_steps_redone"]),
        ),
        (
            "Upload attempts",
            str(raw["upload_attempts"]),
            str(cadence_run["upload_attempts"]),
        ),
        (
            "Duplicate uploads",
            str(raw["duplicate_uploads"]),
            str(cadence_run["duplicate_uploads"]),
        ),
        (
            "Observed final writes",
            str(raw["final_writes"]),
            str(cadence_run["final_writes"]),
        ),
        (
            "Human-wait process/worker seconds",
            f"{raw['process_seconds_waiting_for_human']:.3f}s",
            f"{cadence_run['worker_seconds_during_zero_worker_wait']:.3f}s",
        ),
        (
            "Zero-worker durable wait",
            f"{raw['zero_worker_seconds_during_wait']:.3f}s",
            f"{cadence_run['zero_worker_seconds_during_wait']:.3f}s",
        ),
        (
            "Retry/state/dedup lines",
            (
                f"{line_counts['raw']['retry']}/"
                f"{line_counts['raw']['state']}/"
                f"{line_counts['raw']['deduplication']}"
            ),
            (
                f"{line_counts['cadence']['retry']}/"
                f"{line_counts['cadence']['state']}/"
                f"{line_counts['cadence']['deduplication']}"
            ),
        ),
        (
            "Final answer correct",
            str(raw["final_correct"]),
            str(cadence_run["final_correct"]),
        ),
    ]
    widths = [
        max(len("Metric"), *(len(row[0]) for row in rows)),
        max(len("Raw asyncio"), *(len(row[1]) for row in rows)),
        max(len("Local Cadence"), *(len(row[2]) for row in rows)),
    ]
    print(
        "\nCOMPARISON "
        "(synthetic fixtures, scripted model, compressed three-day wait)"
    )
    print(
        f"{'Metric':<{widths[0]}}  "
        f"{'Raw asyncio':>{widths[1]}}  "
        f"{'Local Cadence':>{widths[2]}}"
    )
    print(
        f"{'-' * widths[0]}  "
        f"{'-' * widths[1]}  "
        f"{'-' * widths[2]}"
    )
    for label, raw_value, cadence_value in rows:
        print(
            f"{label:<{widths[0]}}  "
            f"{raw_value:>{widths[1]}}  "
            f"{cadence_value:>{widths[2]}}"
        )
    print("\nPROVENANCE")
    print("- Model calls are observed ledger entries from a deterministic script.")
    print("- No provider or tokenizer ran, so this demo reports no tokens or cost.")
    print("- Wall time is measured and may be slower on local Cadence.")


async def run(args: argparse.Namespace) -> None:
    root = args.evidence
    root.mkdir(parents=True, exist_ok=True)
    existing = sorted(path for path in root.glob("run-*") if path.is_dir())
    index = max(
        (int(path.name.removeprefix("run-")) for path in existing), default=0
    ) + 1
    run_dir = root / f"run-{index}"
    run_dir.mkdir()

    print("== Raw asyncio: capable baseline without durable execution ==")
    raw_result, raw_entries, raw_commands = run_raw(
        run_dir, f"raw-incident-{int(time.time() * 1000)}"
    )
    raw = raw_metrics(raw_result, raw_entries)

    print("\n== Local Cadence: same fixtures, planner, tools, and faults ==")
    cadence_evidence = run_dir / "cadence"
    cadence_started = time.perf_counter()
    cadence_manifest = await run_rehearsal(
        target=args.target,
        domain=args.domain,
        evidence=cadence_evidence,
        zero_worker_seconds=args.zero_worker_seconds,
    )
    cadence_run = cadence_metrics(
        cadence_manifest, time.perf_counter() - cadence_started
    )
    print_table(raw, cadence_run)

    record = {
        "run": index,
        "command": command_text([PYTHON, "run_compare.py"]),
        "child_commands": {
            "raw": raw_commands,
            "cadence": [
                command_text(
                    [
                        PYTHON,
                        "run_rehearsal.py",
                        "--target",
                        args.target,
                        "--domain",
                        args.domain,
                    ]
                )
            ],
        },
        "scope": (
            "synthetic fixtures and deterministic scripted model; "
            "no model provider or tokenizer"
        ),
        "compression": {
            "real_human_away_seconds": REAL_HUMAN_WAIT_SECONDS,
            "compressed_seconds": COMPRESSED_HUMAN_WAIT_SECONDS,
            "factor": (
                REAL_HUMAN_WAIT_SECONDS / COMPRESSED_HUMAN_WAIT_SECONDS
            ),
        },
        "raw": raw,
        "cadence": cadence_run,
        "durability_lines": durability_report(),
    }
    (run_dir / "compare.json").write_text(
        json.dumps(record, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"\nEvidence: {run_dir}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", default="localhost:7833")
    parser.add_argument("--domain", default="cadence-ai-samples")
    parser.add_argument(
        "--evidence", type=Path, default=HERE / ".evidence" / "compare-runs"
    )
    parser.add_argument("--zero-worker-seconds", type=float, default=0.75)
    args = parser.parse_args()
    import asyncio

    asyncio.run(run(args))


if __name__ == "__main__":
    main()
