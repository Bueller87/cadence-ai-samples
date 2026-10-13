"""Local-server durability and history checks.

Run with: python tests/local_cadence_checks.py
"""

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

from cadence import Client
from cadence.api.v1.service_workflow_pb2 import GetWorkflowExecutionHistoryRequest
from cadence.contrib.pydantic import PydanticDataConverter
from cadence.worker import Worker

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
    REVISE_SIGNAL,
    STATUS_QUERY,
    WORKFLOW,
    RecipeInput,
    RecipeResult,
    WorkflowStatus,
    build_registry,
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


async def wait_for_status(
    client: Client,
    execution: object,
    *,
    state: str,
    revision: int,
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
                    payload = (
                        event.workflow_execution_completed_event_attributes.result
                    )
                    result = client.data_converter.from_data(
                        payload, [RecipeResult]
                    )[0]
                    return result, events
            await asyncio.sleep(0.2)


def activity_counts(events: list[object]) -> Counter[str]:
    return Counter(
        event.activity_task_scheduled_event_attributes.activity_type.name
        for event in events
        if event.HasField("activity_task_scheduled_event_attributes")
    )


async def main() -> None:
    client = Client(
        domain="cadence-ai-samples",
        target="localhost:7833",
        data_converter=PydanticDataConverter(),
    )
    task_list = "auto-research-local-checks-" + uuid.uuid4().hex
    workflow_id = task_list + "-workflow"

    with tempfile.TemporaryDirectory(prefix="auto-research-output-") as output:
        previous = os.environ.get("AUTO_RESEARCH_OUTPUT_DIR")
        os.environ["AUTO_RESEARCH_OUTPUT_DIR"] = output
        try:
            async with Worker(client, task_list, build_registry()):
                execution = await client.start_workflow(
                    WORKFLOW,
                    RecipeInput("Durable synthetic research"),
                    workflow_id=workflow_id,
                    task_list=task_list,
                    execution_start_to_close_timeout=timedelta(minutes=5),
                    task_start_to_close_timeout=timedelta(seconds=30),
                )
                before_restart = await wait_for_status(
                    client,
                    execution,
                    state="WAITING_FOR_REVIEW",
                    revision=0,
                )

            async with Worker(client, task_list, build_registry()):
                after_restart = await wait_for_status(
                    client,
                    execution,
                    state="WAITING_FOR_REVIEW",
                    revision=0,
                )
                assert after_restart.draft == before_restart.draft
                assert after_restart.question_count == 4

                await client.signal_workflow(
                    execution.workflow_id,
                    execution.run_id,
                    APPROVE_SIGNAL,
                    99,
                )
                await client.signal_workflow(
                    execution.workflow_id,
                    execution.run_id,
                    REVISE_SIGNAL,
                    0,
                    "Make the conclusion more direct.",
                )
                await client.signal_workflow(
                    execution.workflow_id,
                    execution.run_id,
                    APPROVE_SIGNAL,
                    0,
                )
                revised = await wait_for_status(
                    client,
                    execution,
                    state="WAITING_FOR_REVIEW",
                    revision=1,
                )
                assert "Make the conclusion more direct" in revised.draft

                await client.signal_workflow(
                    execution.workflow_id,
                    execution.run_id,
                    EXPAND_SIGNAL,
                    1,
                    "Operational recovery",
                )
                expanded = await wait_for_status(
                    client,
                    execution,
                    state="WAITING_FOR_REVIEW",
                    revision=2,
                )
                assert "Operational recovery" in expanded.draft

                await client.signal_workflow(
                    execution.workflow_id,
                    execution.run_id,
                    APPROVE_SIGNAL,
                    2,
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
    assert counts[MOCK_PLAN_ACTIVITY] == 1
    assert counts[SEARCH_ACTIVITY] == 5
    assert counts[READ_ACTIVITY] == 10
    assert counts[MOCK_SPECIALIST_ACTIVITY] == 5
    assert counts[MOCK_SYNTHESIS_ACTIVITY] == 1
    assert counts[MOCK_EDIT_ACTIVITY] == 2
    assert counts[PUBLISH_ACTIVITY] == 1
    assert counts[PROGRESS_ACTIVITY] == 11

    print(
        "PASS Auto Research restart/replay: "
        f"workflow-id={execution.workflow_id} run-id={execution.run_id}; "
        "revision 0 survived Worker restart"
    )
    print(
        "PASS revision-aware review and publication: "
        "stale/conflicting actions were no-ops; revise, expand, and approve "
        "produced revision 2 and exactly one portable publication"
    )
    print(
        "PASS Activity history counts: "
        f"{dict(sorted(counts.items()))}"
    )
    await client.close()


if __name__ == "__main__":
    asyncio.run(main())
