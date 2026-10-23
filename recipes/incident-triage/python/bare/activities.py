"""Cadence Activities for the synthetic incident-triage story."""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any

from cadence import activity

import live_model
import story


IDENTIFY_ACTIVITY = "incident-triage.identify-input"
STORE_TRACE_ACTIVITY = "incident-triage.store-trace"
GET_LOGS_ACTIVITY = "incident-triage.get-logs"
GRAPH_ACTIVITY = "incident-triage.fetch-graph"
PLAN_ACTIVITY = "incident-triage.plan-next"
REVISE_ACTIVITY = "incident-triage.revise-draft"
UPLOAD_ACTIVITY = "incident-triage.upload-conclusion"

NEVER_RETRY = (400, 401, 404)


class RateLimited(RuntimeError):
    """Retryable synthetic HTTP failure."""


class NonRetryableHttpError(RuntimeError):
    """Synthetic HTTP failure that another attempt cannot fix."""


class ReplyLost(RuntimeError):
    """The write committed, but the caller did not receive the reply."""


def state_dir() -> Path:
    path = Path(
        os.environ.get(
            "INCIDENT_TRIAGE_STATE", Path(__file__).resolve().parent / ".state"
        )
    )
    path.mkdir(parents=True, exist_ok=True)
    return path


def record(tool: str, **details: Any) -> None:
    info = activity.info()
    entry = {
        "tool": tool,
        "attempt": info.attempt,
        "worker": os.environ.get("INCIDENT_TRIAGE_WORKER_ID", str(os.getpid())),
        "workflow_id": info.workflow_id,
        "at": time.time(),
        **details,
    }
    with (state_dir() / "ledger.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(entry, sort_keys=True) + "\n")


def take_fault(api: str) -> int | None:
    path = state_dir() / "faults.json"
    if not path.exists():
        return None
    faults = json.loads(path.read_text(encoding="utf-8"))
    workflow_id = activity.info().workflow_id
    key = next(
        (
            candidate
            for candidate, configured in faults.items()
            if workflow_id.startswith(candidate)
            and api in configured
            and configured[api]["times"] > 0
        ),
        None,
    )
    if key is None:
        return None
    faults[key][api]["times"] -= 1
    path.write_text(json.dumps(faults, sort_keys=True), encoding="utf-8")
    return int(faults[key][api]["status"])


def http_error(status: int) -> Exception:
    detail = f"HTTP {status}"
    if status == 429:
        detail += " Too Many Requests; Retry-After: 1"
    error_type = NonRetryableHttpError if status in NEVER_RETRY else RateLimited
    return error_type(detail)


@activity.defn(name=IDENTIFY_ACTIVITY)
def identify_input(raw: str) -> dict[str, Any]:
    record("identify_input_params")
    return story.identify_input_params(raw)


@activity.defn(name=STORE_TRACE_ACTIVITY)
def store_trace(request: dict[str, Any]) -> dict[str, str]:
    key = request["key"]
    params = request["params"]
    database = sqlite3.connect(state_dir() / "store.db")
    with database:
        database.execute(
            "create table if not exists traces (key text primary key, body text)"
        )
        database.execute(
            "insert or ignore into traces values (?, ?)",
            (key, json.dumps(params, sort_keys=True)),
        )
    path = (
        f"traces/{params['service_name']}/{params['endpoint_id']}/"
        f"{params['trace_id']}.json"
    )
    record("store_trace", key=key, path=path)
    return {"path": path}


@activity.defn(name=GET_LOGS_ACTIVITY)
async def get_logs(request: dict[str, Any]) -> dict[str, Any]:
    seconds = float(request.get("scan_seconds", 0))
    rescan_seconds = float(request.get("rescan_seconds", 0))
    duration = seconds if activity.info().attempt == 0 else rescan_seconds
    pages = max(1, int(duration / 0.1)) if duration else 1
    for page in range(pages):
        status = take_fault("logs")
        if status:
            record("get_logs_by_trace_id", service=request["service"], status=status)
            raise http_error(status)
        if duration:
            activity.heartbeat(page)
            await asyncio.sleep(duration / pages)
    record("get_logs_by_trace_id", service=request["service"], status=200)
    return story.logs_for(request["service"], request["trace_id"])


@activity.defn(name=GRAPH_ACTIVITY)
def fetch_graph(request: dict[str, Any]) -> dict[str, Any]:
    status = take_fault("graph")
    record(
        "fetch_dependency_graph",
        endpoint=request["endpoint"],
        status=status or 200,
    )
    if status:
        raise http_error(status)
    return story.dependency_graph()


@activity.defn(name=PLAN_ACTIVITY)
def plan_next(context: dict[str, Any]) -> dict[str, Any]:
    record(
        "plan_next",
        step=context["step"],
        forced=context.get("force_draft", False),
    )
    if live_model.enabled():
        return live_model.plan(context)
    return story.plan_next(context)


@activity.defn(name=REVISE_ACTIVITY)
def revise_draft(request: dict[str, Any]) -> dict[str, Any]:
    record("revise_draft", request=request["request"])
    if live_model.enabled():
        return live_model.revise(
            request["conclusion"], request["request"], request["hops"]
        )
    return story.revise_draft(
        request["conclusion"], request["request"], request["hops"]
    )


@activity.defn(name=UPLOAD_ACTIVITY)
def upload_conclusion(request: dict[str, Any]) -> dict[str, Any]:
    key = request["key"]
    database = sqlite3.connect(state_dir() / "store.db")
    with database:
        database.execute(
            "create table if not exists conclusions "
            "(key text primary key, receipt text, body text)"
        )
        existing = database.execute(
            "select receipt from conclusions where key = ?", (key,)
        ).fetchone()
        if existing:
            record("upload_conclusion", key=key, committed=False)
            return {"receipt": existing[0], "committed": False}
        receipt = "receipt-" + key.replace("/", "-")
        database.execute(
            "insert into conclusions values (?, ?, ?)",
            (key, receipt, json.dumps(request["conclusion"], sort_keys=True)),
        )
    status = take_fault("upload")
    record(
        "upload_conclusion",
        key=key,
        committed=True,
        **({"status": status} if status else {}),
    )
    if status:
        raise ReplyLost(
            f"HTTP {status} Gateway Timeout; write committed but reply was lost"
        )
    return {"receipt": receipt, "committed": True}


ACTIVITIES = (
    identify_input,
    store_trace,
    get_logs,
    fetch_graph,
    plan_next,
    revise_draft,
    upload_conclusion,
)
