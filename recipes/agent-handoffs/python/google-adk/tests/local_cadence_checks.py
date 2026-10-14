"""Local mock durability checks. Requires Cadence at localhost:7833."""

import asyncio
import uuid
from collections import Counter
from datetime import timedelta
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cadence import Client, activity
from cadence.api.v1.service_workflow_pb2 import GetWorkflowExecutionHistoryRequest
from cadence.contrib.pydantic import PydanticDataConverter
from cadence.worker import Registry, Worker

from workflow import (
    BOOK_FLIGHT_ACTIVITY,
    BOOK_RIDE_ACTIVITY,
    MOCK_ROUTE_ACTIVITY,
    WORKFLOW,
    AgentHandoffsWorkflow,
    RecipeInput,
    RecipeResult,
    RideReceipt,
    book_flight,
    book_ride,
    build_registry,
    mock_route,
)


SHORT_TEXT = (
    "Book a ride from 123 Market Street to 1 Ferry Building in San Francisco."
)
LONG_TEXT = (
    "Book a trip from Uber Seattle Office to Uber San Francisco Office tomorrow."
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


def counts(events: list[object]) -> Counter[str]:
    result: Counter[str] = Counter()
    for event in events:
        if event.HasField("activity_task_scheduled_event_attributes"):
            name = event.activity_task_scheduled_event_attributes.activity_type.name
            result[name] += 1
    return result


def completed_count(events: list[object], name: str) -> int:
    scheduled: dict[int, str] = {}
    completed = 0
    for event in events:
        if event.HasField("activity_task_scheduled_event_attributes"):
            scheduled[event.event_id] = (
                event.activity_task_scheduled_event_attributes.activity_type.name
            )
        if event.HasField("activity_task_completed_event_attributes"):
            scheduled_id = (
                event.activity_task_completed_event_attributes.scheduled_event_id
            )
            completed += scheduled.get(scheduled_id) == name
    return completed


async def wait_result(
    client: Client, execution: object
) -> tuple[RecipeResult, list[object]]:
    async with asyncio.timeout(120):
        while True:
            events = await history(client, execution)
            for event in events:
                if event.HasField("workflow_execution_failed_event_attributes"):
                    raise AssertionError("local Workflow failed")
                if event.HasField("workflow_execution_completed_event_attributes"):
                    payload = (
                        event.workflow_execution_completed_event_attributes.result
                    )
                    result = client.data_converter.from_data(
                        payload, [RecipeResult]
                    )[0]
                    return result, events
            await asyncio.sleep(0.2)


async def wait_failure(client: Client, execution: object) -> list[object]:
    async with asyncio.timeout(120):
        while True:
            events = await history(client, execution)
            for event in events:
                if event.HasField("workflow_execution_completed_event_attributes"):
                    raise AssertionError("expected local Workflow to fail")
                if event.HasField("workflow_execution_failed_event_attributes"):
                    return events
            await asyncio.sleep(0.2)


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


async def wait_for_route(client: Client, execution: object) -> None:
    async with asyncio.timeout(60):
        while True:
            events = await history(client, execution)
            if completed_count(events, MOCK_ROUTE_ACTIVITY):
                return
            await asyncio.sleep(0.2)


async def main() -> None:
    client = Client(
        domain="cadence-ai-samples",
        target="localhost:7833",
        data_converter=PydanticDataConverter(),
    )
    task_list = "handoffs-google-adk-local-" + uuid.uuid4().hex
    async with Worker(client, task_list, build_registry()):
        restart = await start(client, task_list, "restart", SHORT_TEXT)
        await wait_for_route(client, restart)
    async with Worker(client, task_list, build_registry()):
        restart_result, restart_events = await wait_result(client, restart)
    restart_counts = counts(restart_events)
    assert restart_result.route == "SHORT_TRIP"
    assert restart_counts[MOCK_ROUTE_ACTIVITY] == 1
    assert restart_counts[BOOK_RIDE_ACTIVITY] == 1
    print(
        "PASS Worker restart/replay: "
        f"workflow-id={restart.workflow_id} run-id={restart.run_id}; "
        f"activities={dict(sorted(restart_counts.items()))}"
    )

    async with Worker(client, task_list, build_registry()):
        short = await start(client, task_list, "short", SHORT_TEXT)
        short_result, short_events = await wait_result(client, short)
        long = await start(client, task_list, "long", LONG_TEXT)
        long_result, long_events = await wait_result(client, long)
    short_counts = counts(short_events)
    long_counts = counts(long_events)
    assert short_result.route == "SHORT_TRIP"
    assert short_counts[BOOK_RIDE_ACTIVITY] == 1
    assert not short_counts[BOOK_FLIGHT_ACTIVITY]
    assert long_result.route == "LONG_TRIP"
    assert long_counts[BOOK_FLIGHT_ACTIVITY] == 1
    assert long_counts[BOOK_RIDE_ACTIVITY] == 2
    print(
        "PASS short and long mock routes: "
        f"short={short.workflow_id}/{short.run_id} {dict(short_counts)}; "
        f"long={long.workflow_id}/{long.run_id} {dict(long_counts)}"
    )

    attempts = 0

    @activity.defn(name=BOOK_RIDE_ACTIVITY)
    async def flaky_ride(
        from_address: str, to_address: str, passengers: int
    ) -> RideReceipt:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise RuntimeError("controlled retryable booking failure")
        return await book_ride(from_address, to_address, passengers)

    retry_registry = Registry()
    retry_registry.workflow(AgentHandoffsWorkflow)
    retry_registry.register_activity(mock_route)
    retry_registry.register_activity(book_flight)
    retry_registry.register_activity(flaky_ride)
    async with Worker(client, task_list, retry_registry):
        retry_run = await start(client, task_list, "booking-retry", SHORT_TEXT)
        retry_result, retry_events = await wait_result(client, retry_run)
    retry_counts = counts(retry_events)
    assert retry_result.route == "SHORT_TRIP"
    assert attempts == 3
    assert retry_counts[BOOK_RIDE_ACTIVITY] == 1
    print(
        "PASS retryable booking failure: "
        f"workflow-id={retry_run.workflow_id} run-id={retry_run.run_id}; "
        f"attempts={attempts}; activities={dict(sorted(retry_counts.items()))}"
    )

    exhausted_attempts = 0

    @activity.defn(name=BOOK_RIDE_ACTIVITY)
    async def exhausted_ride(
        from_address: str, to_address: str, passengers: int
    ) -> RideReceipt:
        del from_address, to_address, passengers
        nonlocal exhausted_attempts
        exhausted_attempts += 1
        raise RuntimeError("controlled exhausted booking failure")

    exhausted_registry = Registry()
    exhausted_registry.workflow(AgentHandoffsWorkflow)
    exhausted_registry.register_activity(mock_route)
    exhausted_registry.register_activity(book_flight)
    exhausted_registry.register_activity(exhausted_ride)
    async with Worker(client, task_list, exhausted_registry):
        exhausted_run = await start(
            client, task_list, "booking-exhausted", SHORT_TEXT
        )
        exhausted_events = await wait_failure(client, exhausted_run)
    exhausted_counts = counts(exhausted_events)
    assert exhausted_attempts == 3
    assert exhausted_counts[BOOK_RIDE_ACTIVITY] == 1
    print(
        "PASS exhausted booking failure: "
        f"workflow-id={exhausted_run.workflow_id} "
        f"run-id={exhausted_run.run_id}; state=FAILED; "
        f"attempts={exhausted_attempts}; "
        f"activities={dict(sorted(exhausted_counts.items()))}"
    )
    await client.close()


if __name__ == "__main__":
    asyncio.run(main())
