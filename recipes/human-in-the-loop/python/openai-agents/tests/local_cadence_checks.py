"""Local-server durability checks: python tests/local_cadence_checks.py.

Uses an isolated mock task list and no provider credentials or requests.
"""

import asyncio
from datetime import timedelta
from pathlib import Path
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cadence import Client
from cadence.api.v1.service_workflow_pb2 import GetWorkflowExecutionHistoryRequest
from cadence.contrib.pydantic import PydanticDataConverter
from cadence.worker import Worker

from mock_model import mock_model_activities
from workflow import (
    APPROVE_SIGNAL,
    BOOK_RIDE_ACTIVITY,
    REJECT_SIGNAL,
    WORKFLOW,
    MarkdownQueryResponse,
    build_registry,
)

MODEL_ACTIVITY = "OpenAIActivities.invoke_model"


async def history(client: Client, execution: object) -> list[object]:
    events: list[object] = []
    token = b""
    while True:
        page = await client.workflow_stub.GetWorkflowExecutionHistory(
            GetWorkflowExecutionHistoryRequest(
                domain=client.domain,
                workflow_execution=execution,
                next_page_token=token,
                page_size=1000,
            )
        )
        events.extend(page.history.events)
        token = page.next_page_token
        if not token:
            return events


async def completed(
    client: Client, execution: object
) -> tuple[str, list[object]]:
    async with asyncio.timeout(60):
        while True:
            events = await history(client, execution)
            for event in events:
                if event.HasField("workflow_execution_failed_event_attributes"):
                    raise AssertionError("local test Workflow failed")
                if event.HasField("workflow_execution_completed_event_attributes"):
                    payload = (
                        event.workflow_execution_completed_event_attributes.result
                    )
                    result = client.data_converter.from_data(
                        payload, [str]
                    )[0]
                    return result, events
            await asyncio.sleep(0.2)


def scheduled_activities(events: list[object]) -> list[str]:
    return [
        event.activity_task_scheduled_event_attributes.activity_type.name
        for event in events
        if event.HasField("activity_task_scheduled_event_attributes")
    ]


async def start(
    client: Client, task_list: str, label: str
) -> object:
    return await client.start_workflow(
        WORKFLOW,
        "Book a ride from Market Street to the Ferry Building.",
        workflow_id=f"{task_list}-{label}",
        task_list=task_list,
        execution_start_to_close_timeout=timedelta(minutes=3),
        task_start_to_close_timeout=timedelta(seconds=30),
    )


async def pending_markdown(client: Client, execution: object) -> str:
    async with asyncio.timeout(30):
        while True:
            try:
                pending = await client.query_workflow(
                    execution.workflow_id,
                    execution.run_id,
                    "get_interruptions",
                    result_type=MarkdownQueryResponse,
                )
                if "mock-ride-1" in pending.data:
                    return pending.data
            except Exception:
                pass
            await asyncio.sleep(0.2)


async def main() -> None:
    client = Client(
        domain="cadence-ai-samples",
        target="localhost:7833",
        data_converter=PydanticDataConverter(),
    )
    task_list = "hitl-local-checks-" + uuid.uuid4().hex

    async with Worker(client, task_list, build_registry(mock_model_activities())):
        approved = await start(client, task_list, "approved")
        before_restart = await pending_markdown(client, approved)
        call_id = "mock-ride-1"

    async with Worker(client, task_list, build_registry(mock_model_activities())):
        after_restart = await pending_markdown(client, approved)
        assert after_restart == before_restart
        await client.signal_workflow(
            approved.workflow_id, approved.run_id, APPROVE_SIGNAL, "unknown-call"
        )
        await client.signal_workflow(
            approved.workflow_id, approved.run_id, APPROVE_SIGNAL, call_id
        )
        await client.signal_workflow(
            approved.workflow_id, approved.run_id, REJECT_SIGNAL, call_id
        )
        approved_result, approved_events = await completed(client, approved)

        rejected = await start(client, task_list, "rejected")
        await pending_markdown(client, rejected)
        rejected_call = "mock-ride-1"
        await client.signal_workflow(
            rejected.workflow_id,
            rejected.run_id,
            REJECT_SIGNAL,
            "stale-call",
        )
        await client.signal_workflow(
            rejected.workflow_id,
            rejected.run_id,
            REJECT_SIGNAL,
            rejected_call,
        )
        await client.signal_workflow(
            rejected.workflow_id,
            rejected.run_id,
            APPROVE_SIGNAL,
            rejected_call,
        )
        rejected_result, rejected_events = await completed(client, rejected)

    assert approved_result == "The simulated ride booking completed."
    assert scheduled_activities(approved_events) == [
        MODEL_ACTIVITY,
        BOOK_RIDE_ACTIVITY,
        MODEL_ACTIVITY,
    ]
    assert rejected_result == "The simulated ride request was rejected."
    assert scheduled_activities(rejected_events) == [MODEL_ACTIVITY, MODEL_ACTIVITY]
    print(
        "PASS Worker restart: "
        f"workflow-id={approved.workflow_id} run-id={approved.run_id}; "
        "pending call restored, model Activity completed twice, booking completed once"
    )
    print(
        "PASS rejection and invalid decisions: "
        f"workflow-id={rejected.workflow_id} run-id={rejected.run_id}; "
        "model Activity completed twice, booking never scheduled"
    )
    await client.close()


if __name__ == "__main__":
    asyncio.run(main())
