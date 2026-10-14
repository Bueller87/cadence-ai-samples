"""Live llama3.2-local validation: python tests/live_ollama_validation.py.

Requires Cadence at localhost:7833, domain cadence-ai-samples, and Ollama serving
llama3.2:latest at localhost:11434. No API key is required for local Ollama.
"""

from __future__ import annotations

import asyncio
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
SHORT_TRIP_TEXT = (
    "Book a ride from 123 Market Street to 1 Ferry Building in San Francisco."
)
LONG_TRIP_TEXT = (
    "Book a trip for me from Uber Seattle Office to Uber San Francisco Office "
    "tomorrow at 10:00 AM."
)


class LiveValidationError(Exception):
    """Live Ollama validation did not meet Issue #16 handoff requirements."""


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
    client: Client, execution: object, *, timeout_seconds: int = 600
) -> tuple[RecipeResult, list[object]]:
    async with asyncio.timeout(timeout_seconds):
        while True:
            events = await history(client, execution)
            for event in events:
                if event.HasField("workflow_execution_failed_event_attributes"):
                    reason = (
                        event.workflow_execution_failed_event_attributes.failure
                    )
                    raise AssertionError(f"live Workflow failed: {reason}")
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
    counts: Counter[str] = Counter()
    for event in events:
        if event.HasField("activity_task_scheduled_event_attributes"):
            name = event.activity_task_scheduled_event_attributes.activity_type.name
            counts[name] += 1
    return counts


async def run_case(
    client: Client,
    task_list: str,
    label: str,
    text: str,
) -> tuple[RecipeResult, Counter[str], object]:
    execution = await client.start_workflow(
        WORKFLOW,
        RecipeInput(text, "live"),
        workflow_id=f"{task_list}-{label}",
        task_list=task_list,
        execution_start_to_close_timeout=timedelta(minutes=15),
        task_start_to_close_timeout=timedelta(minutes=2),
    )
    result, events = await completed(client, execution)
    return result, activity_counts(events), execution


def assert_short_live(
    result: RecipeResult, counts: Counter[str], execution: object
) -> None:
    errors: list[str] = []
    if result.specialist != SHORT_SPECIALIST_NAME:
        errors.append(
            f"specialist={result.specialist!r}, expected {SHORT_SPECIALIST_NAME!r}"
        )
    if result.route != "SHORT_TRIP":
        errors.append(f"route={result.route!r}, expected SHORT_TRIP")
    if counts.get(BOOK_RIDE_ACTIVITY, 0) != 1:
        errors.append(
            f"{BOOK_RIDE_ACTIVITY} count={counts.get(BOOK_RIDE_ACTIVITY, 0)}, expected 1"
        )
    if counts.get(BOOK_FLIGHT_ACTIVITY, 0) != 0:
        errors.append(
            f"{BOOK_FLIGHT_ACTIVITY} count={counts.get(BOOK_FLIGHT_ACTIVITY, 0)}, expected 0"
        )
    if len(result.rides) != 1:
        errors.append(f"ride receipts={len(result.rides)}, expected 1")
    elif result.rides[0].status != "simulated-ride-complete":
        errors.append(f"ride status={result.rides[0].status!r}")
    if result.flight is not None:
        errors.append("flight receipt present on short trip")
    if counts.get(MODEL_ACTIVITY, 0) < 1:
        errors.append(f"missing {MODEL_ACTIVITY}")
    if errors:
        raise LiveValidationError(
            "Short live handoff failed for "
            f"workflow-id={execution.workflow_id} run-id={execution.run_id}: "
            + "; ".join(errors)
        )


def assert_long_live(
    result: RecipeResult, counts: Counter[str], execution: object
) -> None:
    errors: list[str] = []
    if result.specialist != LONG_SPECIALIST_NAME:
        errors.append(
            f"specialist={result.specialist!r}, expected {LONG_SPECIALIST_NAME!r}"
        )
    if result.route != "LONG_TRIP":
        errors.append(f"route={result.route!r}, expected LONG_TRIP")
    if counts.get(BOOK_FLIGHT_ACTIVITY, 0) != 1:
        errors.append(
            f"{BOOK_FLIGHT_ACTIVITY} count={counts.get(BOOK_FLIGHT_ACTIVITY, 0)}, expected 1"
        )
    if counts.get(BOOK_RIDE_ACTIVITY, 0) != 2:
        errors.append(
            f"{BOOK_RIDE_ACTIVITY} count={counts.get(BOOK_RIDE_ACTIVITY, 0)}, expected 2"
        )
    if result.flight is None:
        errors.append("missing flight receipt")
    elif result.flight.status != "simulated-flight-complete":
        errors.append(f"flight status={result.flight.status!r}")
    if len(result.rides) != 2:
        errors.append(f"ride receipts={len(result.rides)}, expected 2")
    else:
        for index, ride in enumerate(result.rides, start=1):
            if ride.status != "simulated-ride-complete":
                errors.append(f"ride {index} status={ride.status!r}")
    if counts.get(MODEL_ACTIVITY, 0) < 1:
        errors.append(f"missing {MODEL_ACTIVITY}")
    if errors:
        raise LiveValidationError(
            "Long live handoff failed for "
            f"workflow-id={execution.workflow_id} run-id={execution.run_id}: "
            + "; ".join(errors)
        )


async def main() -> None:
    client = Client(
        domain="cadence-ai-samples",
        target="localhost:7833",
        data_converter=PydanticDataConverter(),
    )
    task_list = "handoffs-live-ollama-" + uuid.uuid4().hex
    selection = load_model(ROOT, "llama3.2-local")
    registry = build_registry(live_model_activities(selection, os.environ))

    failures: list[str] = []
    short_exec = long_exec = None
    short_result = long_result = None
    short_counts: Counter[str] = Counter()
    long_counts: Counter[str] = Counter()

    async with Worker(client, task_list, registry):
        try:
            short_result, short_counts, short_exec = await run_case(
                client, task_list, "live-short", SHORT_TRIP_TEXT
            )
            assert_short_live(short_result, short_counts, short_exec)
        except LiveValidationError as error:
            failures.append(str(error))

        try:
            long_result, long_counts, long_exec = await run_case(
                client, task_list, "live-long", LONG_TRIP_TEXT
            )
            assert_long_live(long_result, long_counts, long_exec)
        except LiveValidationError as error:
            failures.append(str(error))

    await client.close()

    if failures:
        message = (
            "llama3.2-local could not satisfy Issue #16 live handoff requirements "
            "(specialist handoff plus tool-backed booking Activities). "
            + " | ".join(failures)
        )
        print(message, file=sys.stderr)
        raise SystemExit(1)

    assert short_exec is not None and long_exec is not None
    assert short_result is not None and long_result is not None
    print(
        "PASS live short Ollama handoff: "
        f"workflow-id={short_exec.workflow_id} run-id={short_exec.run_id}; "
        f"activities={dict(sorted(short_counts.items()))}"
    )
    print(
        "PASS live long Ollama handoff: "
        f"workflow-id={long_exec.workflow_id} run-id={long_exec.run_id}; "
        f"activities={dict(sorted(long_counts.items()))}"
    )


if __name__ == "__main__":
    asyncio.run(main())
