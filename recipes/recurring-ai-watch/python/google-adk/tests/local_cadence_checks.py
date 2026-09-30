"""Explicit local-server checks: python tests/local_cadence_checks.py.

Uses an isolated mock Worker/task list. No credentials or provider calls.
Kept outside unittest discovery because this requires a running Cadence server.
"""
import asyncio
from datetime import timedelta
from pathlib import Path
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cadence import Client, activity
from cadence.api.v1.common_pb2 import WorkflowExecution
from cadence.api.v1.service_workflow_pb2 import GetWorkflowExecutionHistoryRequest
from cadence.contrib.pydantic import PydanticDataConverter
from cadence.worker import Worker
from workflow import (
    CLASSIFY_ACTIVITY, STOP_WATCH_SIGNAL, WATCH_STATUS_QUERY, WATCH_WORKFLOW,
    ClassificationDecision, ReleaseNote, WatchInput, WatchStatus, build_registry,
)


async def history(client, execution):
    events, token = [], b""
    while True:
        page = await client.workflow_stub.GetWorkflowExecutionHistory(
            GetWorkflowExecutionHistoryRequest(
                domain=client.domain, workflow_execution=execution,
                next_page_token=token, page_size=1000,
            )
        )
        events.extend(page.history.events)
        token = page.next_page_token
        if not token:
            return events


async def main():
    client = Client(domain="cadence-ai-samples", target="localhost:7833",
                    data_converter=PydanticDataConverter())
    task_list = "watch-local-checks-" + uuid.uuid4().hex
    attempts = []
    active = asyncio.Event()
    release = asyncio.Event()
    scenario = "continue"

    @activity.defn(name=CLASSIFY_ACTIVITY)
    async def classify(update: ReleaseNote) -> ClassificationDecision:
        attempts.append((update.version, activity.info().attempt))
        if scenario == "retry" and len(attempts) <= 3:
            raise RuntimeError("scripted temporary outage")
        if scenario == "stop":
            active.set()
            await release.wait()
        return ClassificationDecision(update.version == "2.5.0", "local fixture")

    async def start(label, **values):
        return await client.start_workflow(
            WATCH_WORKFLOW, WatchInput(**values),
            workflow_id=task_list + "-" + label, task_list=task_list,
            execution_start_to_close_timeout=timedelta(minutes=3),
            task_start_to_close_timeout=timedelta(seconds=30),
        )

    async def completed(execution):
        async with asyncio.timeout(90):
            while True:
                events = await history(client, execution)
                for event in events:
                    if event.HasField("workflow_execution_failed_event_attributes"):
                        raise AssertionError("local test Workflow failed")
                    if event.HasField("workflow_execution_completed_event_attributes"):
                        payload = event.workflow_execution_completed_event_attributes.result
                        return client.data_converter.from_data(payload, [WatchStatus])[0], events
                await asyncio.sleep(0.2)

    async with Worker(client, task_list, build_registry(classify)):
        execution = await start("continue", interval=timedelta(seconds=1), max_checks=22)
        # Empty run ID follows Continue-As-New under the same Workflow ID.
        result, last = await completed(WorkflowExecution(workflow_id=execution.workflow_id))
        first = await history(client, execution)
        continued = [e for e in first if e.HasField("workflow_execution_continued_as_new_event_attributes")]
        assert len(continued) == 1
        assert result.check_count == 22 and result.latest_report and result.latest_update.version == "2.5.1"
        assert len(attempts) == 3, attempts
        assert not any(e.HasField("activity_task_scheduled_event_attributes") for e in last)
        timers = [e for e in last if e.HasField("timer_fired_event_attributes")]
        assert len(timers) == 2, "continued run must wait before checks 21 and 22"
        print("PASS Continue-As-New: 22 checks, same ID, retained report, two timers, no repeated AI")

        scenario, attempts = "retry", []
        execution = await start("retry", interval=timedelta(seconds=1), max_checks=2)
        result, events = await completed(execution)
        assert result.check_count == 2
        assert attempts == [("2.4.0", 0), ("2.4.0", 1), ("2.4.0", 2), ("2.4.0", 0)], attempts
        assert any(e.HasField("activity_task_failed_event_attributes") for e in events)
        print("PASS retry recovery: three attempts exhausted, same release succeeds on next cycle")

        scenario, attempts = "stop", []
        execution = await start("active-stop", interval=timedelta(seconds=1))
        try:
            await asyncio.wait_for(active.wait(), 15)
            status = await client.query_workflow(execution.workflow_id, "", WATCH_STATUS_QUERY, result_type=WatchStatus)
            assert status.state == "CHECKING" and status.check_count == 1
            await client.signal_workflow(execution.workflow_id, "", STOP_WATCH_SIGNAL)
        finally:
            release.set()
        result, events = await completed(execution)
        assert result.state == "STOPPED" and result.check_count == 1 and len(attempts) == 1
        signaled = next(e.event_id for e in events if e.HasField("workflow_execution_signaled_event_attributes"))
        finished = next(e.event_id for e in events if e.HasField("activity_task_completed_event_attributes"))
        assert signaled < finished
        print("PASS active stop: Signal arrived during Activity; Activity completed before STOPPED; one check")
    await client.close()


if __name__ == "__main__":
    asyncio.run(main())
