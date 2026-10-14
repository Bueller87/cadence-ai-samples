"""Strict local Ollama validation. Requires Cadence and llama3.2:latest."""

import asyncio
import logging
import os
import sys
import uuid
from collections import Counter
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cadence import Client
from cadence.api.v1.service_workflow_pb2 import GetWorkflowExecutionHistoryRequest
from cadence.contrib.pydantic import PydanticDataConverter
from cadence.worker import Worker

from config import load_model
from live import live_model_activities
from workflow import (
    BOOK_FLIGHT_ACTIVITY,
    BOOK_RIDE_ACTIVITY,
    LONG_SPECIALIST_NAME,
    MODEL_ACTIVITY,
    SHORT_SPECIALIST_NAME,
    WORKFLOW,
    RecipeInput,
    RecipeResult,
    build_registry,
)


ROOT = next(
    parent
    for parent in Path(__file__).resolve().parents
    if (parent / "models.yaml").is_file()
)
SHORT_TEXT = (
    "Book a ride from 123 Market Street to 1 Ferry Building in San Francisco."
)
LONG_TEXT = (
    "Book a trip from Uber Seattle Office to Uber San Francisco Office tomorrow."
)

logging.disable(logging.CRITICAL)


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
    async with asyncio.timeout(600):
        while True:
            events = await history(client, execution)
            for event in events:
                if event.HasField("workflow_execution_failed_event_attributes"):
                    failure = (
                        event.workflow_execution_failed_event_attributes.failure
                    )
                    raise AssertionError(
                        f"Workflow failed with {failure.reason}"
                    )
                if event.HasField("workflow_execution_completed_event_attributes"):
                    payload = (
                        event.workflow_execution_completed_event_attributes.result
                    )
                    result = client.data_converter.from_data(
                        payload, [RecipeResult]
                    )[0]
                    return result, events
            await asyncio.sleep(0.5)


def activity_counts(events: list[object]) -> Counter[str]:
    result: Counter[str] = Counter()
    for event in events:
        if event.HasField("activity_task_scheduled_event_attributes"):
            name = event.activity_task_scheduled_event_attributes.activity_type.name
            result[name] += 1
    return result


def validate(
    label: str,
    result: RecipeResult,
    counts: Counter[str],
    execution: object,
) -> None:
    expected_specialist = (
        SHORT_SPECIALIST_NAME if label == "short" else LONG_SPECIALIST_NAME
    )
    expected_route = "SHORT_TRIP" if label == "short" else "LONG_TRIP"
    expected_rides = 1 if label == "short" else 2
    expected_flights = 0 if label == "short" else 1
    errors = []
    if result.specialist != expected_specialist:
        errors.append(f"specialist={result.specialist!r}")
    if result.route != expected_route:
        errors.append(f"route={result.route!r}")
    if counts[BOOK_RIDE_ACTIVITY] != expected_rides:
        errors.append(f"ride activities={counts[BOOK_RIDE_ACTIVITY]}")
    if counts[BOOK_FLIGHT_ACTIVITY] != expected_flights:
        errors.append(f"flight activities={counts[BOOK_FLIGHT_ACTIVITY]}")
    if len(result.rides) != expected_rides:
        errors.append(f"ride receipts={len(result.rides)}")
    elif any(
        ride.status != "simulated-ride-complete" for ride in result.rides
    ):
        errors.append("ride receipt status mismatch")
    if (result.flight is not None) != bool(expected_flights):
        errors.append("flight receipt mismatch")
    elif result.flight and result.flight.status != "simulated-flight-complete":
        errors.append("flight receipt status mismatch")
    if counts[MODEL_ACTIVITY] < 1:
        errors.append(f"missing {MODEL_ACTIVITY}")
    if label == "short" and len(result.rides) == 1:
        ride = result.rides[0]
        if (
            ride.from_address,
            ride.to_address,
            ride.passengers,
        ) != (
            "123 Market Street",
            "1 Ferry Building in San Francisco",
            1,
        ):
            errors.append("short ride does not match request")
    if label == "long" and result.flight and len(result.rides) == 2:
        if (
            result.flight.from_city,
            result.flight.to_city,
            result.flight.departure,
        ) != ("Seattle", "San Francisco", "2026-10-14T10:00:00"):
            errors.append("flight does not match request")
        actual_legs = {
            (ride.from_address, ride.to_address, ride.passengers)
            for ride in result.rides
        }
        expected_legs = {
            ("Uber Seattle Office", "Seattle Airport", 1),
            ("San Francisco Airport", "Uber San Francisco Office", 1),
        }
        if actual_legs != expected_legs:
            errors.append("long rides do not match request")
    if errors:
        raise AssertionError(
            f"{label} validation failed for workflow-id={execution.workflow_id} "
            f"run-id={execution.run_id}: {'; '.join(errors)}"
        )


async def run_case(
    client: Client, task_list: str, label: str, text: str
) -> tuple[object, RecipeResult, Counter[str]]:
    execution = await client.start_workflow(
        WORKFLOW,
        RecipeInput(text, "live"),
        workflow_id=f"{task_list}-{label}",
        task_list=task_list,
        execution_start_to_close_timeout=timedelta(minutes=15),
        task_start_to_close_timeout=timedelta(minutes=2),
    )
    try:
        result, events = await completed(client, execution)
        counts = activity_counts(events)
        validate(label, result, counts, execution)
        return execution, result, counts
    except Exception as error:
        retained_counts = activity_counts(await history(client, execution))
        print(
            f"FAIL {label}: workflow-id={execution.workflow_id} "
            f"run-id={execution.run_id}; "
            f"activities={dict(sorted(retained_counts.items()))}: {error}",
            file=sys.stderr,
        )
        raise


async def main() -> None:
    client = Client(
        domain="cadence-ai-samples",
        target="localhost:7833",
        data_converter=PydanticDataConverter(),
    )
    task_list = "handoffs-google-adk-ollama-" + uuid.uuid4().hex
    selection = load_model(ROOT, "llama3.2-local")
    registry = build_registry(live_model_activities(selection, os.environ))
    results: list[tuple[str, tuple[object, RecipeResult, Counter[str]]]] = []
    failures: list[str] = []
    try:
        async with Worker(client, task_list, registry):
            for label, text in (("short", SHORT_TEXT), ("long", LONG_TEXT)):
                try:
                    results.append(
                        (label, await run_case(client, task_list, label, text))
                    )
                except Exception as error:
                    failures.append(f"{label}: {error}")
    finally:
        await client.close()
    for label, (execution, _result, counts) in results:
        print(
            f"PASS live {label}: workflow-id={execution.workflow_id} "
            f"run-id={execution.run_id}; activities={dict(sorted(counts.items()))}"
        )
    if failures:
        print(
            "llama3.2-local did not satisfy strict Google ADK handoffs: "
            + " | ".join(failures),
            file=sys.stderr,
        )
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
