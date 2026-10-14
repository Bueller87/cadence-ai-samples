"""Local-server restart, review, publication, and history checks."""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import uuid
from collections import Counter
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cadence import Client, activity
from cadence.api.v1.service_workflow_pb2 import GetWorkflowExecutionHistoryRequest
from cadence.contrib.pydantic import PydanticDataConverter
from cadence.worker import Worker
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.genai import types

from activities import (
    MOCK_EDIT_ACTIVITY,
    MOCK_PLAN_ACTIVITY,
    MOCK_SPECIALIST_ACTIVITY,
    MOCK_SYNTHESIS_ACTIVITY,
    PROGRESS_ACTIVITY,
    PUBLISH_ACTIVITY,
    READ_ACTIVITY,
    SEARCH_ACTIVITY,
)
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


class RetryingModelActivities:
    """Controlled model Activity used to verify real Cadence retry execution."""

    def __init__(self, failures_before_success: int) -> None:
        self.failures_before_success = failures_before_success
        self.planner_attempts = 0
        self.total_attempts = 0
        self.successful_calls = 0

    @activity.method(name=MODEL_ACTIVITY)
    async def generate_content_async(
        self, model_name: str, llm_request: LlmRequest
    ) -> list[LlmResponse]:
        del model_name, llm_request
        self.total_attempts += 1
        if self.successful_calls == 0:
            self.planner_attempts += 1
            if self.planner_attempts <= self.failures_before_success:
                raise RuntimeError("controlled retryable model failure")
        responses = (
            "1. What evidence supports retry-safe orchestration?",
            "### Retry-safe orchestration\n\nSynthetic bounded summary.",
            (
                "# Retry-safe orchestration\n\n## Executive summary\n\nSynthetic.\n\n"
                "## Key findings\n\nBounded.\n\n"
                "## Cross-cutting challenges\n\nVerification.\n\n"
                "## Outlook\n\nFurther validation."
            ),
        )
        response = responses[self.successful_calls]
        self.successful_calls += 1
        return [
            LlmResponse(
                content=types.Content(
                    role="model", parts=[types.Part(text=response)]
                )
            )
        ]


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
    client: Client, execution: object, *, state: str, revision: int
) -> WorkflowStatus:
    async with asyncio.timeout(120):
        while True:
            try:
                status = await client.query_workflow(
                    execution.workflow_id,
                    execution.run_id,
                    STATUS_QUERY,
                    result_type=WorkflowStatus,
                )
                if status.state == state and status.revision == revision:
                    return status
            except Exception:
                pass
            await asyncio.sleep(0.2)


async def completed(
    client: Client, execution: object
) -> tuple[RecipeResult, list[object]]:
    async with asyncio.timeout(120):
        while True:
            events = await history(client, execution)
            for event in events:
                if event.HasField("workflow_execution_failed_event_attributes"):
                    raise AssertionError("local Auto Research Workflow failed")
                if event.HasField("workflow_execution_completed_event_attributes"):
                    payload = event.workflow_execution_completed_event_attributes.result
                    result = client.data_converter.from_data(
                        payload, [RecipeResult]
                    )[0]
                    return result, events
            await asyncio.sleep(0.2)


async def failed(client: Client, execution: object) -> list[object]:
    async with asyncio.timeout(120):
        while True:
            events = await history(client, execution)
            if any(
                event.HasField("workflow_execution_failed_event_attributes")
                for event in events
            ):
                return events
            await asyncio.sleep(0.2)


def activity_counts(events: list[object]) -> Counter[str]:
    return Counter(
        event.activity_task_scheduled_event_attributes.activity_type.name
        for event in events
        if event.HasField("activity_task_scheduled_event_attributes")
    )


async def retry_checks(client: Client) -> None:
    with tempfile.TemporaryDirectory(prefix="auto-research-retry-output-") as output:
        previous = os.environ.get("AUTO_RESEARCH_OUTPUT_DIR")
        os.environ["AUTO_RESEARCH_OUTPUT_DIR"] = output
        try:
            succeeds = RetryingModelActivities(failures_before_success=2)
            task_list = "auto-research-retry-success-" + uuid.uuid4().hex
            async with Worker(
                client, task_list, build_registry(succeeds)
            ):
                execution = await client.start_workflow(
                    WORKFLOW,
                    RecipeInput("Retry success", "live"),
                    workflow_id=task_list + "-workflow",
                    task_list=task_list,
                    execution_start_to_close_timeout=timedelta(minutes=5),
                    task_start_to_close_timeout=timedelta(seconds=30),
                )
                await wait_for_status(
                    client,
                    execution,
                    state="WAITING_FOR_REVIEW",
                    revision=0,
                )
                await client.signal_workflow(
                    execution.workflow_id,
                    execution.run_id,
                    APPROVE_SIGNAL,
                    0,
                )
                result, success_events = await completed(client, execution)
            assert result.outcome == "APPROVED_PUBLISHED"
            assert succeeds.planner_attempts == 3
            assert succeeds.total_attempts == 5
            assert activity_counts(success_events)[PUBLISH_ACTIVITY] == 1
            assert len(list(Path(output).glob("*.md"))) == 1
            success_execution = execution

            exhausted = RetryingModelActivities(failures_before_success=99)
            task_list = "auto-research-retry-exhausted-" + uuid.uuid4().hex
            async with Worker(
                client, task_list, build_registry(exhausted)
            ):
                execution = await client.start_workflow(
                    WORKFLOW,
                    RecipeInput("Retry exhaustion", "live"),
                    workflow_id=task_list + "-workflow",
                    task_list=task_list,
                    execution_start_to_close_timeout=timedelta(minutes=5),
                    task_start_to_close_timeout=timedelta(seconds=30),
                )
                failure_events = await failed(client, execution)
            assert exhausted.planner_attempts == 3
            assert exhausted.total_attempts == 3
            assert activity_counts(failure_events)[PUBLISH_ACTIVITY] == 0
            assert len(list(Path(output).glob("*.md"))) == 1
            exhausted_execution = execution
        finally:
            if previous is None:
                os.environ.pop("AUTO_RESEARCH_OUTPUT_DIR", None)
            else:
                os.environ["AUTO_RESEARCH_OUTPUT_DIR"] = previous

    print(
        "PASS real Cadence model retries: retryable planner succeeded on "
        f"attempt 3, workflow-id={success_execution.workflow_id} "
        f"run-id={success_execution.run_id}; exhausted planner failed after "
        f"exactly 3 attempts, workflow-id={exhausted_execution.workflow_id} "
        f"run-id={exhausted_execution.run_id}"
    )


async def main() -> None:
    client = Client(
        domain="cadence-ai-samples",
        target="localhost:7833",
        data_converter=PydanticDataConverter(),
    )
    task_list = "auto-research-google-adk-checks-" + uuid.uuid4().hex
    workflow_id = task_list + "-workflow"

    with tempfile.TemporaryDirectory(prefix="auto-research-output-") as output:
        previous = os.environ.get("AUTO_RESEARCH_OUTPUT_DIR")
        os.environ["AUTO_RESEARCH_OUTPUT_DIR"] = output
        try:
            execution = await client.start_workflow(
                WORKFLOW,
                RecipeInput("Durable synthetic research"),
                workflow_id=workflow_id,
                task_list=task_list,
                execution_start_to_close_timeout=timedelta(minutes=5),
                task_start_to_close_timeout=timedelta(seconds=30),
            )
            await client.signal_workflow(
                execution.workflow_id,
                execution.run_id,
                REVISE_SIGNAL,
                ReviseReportRequest(0, "early revision"),
            )
            await client.signal_workflow(
                execution.workflow_id,
                execution.run_id,
                APPROVE_SIGNAL,
                0,
            )
            async with Worker(client, task_list, build_registry()):
                before_restart = await wait_for_status(
                    client, execution, state="WAITING_FOR_REVIEW", revision=0
                )

            async with Worker(client, task_list, build_registry()):
                after_restart = await wait_for_status(
                    client, execution, state="WAITING_FOR_REVIEW", revision=0
                )
                assert after_restart.draft == before_restart.draft
                assert after_restart.question_count == 4
                await client.signal_workflow(
                    execution.workflow_id, execution.run_id, APPROVE_SIGNAL, 99
                )
                await client.signal_workflow(
                    execution.workflow_id,
                    execution.run_id,
                    REVISE_SIGNAL,
                    ReviseReportRequest(0, "Make the conclusion more direct."),
                )
                await client.signal_workflow(
                    execution.workflow_id, execution.run_id, APPROVE_SIGNAL, 0
                )
                revised = await wait_for_status(
                    client, execution, state="WAITING_FOR_REVIEW", revision=1
                )
                assert "Make the conclusion more direct" in revised.draft
                await client.signal_workflow(
                    execution.workflow_id,
                    execution.run_id,
                    EXPAND_SIGNAL,
                    ExpandReportRequest(1, "Operational recovery"),
                )
                expanded = await wait_for_status(
                    client, execution, state="WAITING_FOR_REVIEW", revision=2
                )
                assert "Operational recovery" in expanded.draft
                await client.signal_workflow(
                    execution.workflow_id, execution.run_id, APPROVE_SIGNAL, 2
                )
                result, events = await completed(client, execution)
        finally:
            if previous is None:
                os.environ.pop("AUTO_RESEARCH_OUTPUT_DIR", None)
            else:
                os.environ["AUTO_RESEARCH_OUTPUT_DIR"] = previous

        reports = list(Path(output).glob("*.md"))
        assert len(reports) == 1
        assert reports[0].name == result.publication.filename
        assert str(Path(output).resolve()) not in repr(result.publication)

    counts = activity_counts(events)
    assert result.outcome == "APPROVED_PUBLISHED"
    assert result.revision == 2
    expected = {
        MOCK_PLAN_ACTIVITY: 1,
        SEARCH_ACTIVITY: 5,
        READ_ACTIVITY: 10,
        MOCK_SPECIALIST_ACTIVITY: 5,
        MOCK_SYNTHESIS_ACTIVITY: 1,
        MOCK_EDIT_ACTIVITY: 2,
        PUBLISH_ACTIVITY: 1,
        PROGRESS_ACTIVITY: 11,
    }
    for name, count in expected.items():
        assert counts[name] == count, (name, counts[name])

    print(
        "PASS Auto Research Google ADK restart/replay: "
        f"workflow-id={execution.workflow_id} run-id={execution.run_id}"
    )
    print(
        "PASS revision-aware review: early/stale/conflicting no-ops, revise, "
        "expand, approve, and one portable publication"
    )
    print(f"PASS Activity history counts: {dict(sorted(counts.items()))}")
    await retry_checks(client)
    await client.close()


if __name__ == "__main__":
    asyncio.run(main())
