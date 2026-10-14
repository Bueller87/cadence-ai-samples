"""Strict full-state-machine validation against local llama3.2-local."""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import tempfile
import uuid
from collections import Counter
from datetime import timedelta
from pathlib import Path
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cadence import Client
from cadence.api.v1.service_workflow_pb2 import GetWorkflowExecutionHistoryRequest
from cadence.contrib.pydantic import PydanticDataConverter
from cadence.worker import Worker

from activities import PUBLISH_ACTIVITY, READ_ACTIVITY, SEARCH_ACTIVITY
from config import load_model
from live import live_model_activities
from workflow import (
    APPROVE_SIGNAL,
    EXPAND_SIGNAL,
    MODEL_ACTIVITY,
    REVISE_SIGNAL,
    STATUS_QUERY,
    WORKFLOW,
    ExpandReportRequest,
    RecipeInput,
    RecipeResult,
    ReviseReportRequest,
    WorkflowStatus,
    build_registry,
)


ROOT = next(
    parent
    for parent in Path(__file__).resolve().parents
    if (parent / "models.yaml").is_file()
)
logging.disable(logging.CRITICAL)


def require_ollama() -> None:
    selection = load_model(ROOT, "llama3.2-local")
    request = Request(selection.endpoint.rstrip("/") + "/api/tags")
    try:
        with urlopen(request, timeout=5) as response:
            body = response.read().decode("utf-8")
    except Exception as error:
        raise RuntimeError(
            "Ollama is unavailable at the llama3.2-local catalog endpoint"
        ) from error
    if '"name":"llama3.2:latest"' not in body.replace(" ", ""):
        raise RuntimeError("Ollama does not have llama3.2:latest")


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


async def wait_for_status(
    client: Client, execution: object, revision: int
) -> WorkflowStatus:
    async with asyncio.timeout(600):
        while True:
            events = await history(client, execution)
            for event in events:
                if event.HasField("workflow_execution_failed_event_attributes"):
                    failure = event.workflow_execution_failed_event_attributes.failure
                    raise AssertionError(f"Workflow failed with {failure.reason}")
            status = await client.query_workflow(
                execution.workflow_id,
                execution.run_id,
                STATUS_QUERY,
                result_type=WorkflowStatus,
            )
            if status.state == "WAITING_FOR_REVIEW" and status.revision == revision:
                return status
            await asyncio.sleep(1)


async def completed(
    client: Client, execution: object
) -> tuple[RecipeResult, list[object]]:
    async with asyncio.timeout(600):
        while True:
            events = await history(client, execution)
            for event in events:
                if event.HasField("workflow_execution_failed_event_attributes"):
                    failure = event.workflow_execution_failed_event_attributes.failure
                    raise AssertionError(f"Workflow failed with {failure.reason}")
                if event.HasField("workflow_execution_completed_event_attributes"):
                    payload = event.workflow_execution_completed_event_attributes.result
                    result = client.data_converter.from_data(
                        payload, [RecipeResult]
                    )[0]
                    return result, events
            await asyncio.sleep(1)


def counts(events: list[object]) -> Counter[str]:
    return Counter(
        event.activity_task_scheduled_event_attributes.activity_type.name
        for event in events
        if event.HasField("activity_task_scheduled_event_attributes")
    )


async def main() -> None:
    require_ollama()
    selection = load_model(ROOT, "llama3.2-local")
    client = Client(
        domain="cadence-ai-samples",
        target="localhost:7833",
        data_converter=PydanticDataConverter(),
    )
    task_list = "auto-research-google-adk-live-" + uuid.uuid4().hex
    workflow_id = task_list + "-workflow"
    execution = None
    with tempfile.TemporaryDirectory(prefix="auto-research-live-") as output:
        previous = os.environ.get("AUTO_RESEARCH_OUTPUT_DIR")
        os.environ["AUTO_RESEARCH_OUTPUT_DIR"] = output
        try:
            registry = build_registry(live_model_activities(selection, os.environ))
            async with Worker(client, task_list, registry):
                execution = await client.start_workflow(
                    WORKFLOW,
                    RecipeInput("Durable workflow recovery patterns", "live"),
                    workflow_id=workflow_id,
                    task_list=task_list,
                    execution_start_to_close_timeout=timedelta(minutes=30),
                    task_start_to_close_timeout=timedelta(seconds=30),
                )
                print(
                    f"START workflow-id={execution.workflow_id} "
                    f"run-id={execution.run_id}",
                    flush=True,
                )
                initial = await wait_for_status(client, execution, 0)
                await client.signal_workflow(
                    execution.workflow_id,
                    execution.run_id,
                    REVISE_SIGNAL,
                    ReviseReportRequest(0, "Shorten the executive summary."),
                )
                await wait_for_status(client, execution, 1)
                await client.signal_workflow(
                    execution.workflow_id,
                    execution.run_id,
                    EXPAND_SIGNAL,
                    ExpandReportRequest(1, "Operational recovery"),
                )
                await wait_for_status(client, execution, 2)
                await client.signal_workflow(
                    execution.workflow_id, execution.run_id, APPROVE_SIGNAL, 1
                )
                await client.signal_workflow(
                    execution.workflow_id, execution.run_id, APPROVE_SIGNAL, 2
                )
                result, events = await completed(client, execution)
            activity_counts = counts(events)
            assert result.revision == 2
            assert activity_counts[MODEL_ACTIVITY] == initial.question_count + 5
            assert activity_counts[SEARCH_ACTIVITY] == initial.question_count + 1
            assert activity_counts[READ_ACTIVITY] == 2 * (initial.question_count + 1)
            assert activity_counts[PUBLISH_ACTIVITY] == 1
            assert len(list(Path(output).glob("*.md"))) == 1
            print(f"PASS Activity history: {dict(sorted(activity_counts.items()))}")
        except Exception:
            if execution is not None:
                try:
                    events = await history(client, execution)
                    print(
                        "FAIL retained evidence: "
                        f"workflow-id={execution.workflow_id} run-id={execution.run_id} "
                        f"activities={dict(sorted(counts(events).items()))}",
                        file=sys.stderr,
                    )
                except Exception:
                    pass
            raise
        finally:
            if previous is None:
                os.environ.pop("AUTO_RESEARCH_OUTPUT_DIR", None)
            else:
                os.environ["AUTO_RESEARCH_OUTPUT_DIR"] = previous
            await client.close()


if __name__ == "__main__":
    asyncio.run(main())
