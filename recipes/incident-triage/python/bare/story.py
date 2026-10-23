"""Shared synthetic incident, scripted planner, and fixture readers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


HERE = Path(__file__).resolve().parent
FIXTURES = HERE / "fixtures"

WRONG_ROOT_CAUSE = "inventory stock checks are slow"
RIGHT_ROOT_CAUSE = "payments v2.14 exhausted its ledger connection pool (50 of 50)"
CORRECT_PATH = ["api-gateway", "checkout", "payments", "ledger"]
GUIDANCE = "Payments deployed v2.14 at 10:02. Check payments."
REVISION = "add the error at each hop"
MAX_STEPS = 6
REPEAT_LIMIT = 2


def trace_text() -> str:
    return (FIXTURES / "trace.json").read_text(encoding="utf-8")


def identify_input_params(raw: str) -> dict[str, Any]:
    data = json.loads(raw)
    return {key: data[key] for key in ("endpoint_id", "service_name", "trace_id")}


def parse_log_line(line: str) -> dict[str, str]:
    output: dict[str, str] = {}
    key: str | None = None
    buffer = ""
    quoted = False
    for character in line + " ":
        if character == '"':
            quoted = not quoted
        elif character == "=" and not quoted and key is None:
            key, buffer = buffer, ""
        elif character == " " and not quoted:
            if key is not None:
                output[key] = buffer
            key, buffer = None, ""
        else:
            buffer += character
    return output


def logs_for(service: str, trace_id: str) -> dict[str, Any]:
    path = FIXTURES / "logs" / f"{service}.log"
    lines = [
        parse_log_line(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return {
        "service": service,
        "lines": [
            line
            for line in lines
            if line["trace_id"] == trace_id and line["level"] in ("WARN", "ERROR")
        ],
    }


def dependency_graph() -> dict[str, Any]:
    graph = json.loads((FIXTURES / "graph.json").read_text(encoding="utf-8"))
    return {
        "endpoint": graph["endpoint"],
        "tree": graph["tree"],
        "depth1": [service for service in graph["tree"] if service not in graph["ignore"]],
    }


def next_hop(hop: dict[str, Any], guidance: list[str]) -> str | None:
    errors = [
        line["downstream"]
        for line in hop["lines"]
        if line.get("downstream") and line["level"] == "ERROR"
    ]
    warnings = [
        line["downstream"]
        for line in hop["lines"]
        if line.get("downstream") and line["level"] == "WARN"
    ]
    if any(error in note.lower() for note in guidance for error in errors):
        return errors[0]
    return (warnings or errors or [None])[0]


def conclusion(hops: list[dict[str, Any]], correct: bool) -> dict[str, Any]:
    path = list(dict.fromkeys(["api-gateway", *[hop["service"] for hop in hops]]))
    if correct:
        return {
            "call_path": path,
            "root_cause": RIGHT_ROOT_CAUSE,
            "confidence": "high",
            "summary": (
                "payments v2.14 ran out of ledger connections (50 of 50); "
                "its calls timed out after 2 s, checkout got 503s, and the "
                "gateway returned 500."
            ),
        }
    return {
        "call_path": path,
        "root_cause": WRONG_ROOT_CAUSE,
        "confidence": "low",
        "summary": (
            "checkout logged a slow inventory stock check; inventory looks "
            "like the bottleneck."
        ),
    }


def plan_next(context: dict[str, Any]) -> dict[str, Any]:
    hops = context["hops"]
    if not hops:
        return {
            "tool": "get_logs_by_trace_id",
            "args": {"service": context["start"]},
            "hypothesis": "pending",
        }
    if hops[-1]["service"] == "ledger":
        return {
            "tool": "draft",
            "args": {},
            "hypothesis": RIGHT_ROOT_CAUSE,
            "conclusion": conclusion(hops, True),
        }
    if context.get("force_draft"):
        return {
            "tool": "draft",
            "args": {},
            "hypothesis": WRONG_ROOT_CAUSE,
            "conclusion": conclusion(hops, False),
        }
    following = next_hop(hops[-1], context["guidance"]) or hops[-1]["service"]
    return {
        "tool": "get_logs_by_trace_id",
        "args": {"service": following},
        "hypothesis": (
            RIGHT_ROOT_CAUSE
            if following in ("payments", "ledger")
            else WRONG_ROOT_CAUSE
        ),
    }


def revise_draft(
    draft: dict[str, Any], request: str, hops: list[dict[str, Any]]
) -> dict[str, Any]:
    errors = {
        hop["service"]: "; ".join(line["msg"] for line in hop["lines"])
        or "no warn/error lines"
        for hop in hops
    }
    return {
        **draft,
        "revision_request": request,
        "errors_per_hop": [
            f"{service}: {errors.get(service, 'HTTP 500 to the caller')}"
            for service in draft["call_path"]
        ],
    }
