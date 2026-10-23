"""Run the self-checking incident story against a local Cadence server."""

from __future__ import annotations

import argparse
import asyncio
import collections
import json
import os
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import timedelta
from pathlib import Path
from typing import Any, Callable

import cadence
from cadence.api.v1.common_pb2 import WorkflowExecution
from cadence.api.v1.service_workflow_pb2 import (
    GetWorkflowExecutionHistoryRequest,
)
from cadence.contrib.pydantic import PydanticDataConverter

import story
from workflow import (
    APPROVAL_SIGNAL,
    GUIDANCE_SIGNAL,
    REVISE_SIGNAL,
    STATUS_QUERY,
    WORKFLOW,
    RecipeInput,
)


HERE = Path(__file__).resolve().parent
PYTHON = sys.executable


def read_ledger(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def ledger_counts(entries: list[dict[str, Any]]) -> dict[str, int]:
    counts: collections.Counter[str] = collections.Counter()
    for entry in entries:
        key = entry["tool"]
        if key == "get_logs_by_trace_id":
            key += f"({entry['service']})"
        counts[key] += 1
    return dict(counts)


def spawn_worker(
    role: str,
    worker_id: str,
    task_list: str,
    target: str,
    domain: str,
    state: Path,
) -> subprocess.Popen[str]:
    environment = {
        **os.environ,
        "INCIDENT_TRIAGE_STATE": str(state),
        "INCIDENT_TRIAGE_WORKER_ID": worker_id,
    }
    log = (state / f"{worker_id}.log").open("w", encoding="utf-8")
    process = subprocess.Popen(
        [
            PYTHON,
            "worker.py",
            "--role",
            role,
            "--id",
            worker_id,
            "--target",
            target,
            "--domain",
            domain,
            "--task-list",
            task_list,
        ],
        cwd=HERE,
        env=environment,
        stdout=log,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )
    log.close()
    return process


def stop_workers(processes: list[subprocess.Popen[str]]) -> None:
    for process in processes:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=5)


async def wait_for(
    predicate: Callable[[], Any],
    *,
    timeout: float = 30,
    description: str,
) -> Any:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            value = predicate()
            if asyncio.iscoroutine(value):
                value = await value
            if value:
                return value
        except Exception:
            pass
        await asyncio.sleep(0.1)
    raise AssertionError(f"timed out waiting for {description}")


async def history_events(
    client: cadence.Client, workflow_id: str, run_id: str
) -> list[Any]:
    events: list[Any] = []
    token = b""
    while True:
        response = await client.workflow_stub.GetWorkflowExecutionHistory(
            GetWorkflowExecutionHistoryRequest(
                domain=client.domain,
                workflow_execution=WorkflowExecution(
                    workflow_id=workflow_id, run_id=run_id
                ),
                next_page_token=token,
                page_size=1000,
            )
        )
        events.extend(response.history.events)
        token = response.next_page_token
        if not token:
            return events


async def run_rehearsal(
    *,
    target: str,
    domain: str,
    evidence: Path | None,
    zero_worker_seconds: float,
) -> dict[str, Any]:
    state = Path(tempfile.mkdtemp(prefix="incident-triage-"))
    task_list = "incident-triage-rehearsal-" + uuid.uuid4().hex
    workflow_id = "incident-triage-" + uuid.uuid4().hex
    processes: list[subprocess.Popen[str]] = []
    marks: dict[str, float] = {}
    client = cadence.Client(
        domain=domain,
        target=target,
        identity="incident-triage-rehearsal",
        data_converter=PydanticDataConverter(),
    )
    try:
        processes.append(
            spawn_worker(
                "workflow", "workflow-A", task_list, target, domain, state
            )
        )
        processes.append(
            spawn_worker(
                "activity", "activities-A", task_list, target, domain, state
            )
        )
        await asyncio.sleep(1)
        (state / "faults.json").write_text(
            json.dumps(
                {workflow_id: {"logs": {"status": 429, "times": 1}}}
            ),
            encoding="utf-8",
        )
        marks["started"] = time.time()
        execution = await client.start_workflow(
            WORKFLOW,
            RecipeInput(
                text=story.trace_text(),
                limits={
                    "scan_seconds": 0.3,
                    "rescan_seconds": 0.1,
                    "approval_window_seconds": 5,
                },
            ),
            task_list=task_list,
            workflow_id=workflow_id,
            execution_start_to_close_timeout=timedelta(minutes=5),
            task_start_to_close_timeout=timedelta(seconds=30),
        )

        async def status() -> dict[str, Any]:
            return await client.query_workflow(
                workflow_id, "", STATUS_QUERY, result_type=dict
            )

        wrong = await wait_for(
            lambda: _status_when(status, "AWAITING_GUIDANCE"),
            description="the first draft",
        )
        if wrong["conclusion"]["root_cause"] != story.WRONG_ROOT_CAUSE:
            raise AssertionError(wrong)
        first_counts = ledger_counts(read_ledger(state / "ledger.jsonl"))
        if first_counts.get("get_logs_by_trace_id(api-gateway)") != 2:
            raise AssertionError(first_counts)
        for stable in (
            "identify_input_params",
            "store_trace",
            "fetch_dependency_graph",
        ):
            if first_counts.get(stable) != 1:
                raise AssertionError(first_counts)

        await client.signal_workflow(
            workflow_id, "", REVISE_SIGNAL, story.REVISION
        )
        await wait_for(
            lambda: _revision_when(status, 1, "AWAITING_GUIDANCE"),
            description="the changed brief on the first draft",
        )
        await client.signal_workflow(
            workflow_id, "", GUIDANCE_SIGNAL, story.GUIDANCE
        )
        right = await wait_for(
            lambda: _right_approval(status),
            description="the corrected draft",
        )
        if right["revision"] != 2:
            raise AssertionError(right)
        before_wait = ledger_counts(read_ledger(state / "ledger.jsonl"))
        for stable in (
            "identify_input_params",
            "store_trace",
            "fetch_dependency_graph",
            "get_logs_by_trace_id(api-gateway)",
        ):
            if before_wait.get(stable) != first_counts.get(stable):
                raise AssertionError((stable, first_counts, before_wait))

        stop_workers(processes)
        processes.clear()
        marks["all_workers_down"] = time.time()
        print(
            "CADENCE WAIT: zero workers; Workflow state and approval timer "
            "remain in history",
            flush=True,
        )
        await asyncio.sleep(zero_worker_seconds)
        events_while_down = await history_events(
            client, workflow_id, execution.run_id
        )
        if ledger_counts(read_ledger(state / "ledger.jsonl")) != before_wait:
            raise AssertionError("an Activity ran while all Workers were down")

        marks["workers_back"] = time.time()
        processes.append(
            spawn_worker(
                "workflow", "workflow-B", task_list, target, domain, state
            )
        )
        processes.append(
            spawn_worker(
                "activity", "activities-B", task_list, target, domain, state
            )
        )
        await asyncio.sleep(1)
        await client.signal_workflow(
            workflow_id, "", APPROVAL_SIGNAL, "approve"
        )
        final = await wait_for(
            lambda: _status_when(status, "DONE"),
            description="the final write",
        )
        marks["done"] = time.time()
        if final["outcome"] != "conclusion_uploaded":
            raise AssertionError(final)
        entries = read_ledger(state / "ledger.jsonl")
        final_counts = ledger_counts(entries)
        upload_attempts = sum(
            1 for entry in entries if entry["tool"] == "upload_conclusion"
        )
        committed_writes = sum(
            1
            for entry in entries
            if entry["tool"] == "upload_conclusion"
            and entry.get("committed") is True
        )
        if upload_attempts != 1 or committed_writes != 1:
            raise AssertionError((upload_attempts, committed_writes, final_counts))
        rows = sqlite3.connect(state / "store.db").execute(
            "select key, receipt from conclusions"
        ).fetchall()
        if len(rows) != 1:
            raise AssertionError(rows)
        uploader = [
            entry["worker"]
            for entry in read_ledger(state / "ledger.jsonl")
            if entry["tool"] == "upload_conclusion"
        ]
        if uploader != ["activities-B"]:
            raise AssertionError(uploader)

        manifest = {
            "scope": "synthetic fixtures and scripted model; local Cadence",
            "workflow_id": workflow_id,
            "run_id": execution.run_id,
            "marks": marks,
            "zero_worker_seconds": (
                marks["workers_back"] - marks["all_workers_down"]
            ),
            "history_events_at_zero_workers": len(events_while_down),
            "first_draft": wrong,
            "corrected_draft": right,
            "final": final,
            "ledger": final_counts,
            "upload_attempts": upload_attempts,
            "committed_writes": committed_writes,
            "upload_rows": len(rows),
            "upload_worker": uploader[0],
        }
        if evidence:
            evidence.mkdir(parents=True, exist_ok=True)
            (evidence / "manifest.json").write_text(
                json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            (evidence / "ledger.jsonl").write_text(
                (state / "ledger.jsonl").read_text(encoding="utf-8"),
                encoding="utf-8",
            )
        print(
            "REHEARSAL PASSED: 429 retried, loop bounded, brief revised, "
            "guidance retained setup, zero-worker wait survived, one write",
            flush=True,
        )
        return manifest
    finally:
        stop_workers(processes)
        await client.close()


async def _status_when(
    status: Callable[[], Any], phase: str
) -> dict[str, Any] | None:
    current = await status()
    return current if current["phase"] == phase else None


async def _right_approval(
    status: Callable[[], Any],
) -> dict[str, Any] | None:
    current = await status()
    if (
        current["phase"] == "AWAITING_APPROVAL"
        and current["conclusion"].get("root_cause") == story.RIGHT_ROOT_CAUSE
    ):
        return current
    return None


async def _revision_when(
    status: Callable[[], Any], revision: int, phase: str
) -> dict[str, Any] | None:
    current = await status()
    if current["revision"] == revision and current["phase"] == phase:
        return current
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", default="localhost:7833")
    parser.add_argument("--domain", default="cadence-ai-samples")
    parser.add_argument("--evidence", type=Path)
    parser.add_argument("--zero-worker-seconds", type=float, default=0.75)
    args = parser.parse_args()
    asyncio.run(
        run_rehearsal(
            target=args.target,
            domain=args.domain,
            evidence=args.evidence,
            zero_worker_seconds=args.zero_worker_seconds,
        )
    )


if __name__ == "__main__":
    main()
