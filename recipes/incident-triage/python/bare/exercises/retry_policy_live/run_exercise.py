"""Run the Incident Triage agent with a real model, then repair a lookup failure.

Scenarios:
  baseline          no injected failure; see the agent reach human review
  rate-limit        one temporary HTTP 429 on the gateway lookup
  missing-resource  a permanent HTTP 404 on the gateway lookup
  repeat-guard      two injected duplicates of the model's first lookup
  review            revise the draft, then send guidance and watch new lookups
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from datetime import timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote

HERE = Path(__file__).resolve().parent
RECIPE = HERE.parents[1]
SCENARIOS = ("baseline", "rate-limit", "missing-resource", "repeat-guard", "review")
REVISE_REQUEST = "Add the ERROR line seen at each hop."
GUIDANCE = "Check the ledger logs before concluding."
DEFAULT_MODEL_ID = "qwen2.5-local"
INJECTED_STEPS = (2, 3)


class SetupError(RuntimeError):
    """A prerequisite is missing; the message says how to fix it."""


class WorkflowClosed(RuntimeError):
    """The Workflow closed without completing while the runner waited on it."""


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--scenario", choices=[*SCENARIOS, "all"], default="baseline")
    parser.add_argument(
        "--model-id",
        default=DEFAULT_MODEL_ID,
        help="Catalog id from the repository-root models.yaml (default: %(default)s).",
    )
    parser.add_argument("--options", type=Path, default=HERE / "exercise_options.py")
    parser.add_argument("--target", default="localhost:7833")
    parser.add_argument("--domain", default="cadence-ai-samples")
    parser.add_argument("--web-url", default="http://localhost:8088")
    parser.add_argument("--cluster", default="cluster0")
    parser.add_argument(
        "--cleanup",
        choices=["prompt", "reject"],
        default="prompt",
        help="Ask before rejecting the draft, or reject without asking.",
    )
    parser.add_argument("--evidence", type=Path)
    parser.add_argument(
        "--max-steps", type=int, help="Override the recipe's planning step budget."
    )
    parser.add_argument("--timeout", type=float, default=600)
    return parser.parse_args()


def load_module(name: str, path: Path) -> Any:
    import importlib.util

    spec = importlib.util.spec_from_file_location(name, path.resolve())
    if spec is None or spec.loader is None:
        raise SetupError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def read_lines(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def append_line(path: Path, entry: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(entry, sort_keys=True) + "\n")


def web_link(args: argparse.Namespace, workflow_id: str, run_id: str) -> str:
    parts = [quote(value, safe="") for value in (args.domain, args.cluster, workflow_id, run_id)]
    return f"{args.web_url.rstrip('/')}/domains/{parts[0]}/{parts[1]}/workflows/{parts[2]}/{parts[3]}/summary"


def check_ollama(selection: Any) -> str:
    """Return the local model digest, or explain what to start or pull."""
    tags_url = selection.endpoint.rstrip("/") + "/api/tags"
    try:
        with urllib.request.urlopen(tags_url, timeout=5) as response:
            models = json.loads(response.read()).get("models", [])
    except (urllib.error.URLError, OSError, ValueError) as error:
        raise SetupError(
            f"Ollama is not answering at {selection.endpoint} ({error}). "
            "Start it with `ollama serve`, or open the Ollama app."
        ) from None
    for model in models:
        if model.get("name") == selection.model:
            return str(model.get("digest", ""))[:12]
    raise SetupError(
        f"Ollama is running but {selection.model} is not pulled. "
        f"Run `ollama pull {selection.model}` before the session."
    )


def report_model_failure(
    state: Path,
    error: BaseException,
    *,
    attempt: int,
    step: Any = None,
    call: str | None = None,
) -> None:
    """Print and record one failed model attempt. Extra spend from that attempt stays."""
    entry: dict[str, Any] = {
        "attempt": attempt,
        "error": f"{type(error).__name__}: {error}",
    }
    if step is not None:
        entry["step"] = step
    if call is not None:
        entry["call"] = call
    append_line(state / "model-errors.jsonl", entry)
    where = (
        f"revise attempt {attempt}"
        if call == "revise_draft"
        else f"step {step} attempt {attempt}"
    )
    print(
        f"MODEL CALL FAILED {where}: {type(error).__name__}: {error}",
        flush=True,
    )


def model_was_used(
    receipts: dict[str, Any], calls: list[Any], conclusion: Any
) -> bool:
    """True when each planning decision has a receipt and tokens were reported.

    Spend is recorded before response validation. A rejected attempt can leave
    a plan receipt with no decision, then a retry adds the decision. Receipts
    may therefore exceed decisions. A missing decision still fails the check.
    """
    return (
        receipts["model_responses"] > 0
        and receipts["plan_decisions"] > 0
        and receipts["plan_receipts"] >= receipts["plan_decisions"]
        and (receipts["input_tokens"] or 0) > 0
        and (receipts["output_tokens"] or 0) > 0
        and bool(calls)
        and bool(conclusion)
    )


def model_receipts(state: Path) -> dict[str, Any]:
    spend = read_lines(state / "model-spend.jsonl")
    decisions = read_lines(state / "decisions.jsonl")
    errors = read_lines(state / "model-errors.jsonl")

    def total(key: str) -> int | None:
        values = [row.get(key) for row in spend]
        if not values or not all(isinstance(x, int) for x in values):
            return None
        return sum(values)

    return {
        "model_responses": len(spend),
        "plan_decisions": sum(1 for d in decisions if d["source"] == "model"),
        "plan_receipts": sum(1 for row in spend if row.get("call") == "plan_next"),
        "revise_receipts": sum(1 for row in spend if row.get("call") == "revise_draft"),
        "input_tokens": total("input_tokens"),
        "output_tokens": total("output_tokens"),
        "decisions": decisions,
        "errors": errors,
        "note": "Provider-reported token counts. A missing count stays unavailable.",
    }


def failure_details(events: list[dict[str, Any]]) -> list[str]:
    import base64

    lines = []
    for event in events:
        for key, value in event.items():
            if not key.endswith("_event_attributes") or not isinstance(value, dict):
                continue
            failure = value.get("failure")
            if "failed" not in key or not isinstance(failure, dict):
                continue
            details = failure.get("details", "")
            try:
                details = base64.b64decode(details).decode("utf-8", "replace")
            except (ValueError, TypeError):
                pass
            message = next(
                (line for line in reversed(str(details).splitlines()) if line.strip()), ""
            )
            name = key.removesuffix("_event_attributes")
            lines.append(f"{name}: {failure.get('reason', '')}: {message}"[:300])
    return lines


async def run(args: argparse.Namespace) -> int:
    os.environ["INCIDENT_TRIAGE_MODEL"] = "live"
    os.environ["INCIDENT_TRIAGE_MODEL_ID"] = args.model_id
    sys.path.insert(0, str(RECIPE))

    import live_model

    try:
        selection = live_model.prepare()
        digest = check_ollama(selection) if selection.provider == "ollama" else ""
    except (live_model.LiveConfigurationError, SetupError) as error:
        print(f"SETUP FAILED: {error}", flush=True)
        return 2
    described = f"{selection.id} -> {selection.provider} {selection.model}"
    if digest:
        described += f" (digest {digest})"
    print(f"Model: {described} at {selection.endpoint}", flush=True)

    import cadence
    from cadence import activity
    from cadence.api.v1.common_pb2 import WorkflowExecution
    from cadence.api.v1.service_domain_pb2 import DescribeDomainRequest
    from cadence.api.v1.service_workflow_pb2 import (
        DescribeWorkflowExecutionRequest,
        GetWorkflowExecutionHistoryRequest,
    )
    from cadence.api.v1.workflow_pb2 import (
        WORKFLOW_EXECUTION_CLOSE_STATUS_COMPLETED,
        WorkflowExecutionCloseStatus,
    )
    from cadence.contrib.pydantic import PydanticDataConverter
    from cadence.worker import Worker
    from google.protobuf.json_format import MessageToDict

    import story
    import workflow

    options = load_module("exercise_options", args.options)
    workflow.SCAN_ACTIVITY = options.SCAN_ACTIVITY
    retry = options.SCAN_ACTIVITY.get("retry_policy")
    limit = retry.get("maximum_attempts") if retry else 1
    bounded = isinstance(limit, int) and not isinstance(limit, bool) and limit > 0
    initial = retry.get("initial_interval", timedelta(0)) if retry else timedelta(0)

    evidence = (
        args.evidence.resolve()
        if args.evidence
        else Path(tempfile.mkdtemp(prefix="incident-triage-exercise-"))
    )
    evidence.mkdir(parents=True, exist_ok=True)
    scenarios = list(SCENARIOS) if args.scenario == "all" else [args.scenario]

    client = cadence.Client(
        domain=args.domain,
        target=args.target,
        identity="incident-triage-exercise",
        data_converter=PydanticDataConverter(),
    )
    try:
        await asyncio.wait_for(
            client.domain_stub.DescribeDomain(DescribeDomainRequest(name=args.domain)),
            timeout=10,
        )
    except Exception as error:
        print(
            f"SETUP FAILED: Cadence at {args.target} did not describe domain "
            f"{args.domain!r} ({type(error).__name__}: {error}). Start local Cadence "
            "and register the domain as the recipe README describes.",
            flush=True,
        )
        await client.close()
        return 2

    original_plan = live_model.plan

    def recorded_plan(context: dict[str, Any], transport: Any = None) -> dict[str, Any]:
        """Record every proposal, and inject duplicates in the repeat-guard scenario."""
        info = activity.info()
        state = Path(os.environ["INCIDENT_TRIAGE_STATE"])
        step = context.get("step")
        if (
            info.workflow_id.startswith("exercise-repeat-guard-")
            and step in INJECTED_STEPS
            and not context.get("force_draft")
        ):
            first = next(
                d for d in read_lines(state / "decisions.jsonl") if d["source"] == "model"
            )["action"]
            action = {
                "tool": first["tool"],
                "args": first["args"],
                "hypothesis": "Injected duplicate of the step 1 proposal; not a model response.",
            }
            append_line(
                state / "decisions.jsonl",
                {"step": step, "attempt": info.attempt, "source": "injected", "action": action},
            )
            print(f"INJECTED step {step}: {action['tool']} {json.dumps(action['args'])}", flush=True)
            return action
        try:
            action = original_plan(context, transport)
        except Exception as error:
            report_model_failure(state, error, step=step, attempt=info.attempt)
            raise
        append_line(
            state / "decisions.jsonl",
            {"step": step, "attempt": info.attempt, "source": "model", "action": action},
        )
        print(
            f"MODEL RESPONSE step {step}: {action['tool']} {json.dumps(action['args'])} "
            f"| {action['hypothesis']}",
            flush=True,
        )
        return action

    live_model.plan = recorded_plan
    original_revise = live_model.revise

    def recorded_revise(
        conclusion: dict[str, Any],
        request: str,
        hops: list[dict[str, Any]],
        transport: Any = None,
    ) -> dict[str, Any]:
        """Record a failed revision the same way as a failed planning call."""
        info = activity.info()
        state = Path(os.environ["INCIDENT_TRIAGE_STATE"])
        try:
            return original_revise(conclusion, request, hops, transport)
        except Exception as error:
            report_model_failure(
                state, error, attempt=info.attempt, call="revise_draft"
            )
            raise

    live_model.revise = recorded_revise

    async def close_status(workflow_id: str, run_id: str) -> int:
        description = await client.workflow_stub.DescribeWorkflowExecution(
            DescribeWorkflowExecutionRequest(
                domain=args.domain,
                workflow_execution=WorkflowExecution(workflow_id=workflow_id, run_id=run_id),
            )
        )
        return description.workflow_execution_info.close_status

    async def wait_closed(workflow_id: str, run_id: str) -> int:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            value = await close_status(workflow_id, run_id)
            if value:
                return value
            await asyncio.sleep(0.2)
        raise TimeoutError("Cadence did not report a closed Workflow")

    async def history(workflow_id: str, run_id: str) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        token = b""
        while True:
            response = await client.workflow_stub.GetWorkflowExecutionHistory(
                GetWorkflowExecutionHistoryRequest(
                    domain=args.domain,
                    workflow_execution=WorkflowExecution(workflow_id=workflow_id, run_id=run_id),
                    next_page_token=token,
                    page_size=1000,
                )
            )
            result.extend(
                MessageToDict(event, preserving_proto_field_name=True)
                for event in response.history.events
            )
            token = response.next_page_token
            if not token:
                return result

    def ledger(state: Path, tool: str) -> list[dict[str, Any]]:
        return [x for x in read_lines(state / "ledger.jsonl") if x["tool"] == tool]

    async def wait_for(workflow_id: str, run_id: str, done: Any, label: str) -> dict[str, Any]:
        deadline = time.monotonic() + args.timeout
        status: dict[str, Any] = {}
        while time.monotonic() < deadline:
            closed = await close_status(workflow_id, run_id)
            if closed and closed != WORKFLOW_EXECUTION_CLOSE_STATUS_COMPLETED:
                name = WorkflowExecutionCloseStatus.Name(closed)
                raise WorkflowClosed(f"{name} while waiting for {label}")
            try:
                status = await client.query_workflow(
                    workflow_id, run_id, workflow.STATUS_QUERY, result_type=dict
                )
            except Exception:
                # The Workflow can close between the status check and the query.
                if await close_status(workflow_id, run_id):
                    continue
                raise
            if done(status):
                return status
            await asyncio.sleep(0.5)
        raise TimeoutError(
            f"timed out after {args.timeout:.0f}s waiting for {label}; "
            f"last phase {status.get('phase')}"
        )

    async def show_review(workflow_id: str, run_id: str, state: Path) -> dict[str, Any]:
        def services() -> list[str]:
            return [x["service"] for x in ledger(state, "get_logs_by_trace_id")]

        before = len(services())
        print(f"\nSignal revise: {REVISE_REQUEST!r}", flush=True)
        await client.signal_workflow(workflow_id, run_id, workflow.REVISE_SIGNAL, REVISE_REQUEST)
        revised = await wait_for(
            workflow_id,
            run_id,
            lambda s: s["revision"] >= 1 and s["phase"] == "AWAITING_GUIDANCE",
            "the revision",
        )
        after_revision = len(services())
        print(
            f"REVISED DRAFT, revision {revised['revision']}, new lookups: "
            f"{after_revision - before}",
            flush=True,
        )
        print(json.dumps(revised["conclusion"], indent=2), flush=True)
        print(f"\nSignal guidance: {GUIDANCE!r}", flush=True)
        await client.signal_workflow(workflow_id, run_id, workflow.GUIDANCE_SIGNAL, GUIDANCE)
        resumed = await wait_for(
            workflow_id,
            run_id,
            lambda s: s["phase"] in {"AWAITING_APPROVAL", "DONE"},
            "the guided investigation",
        )
        new_lookups = services()[after_revision:]
        print(
            f"GUIDED DRAFT, phase {resumed['phase']}, revision {resumed['revision']}, "
            f"new lookups: {new_lookups}",
            flush=True,
        )
        print(json.dumps(resumed["conclusion"], indent=2), flush=True)
        return {
            "revision_new_lookups": after_revision - before,
            "revision_number": revised["revision"],
            "revised_conclusion": revised["conclusion"],
            "guidance_new_lookups": new_lookups,
            "guided_phase": resumed["phase"],
            "guided_revision_number": resumed["revision"],
            "guided_calls": resumed["calls"],
            "guided_conclusion": resumed["conclusion"],
        }

    results = []
    try:
        for scenario in scenarios:
            state = evidence / scenario
            state.mkdir(exist_ok=False)
            os.environ["INCIDENT_TRIAGE_STATE"] = str(state)
            workflow_id = f"exercise-{scenario}-{uuid.uuid4().hex[:8]}"
            task_list = f"incident-triage-exercise-{uuid.uuid4().hex}"
            if scenario in {"rate-limit", "missing-resource"}:
                code, times = (429, 1) if scenario == "rate-limit" else (404, 100)
                faults = {workflow_id: {"logs": {"status": code, "times": times}}}
                (state / "faults.json").write_text(json.dumps(faults))
            limits: dict[str, Any] = {"scan_seconds": 0.1, "rescan_seconds": 0.05}
            if args.max_steps is not None:
                limits["max_steps"] = args.max_steps
            print(f"\n== Scenario: {scenario}", flush=True)
            started = time.monotonic()
            checkpoint: dict[str, Any] = {}
            final: dict[str, Any] = {}
            review: dict[str, Any] = {}
            closed = 0
            async with Worker(client, task_list, workflow.build_registry()):
                execution = await client.start_workflow(
                    workflow.WORKFLOW,
                    workflow.RecipeInput(story.trace_text(), limits),
                    task_list=task_list,
                    workflow_id=workflow_id,
                    execution_start_to_close_timeout=timedelta(seconds=args.timeout + 120),
                    task_start_to_close_timeout=timedelta(seconds=10),
                )
                run_id = execution.run_id
                link = web_link(args, workflow_id, run_id)
                print(f"Workflow ID: {workflow_id}", flush=True)
                print(f"Run ID:      {run_id}", flush=True)
                print(f"Cadence Web: {link}", flush=True)
                try:
                    try:
                        checkpoint = await wait_for(
                            workflow_id,
                            run_id,
                            lambda s: s["phase"] in {"AWAITING_GUIDANCE", "DONE"},
                            "review or a clear stop",
                        )
                    except WorkflowClosed as error:
                        print(f"Workflow stopped: {error}", flush=True)
                    (state / "review-checkpoint.json").write_text(
                        json.dumps(checkpoint, indent=2) + "\n"
                    )
                    if checkpoint.get("phase") == "AWAITING_GUIDANCE":
                        print("\nMODEL DRAFT (needs human review):", flush=True)
                        print(json.dumps(checkpoint["conclusion"], indent=2), flush=True)
                        if scenario == "review":
                            try:
                                review = await show_review(workflow_id, run_id, state)
                            except WorkflowClosed as error:
                                print(f"Review stopped: {error}", flush=True)
                    if not await close_status(workflow_id, run_id):
                        if args.cleanup == "prompt":
                            answer = await asyncio.to_thread(
                                input, "Open the run in Cadence Web. Type reject to finish: "
                            )
                            if answer.strip().lower() != "reject":
                                raise ValueError("no rejection given; cancelling this run")
                        await client.signal_workflow(
                            workflow_id, run_id, workflow.APPROVAL_SIGNAL, "reject"
                        )
                    closed = await wait_closed(workflow_id, run_id)
                    if closed == WORKFLOW_EXECUTION_CLOSE_STATUS_COMPLETED:
                        final = await client.query_workflow(
                            workflow_id, run_id, workflow.STATUS_QUERY, result_type=dict
                        )
                finally:
                    events = await history(workflow_id, run_id)
                    (state / "history.json").write_text(json.dumps(events, indent=2) + "\n")
                    if not closed:
                        await client.cancel_workflow(workflow_id, run_id)
                        closed = await wait_closed(workflow_id, run_id)

            elapsed = time.monotonic() - started
            service_status = WorkflowExecutionCloseStatus.Name(closed).removeprefix(
                "WORKFLOW_EXECUTION_CLOSE_STATUS_"
            )
            receipts = model_receipts(state)
            entries = read_lines(state / "ledger.jsonl")
            planning = next(
                (i for i, item in enumerate(entries) if item["tool"] == "plan_next"),
                len(entries),
            )
            # Only the setup lookup before planning; the agent may look up the
            # gateway again later as one of its own steps.
            attempts = [
                (item["attempt"], item["status"])
                for item in entries[:planning]
                if item["tool"] == "get_logs_by_trace_id"
                and item["service"] == "api-gateway"
            ]
            graph_calls = len(ledger(state, "fetch_dependency_graph"))
            uploads = len(ledger(state, "upload_conclusion"))
            calls = checkpoint.get("calls", [])
            reviewed = checkpoint.get("phase") == "AWAITING_GUIDANCE"
            used_model = model_was_used(
                receipts, calls, checkpoint.get("conclusion")
            )
            checks: dict[str, bool] = {
                "service status COMPLETED": closed == WORKFLOW_EXECUTION_CLOSE_STATUS_COMPLETED,
                "one dependency graph lookup": graph_calls == 1,
                "no report upload": uploads == 0,
                "finite retry attempts": bounded,
            }
            if scenario == "missing-resource":
                checks["gateway attempts (0,404) only"] = attempts == [(0, 404)]
                checks["outcome gateway_logs_unavailable"] = (
                    final.get("outcome") == "gateway_logs_unavailable"
                )
                checks["no planning after the permanent error"] = not ledger(state, "plan_next")
            else:
                expected = [(0, 429), (1, 200)] if scenario == "rate-limit" else [(0, 200)]
                checks[f"gateway attempts {expected}"] = attempts == expected
                checks["model reached human review"] = reviewed
                checks["real model responses with token counts"] = used_model
                checks["rejected at review"] = final.get("outcome") == "rejected"
            if scenario == "rate-limit":
                checks["first retry waits at least 1s"] = initial >= timedelta(seconds=1)
            if scenario == "repeat-guard":
                first = calls[0] if calls else ""
                sources = [(d["step"], d["source"]) for d in receipts["decisions"]]
                checks["second identical call dispatched"] = calls == [first, first]
                checks["third identical call blocked"] = (
                    checkpoint.get("guard") == f"blocked repeated call {first}"
                )
                checks["model drafted after the guard"] = (
                    (2, "injected") in sources
                    and (3, "injected") in sources
                    and sources[-1] == (4, "model")
                )
            if scenario == "review":
                checks["revision made no new lookups"] = (
                    review.get("revision_new_lookups") == 0
                    and review.get("revision_number") == 1
                )
                checks["guidance resumed with new lookups"] = bool(
                    review.get("guidance_new_lookups")
                )
                checks["revision reapplied after guidance"] = (
                    review.get("guided_phase") == "AWAITING_APPROVAL"
                    and review.get("guided_revision_number") == 2
                )
            passed = all(checks.values())

            print(f"\nService status: {service_status}. Outcome: {final.get('outcome')}.")
            print(f"Gateway attempts: {attempts}. Graph calls: {graph_calls}.")
            print(f"Executed agent lookups: {calls}")
            if checkpoint.get("guard"):
                print(f"Repeated-call guard: {checkpoint['guard']}")
            print(
                f"Model responses: {receipts['model_responses']}; input tokens: "
                f"{receipts['input_tokens']}; output tokens: {receipts['output_tokens']}."
            )
            for line in receipts["errors"]:
                print(f"Model error kept: step {line['step']} attempt {line['attempt']}: {line['error']}")
            if closed != WORKFLOW_EXECUTION_CLOSE_STATUS_COMPLETED:
                for line in failure_details(events):
                    print(f"Cadence failure: {line}")
            for name, ok in checks.items():
                print(f"  [{'x' if ok else ' '}] {name}")
            if scenario == "rate-limit" and attempts == [(0, 429)]:
                print(
                    "Hint: the gateway lookup failed once and was not retried, so the "
                    "agent never planned. Add a retry policy in exercise_options.py."
                )
            if scenario == "missing-resource" and len(attempts) > 1:
                print(
                    "Hint: the permanent 404 was retried. Keep non-retryable errors "
                    "from being retried with the same input."
                )
            print(f"Cadence Web: {link}")
            print(f"Elapsed: {elapsed:.1f}s")
            print(f"{'PASS' if passed else 'NOT PASSING'}: {scenario}", flush=True)

            result = {
                "scenario": scenario,
                "model_selection": {
                    "id": selection.id,
                    "provider": selection.provider,
                    "model": selection.model,
                    "endpoint": selection.endpoint,
                    "digest": digest,
                },
                "workflow_id": workflow_id,
                "run_id": run_id,
                "cadence_web": link,
                "elapsed_seconds": round(elapsed, 1),
                "attempts": attempts,
                "configured_maximum_attempts": limit,
                "configured_initial_interval_seconds": initial.total_seconds(),
                "graph_calls": graph_calls,
                "upload_calls": uploads,
                "executed_agent_calls": calls,
                "model_receipts": receipts,
                "checkpoint": checkpoint,
                "review": review,
                "final": final,
                "service_status": service_status,
                "checks": checks,
                "passed": passed,
            }
            (state / "result.json").write_text(json.dumps(result, indent=2) + "\n")
            results.append(result)
    except Exception as error:
        print(f"\nRUN FAILED: {type(error).__name__}: {error}", flush=True)
        results.append({"scenario": scenarios[len(results)], "passed": False, "error": str(error)})
    finally:
        await client.close()
        (evidence / "results.json").write_text(json.dumps(results, indent=2) + "\n")
        print(f"\nEvidence: {evidence}", flush=True)
    return 0 if results and all(item["passed"] for item in results) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run(arguments())))
