"""Run bounded local-Cadence endings for the synthetic incident recipe."""

from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import subprocess
import tempfile
import uuid
from datetime import timedelta
from pathlib import Path
from typing import Any

import cadence
from cadence.contrib.pydantic import PydanticDataConverter

import story
from run_rehearsal import (
    ledger_counts,
    read_ledger,
    spawn_worker,
    stop_workers,
    wait_for,
)
from workflow import (
    APPROVAL_SIGNAL,
    GUIDANCE_SIGNAL,
    STATUS_QUERY,
    WORKFLOW,
    RecipeInput,
)


VARIANTS = ("expired", "rejected", "not-found", "budget", "lost-reply")


def upload_rows(state: Path) -> list[tuple[str, str]]:
    database = state / "store.db"
    if not database.exists():
        return []
    try:
        return sqlite3.connect(database).execute(
            "select key, receipt from conclusions"
        ).fetchall()
    except sqlite3.OperationalError:
        return []


async def run_variant(
    name: str, target: str, domain: str
) -> dict[str, Any]:
    state = Path(tempfile.mkdtemp(prefix=f"incident-triage-{name}-"))
    task_list = f"incident-triage-{name}-{uuid.uuid4().hex}"
    workflow_id = f"incident-triage-{name}-{uuid.uuid4().hex}"
    processes: list[subprocess.Popen[str]] = []
    client = cadence.Client(
        domain=domain,
        target=target,
        identity=f"incident-triage-{name}",
        data_converter=PydanticDataConverter(),
    )
    try:
        processes.append(
            spawn_worker(
                "workflow", f"{name}-workflow", task_list, target, domain, state
            )
        )
        processes.append(
            spawn_worker(
                "activity", f"{name}-activities", task_list, target, domain, state
            )
        )
        await asyncio.sleep(0.75)
        if name == "not-found":
            (state / "faults.json").write_text(
                json.dumps(
                    {workflow_id: {"graph": {"status": 404, "times": 1}}}
                ),
                encoding="utf-8",
            )
        limits = {
            "scan_seconds": 0.1,
            "rescan_seconds": 0.05,
            "approval_window_seconds": 2 if name == "expired" else 8,
        }
        if name == "budget":
            limits.update({"repeat_limit": 99, "max_steps": 6})
        execution = await client.start_workflow(
            WORKFLOW,
            RecipeInput(story.trace_text(), limits),
            task_list=task_list,
            workflow_id=workflow_id,
            execution_start_to_close_timeout=timedelta(minutes=2),
            task_start_to_close_timeout=timedelta(seconds=30),
        )

        async def status() -> dict[str, Any]:
            return await client.query_workflow(
                workflow_id, execution.run_id, STATUS_QUERY, result_type=dict
            )

        if name == "budget":
            final = await wait_for(
                lambda: status_when(status, "DONE"),
                description="budget exhaustion",
            )
            if final["outcome"] != "budget_exhausted":
                raise AssertionError(final)
        else:
            await wait_for(
                lambda: status_when(status, "AWAITING_GUIDANCE"),
                description=f"{name} first draft",
            )
            await client.signal_workflow(
                workflow_id, execution.run_id, GUIDANCE_SIGNAL, story.GUIDANCE
            )
            if name == "expired":
                final = await wait_for(
                    lambda: status_when(status, "DONE"),
                    description="approval expiration",
                )
                if final["outcome"] != "approval_expired":
                    raise AssertionError(final)
            else:
                await wait_for(
                    lambda: status_when(status, "AWAITING_APPROVAL"),
                    description=f"{name} corrected draft",
                )
                if name == "lost-reply":
                    (state / "faults.json").write_text(
                        json.dumps(
                            {
                                workflow_id: {
                                    "upload": {"status": 504, "times": 1}
                                }
                            }
                        ),
                        encoding="utf-8",
                    )
                decision = "reject" if name == "rejected" else "approve"
                await client.signal_workflow(
                    workflow_id,
                    execution.run_id,
                    APPROVAL_SIGNAL,
                    decision,
                )
                final = await wait_for(
                    lambda: status_when(status, "DONE"),
                    description=f"{name} completion",
                )

        entries = read_ledger(state / "ledger.jsonl")
        counts = ledger_counts(entries)
        rows = upload_rows(state)
        if name in ("expired", "rejected", "budget"):
            if rows or counts.get("upload_conclusion", 0):
                raise AssertionError((name, rows, counts))
        elif name == "not-found":
            graph = [
                (entry["attempt"], entry["status"])
                for entry in entries
                if entry["tool"] == "fetch_dependency_graph"
            ]
            if graph != [(0, 404)] or final["outcome"] != "conclusion_uploaded":
                raise AssertionError((graph, final))
        elif name == "lost-reply":
            writes = [
                (
                    entry["attempt"],
                    entry["committed"],
                    entry.get("status"),
                )
                for entry in entries
                if entry["tool"] == "upload_conclusion"
            ]
            if writes != [(0, True, 504), (1, False, None)] or len(rows) != 1:
                raise AssertionError((writes, rows))

        return {
            "workflow_id": workflow_id,
            "run_id": execution.run_id,
            "outcome": final["outcome"],
            "ledger": counts,
            "upload_rows": len(rows),
        }
    finally:
        stop_workers(processes)
        await client.close()


async def status_when(
    status: Any, phase: str
) -> dict[str, Any] | None:
    current = await status()
    return current if current["phase"] == phase else None


async def run(args: argparse.Namespace) -> None:
    results: dict[str, Any] = {}
    selected = args.only or list(VARIANTS)
    for name in selected:
        result = await run_variant(name, args.target, args.domain)
        results[name] = result
        print(f"PASS {name}: {result['outcome']}")
    if args.evidence:
        args.evidence.parent.mkdir(parents=True, exist_ok=True)
        args.evidence.write_text(
            json.dumps(results, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    print(f"{len(results)}/{len(selected)} variants passed")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", default="localhost:7833")
    parser.add_argument("--domain", default="cadence-ai-samples")
    parser.add_argument("--only", nargs="+", choices=VARIANTS)
    parser.add_argument("--evidence", type=Path)
    args = parser.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
