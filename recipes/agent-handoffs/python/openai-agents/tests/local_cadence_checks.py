"""Local-server durability checks: python tests/local_cadence_checks.py.

Uses an isolated mock task list and no provider credentials or requests.
"""

import asyncio
from collections import Counter
from datetime import timedelta
from pathlib import Path
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cadence import Client
from cadence.api.v1.service_workflow_pb2 import GetWorkflowExecutionHistoryRequest
from cadence.contrib.pydantic import PydanticDataConverter
from cadence.worker import Worker

from workflow import (
    BOOK_FLIGHT_ACTIVITY,
    BOOK_RIDE_ACTIVITY,
    MOCK_ROUTE_ACTIVITY,
    WORKFLOW,
    RecipeInput,
    RecipeResult,
    build_registry,
)


SHORT_TRIP_TEXT = (
    "Book a ride from 123 Market Street to 1 Ferry Building in San Francisco."
)
LONG_TRIP_TEXT = (
    "Book a trip for me from Uber Seattle Office to Uber San Francisco Office "
    "tomorrow at 10:00 AM."
)


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
) -> tuple[RecipeResult, list[object]]:
    async with asyncio.timeout(120):
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
                        payload, [RecipeResult]
                    )[0]
                    return result, events
            await asyncio.sleep(0.2)


def activity_counts(events: list[object]) -> Counter[str]:
    counts: Counter[str] = Counter()
    for event in events:
        if event.HasField("activity_task_scheduled_event_attributes"):
            name = event.activity_task_scheduled_event_attributes.activity_type.name
            counts[name] += 1
    return counts


def activity_completed(events: list[object], activity_name: str) -> int:
    scheduled_names: dict[int, str] = {}
    completed = 0
    for event in events:
        if event.HasField("activity_task_scheduled_event_attributes"):
            attrs = event.activity_task_scheduled_event_attributes
            scheduled_names[event.event_id] = attrs.activity_type.name
        if event.HasField("activity_task_completed_event_attributes"):
            scheduled_event_id = (
                event.activity_task_completed_event_attributes.scheduled_event_id
            )
            if scheduled_names.get(scheduled_event_id) == activity_name:
                completed += 1
    return completed


async def workflow_completed(events: list[object]) -> bool:
    return any(
        event.HasField("workflow_execution_completed_event_attributes")
        for event in events
    )


async def start(
    client: Client, task_list: str, label: str, text: str
) -> object:
    return await client.start_workflow(
        WORKFLOW,
        RecipeInput(text),
        workflow_id=f"{task_list}-{label}",
        task_list=task_list,
        execution_start_to_close_timeout=timedelta(minutes=5),
        task_start_to_close_timeout=timedelta(seconds=30),
    )


async def wait_for_activity_completion(
    client: Client, execution: object, activity_name: str
) -> list[object]:
    async with asyncio.timeout(60):
        while True:
            events = await history(client, execution)
            if activity_completed(events, activity_name) >= 1:
                return events
            if await workflow_completed(events):
                return events
            await asyncio.sleep(0.2)


async def main() -> None:
    client = Client(
        domain="cadence-ai-samples",
        target="localhost:7833",
        data_converter=PydanticDataConverter(),
    )
    task_list = "handoffs-local-checks-" + uuid.uuid4().hex

    async with Worker(client, task_list, build_registry()):
        restart_run = await start(client, task_list, "restart", SHORT_TRIP_TEXT)
        mid_events = await wait_for_activity_completion(
            client, restart_run, MOCK_ROUTE_ACTIVITY
        )
        assert activity_completed(mid_events, MOCK_ROUTE_ACTIVITY) == 1
        assert not await workflow_completed(mid_events)

    async with Worker(client, task_list, build_registry()):
        restart_result, restart_events = await completed(client, restart_run)

    restart_counts = activity_counts(restart_events)
    assert restart_result.route == "SHORT_TRIP"
    assert restart_counts[MOCK_ROUTE_ACTIVITY] == 1
    assert restart_counts[BOOK_RIDE_ACTIVITY] == 1
    print(
        "PASS Worker restart/replay: "
        f"workflow-id={restart_run.workflow_id} run-id={restart_run.run_id}; "
        "mock-route and book-ride each scheduled once after restart"
    )

    async with Worker(client, task_list, build_registry()):
        short = await start(client, task_list, "short", SHORT_TRIP_TEXT)
        short_result, short_events = await completed(client, short)

        long_run = await start(client, task_list, "long", LONG_TRIP_TEXT)
        long_result, long_events = await completed(client, long_run)

    short_counts = activity_counts(short_events)
    long_counts = activity_counts(long_events)
    assert short_result.route == "SHORT_TRIP"
    assert long_result.route == "LONG_TRIP"
    assert short_counts[MOCK_ROUTE_ACTIVITY] == 1
    assert short_counts[BOOK_RIDE_ACTIVITY] == 1
    assert BOOK_FLIGHT_ACTIVITY not in short_counts
    assert long_counts[MOCK_ROUTE_ACTIVITY] == 1
    assert long_counts[BOOK_FLIGHT_ACTIVITY] == 1
    assert long_counts[BOOK_RIDE_ACTIVITY] == 2

    print(
        "PASS short mock route: "
        f"workflow-id={short.workflow_id} run-id={short.run_id}; "
        f"activities={dict(sorted(short_counts.items()))}"
    )
    print(
        "PASS long mock route: "
        f"workflow-id={long_run.workflow_id} run-id={long_run.run_id}; "
        f"activities={dict(sorted(long_counts.items()))}"
    )
    await client.close()


if __name__ == "__main__":
    asyncio.run(main())
