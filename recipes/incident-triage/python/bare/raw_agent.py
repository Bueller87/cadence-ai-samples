"""The same synthetic triage agent as plain asyncio with in-memory state."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import story


@dataclass
class RawState:
    params: dict[str, Any] = field(default_factory=dict)
    stored: dict[str, Any] = field(default_factory=dict)
    hops: list[dict[str, Any]] = field(default_factory=list)
    calls: list[str] = field(default_factory=list)
    guidance: list[str] = field(default_factory=list)
    pending_revisions: list[str] = field(default_factory=list)
    conclusion: dict[str, Any] = field(default_factory=dict)
    reports: dict[str, dict[str, Any]] = field(default_factory=dict)
    revision: int = 0


class RawAgent:
    def __init__(self, run_id: str, phase: str, state_dir: Path) -> None:
        self.run_id = run_id
        self.phase = phase
        self.state_dir = state_dir
        self.ledger = state_dir / "ledger-raw.jsonl"
        self.memory = RawState()
        self.pass_id = ""

    def record(self, tool: str, attempt: int = 0, **details: Any) -> None:
        entry = {
            "tool": tool,
            "attempt": attempt,
            "worker": f"raw-{os.getpid()}",
            "workflow_id": self.run_id,
            "at": time.time(),
            "phase": self.phase,
            "pass_id": self.pass_id,
            **details,
        }
        with self.ledger.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(entry, sort_keys=True) + "\n")

    async def logs_with_retry(
        self, service: str, trace_id: str, inject_429: bool = False
    ) -> dict[str, Any]:
        attempt = 0
        while True:
            try:
                if inject_429 and attempt == 0:
                    self.record(
                        "get_logs_by_trace_id",
                        attempt=attempt,
                        service=service,
                        status=429,
                    )
                    raise RuntimeError("HTTP 429 Too Many Requests")
                result = story.logs_for(service, trace_id)
                self.record(
                    "get_logs_by_trace_id",
                    attempt=attempt,
                    service=service,
                    status=200,
                )
                return result
            except RuntimeError:
                if attempt >= 2:
                    raise
                await asyncio.sleep(0.1 * (2**attempt))
                attempt += 1

    async def run_pass(
        self, pass_id: str, guidance: list[str], inject_429: bool
    ) -> RawState:
        self.memory = RawState()
        self.pass_id = pass_id
        self.memory.guidance.extend(guidance)

        self.memory.params = story.identify_input_params(story.trace_text())
        self.record("identify_input_params")
        key = f"{self.run_id}/input"
        self.memory.stored[key] = dict(self.memory.params)
        self.record("store_trace", key=key)
        print(f"RAW STEP 1: input and trace stored ({pass_id})", flush=True)

        gateway, graph = await asyncio.gather(
            self.logs_with_retry(
                self.memory.params["service_name"],
                self.memory.params["trace_id"],
                inject_429=inject_429,
            ),
            asyncio.to_thread(story.dependency_graph),
        )
        self.record("fetch_dependency_graph", status=200)
        start = graph["depth1"][0]
        print(f"RAW STEP 2: research complete ({pass_id})", flush=True)

        repeats: dict[str, int] = {}
        guard = False
        action: dict[str, Any] = {}
        for step in range(1, story.MAX_STEPS + 1):
            self.record("plan_next", model_step=f"plan:{step}")
            action = story.plan_next(
                {
                    "step": step,
                    "start": start,
                    "hops": self.memory.hops,
                    "guidance": self.memory.guidance,
                    "force_draft": guard,
                }
            )
            guard = False
            if action["tool"] == "draft":
                break
            call_key = action["tool"] + json.dumps(
                action["args"], sort_keys=True
            )
            repeats[call_key] = repeats.get(call_key, 0) + 1
            if repeats[call_key] > story.REPEAT_LIMIT:
                guard = True
                continue
            self.memory.calls.append(call_key)
            self.memory.hops.append(
                await self.logs_with_retry(
                    action["args"]["service"],
                    self.memory.params["trace_id"],
                )
            )
            if step == 2:
                self.memory.pending_revisions.append(story.REVISION)
                print(
                    "RAW BRIEF: change queued by explicit in-process asyncio code",
                    flush=True,
                )
        else:
            action = {"conclusion": story.conclusion(self.memory.hops, False)}
            print(
                f"RAW TURN LIMIT: stopped after {story.MAX_STEPS} planner turns",
                flush=True,
            )

        if not self.memory.pending_revisions:
            self.memory.pending_revisions.append(story.REVISION)
        self.memory.conclusion = action["conclusion"]
        self.record("revise_draft", model_step="revise")
        self.memory.conclusion = story.revise_draft(
            self.memory.conclusion,
            self.memory.pending_revisions.pop(0),
            [gateway, *self.memory.hops],
        )
        self.memory.revision += 1
        print(
            "RAW DRAFT: "
            f"{self.memory.conclusion['root_cause']} "
            f"(revision {self.memory.revision})",
            flush=True,
        )
        return self.memory

    async def wait_for_approval(self, approve_at: float | None) -> float:
        print(
            "RAW WAITING FOR APPROVAL: live process and in-memory state must stay up",
            flush=True,
        )
        started = time.perf_counter()
        while approve_at is None or time.time() < approve_at:
            await asyncio.sleep(0.05)
        return time.perf_counter() - started

    def upload(self) -> dict[str, Any]:
        key = f"{self.run_id}/conclusion"
        if key in self.memory.reports:
            self.record("upload_conclusion", key=key, committed=False)
            return self.memory.reports[key]
        receipt = {
            "receipt": "receipt-" + key.replace("/", "-"),
            "committed": True,
        }
        self.memory.reports[key] = receipt
        self.record("upload_conclusion", key=key, committed=True)
        return receipt


async def run() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--phase", choices=("first", "restart"), required=True)
    parser.add_argument("--approve-at", type=float)
    args = parser.parse_args()

    state_dir = Path(
        os.environ.get(
            "INCIDENT_TRIAGE_STATE",
            Path(__file__).resolve().parent / ".state",
        )
    )
    state_dir.mkdir(parents=True, exist_ok=True)
    agent = RawAgent(args.run_id, args.phase, state_dir)

    if args.phase == "first":
        wrong = await agent.run_pass("initial", [], inject_429=True)
        if wrong.conclusion["root_cause"] != story.WRONG_ROOT_CAUSE:
            raise AssertionError(wrong.conclusion)
        print(
            "RAW WRONG ANSWER: no reset primitive; discarding memory and "
            "rerunning from step 1",
            flush=True,
        )
        correct = await agent.run_pass(
            "manual-rerun", [story.GUIDANCE], inject_429=False
        )
        if correct.conclusion["root_cause"] != story.RIGHT_ROOT_CAUSE:
            raise AssertionError(correct.conclusion)
        await agent.wait_for_approval(None)
        return

    print(
        "RAW RESTART: previous in-memory state is gone; beginning again at step 1",
        flush=True,
    )
    correct = await agent.run_pass(
        "process-restart", [story.GUIDANCE], inject_429=False
    )
    if correct.conclusion["root_cause"] != story.RIGHT_ROOT_CAUSE:
        raise AssertionError(correct.conclusion)
    waited = await agent.wait_for_approval(args.approve_at)
    receipt = agent.upload()
    result = {
        "phase": "DONE",
        "outcome": "conclusion_uploaded",
        "call_path": correct.conclusion["call_path"],
        "root_cause": correct.conclusion["root_cause"],
        "revision": correct.revision,
        "wait_seconds": waited,
        "receipt": receipt["receipt"],
    }
    print("RAW RESULT " + json.dumps(result, sort_keys=True), flush=True)


if __name__ == "__main__":
    asyncio.run(run())
