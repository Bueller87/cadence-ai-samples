"""Local Cadence check for Explorer structured Signal payloads.

Run with a prepared recipe environment while Cadence and Cadence-Web are local:

    recipes/human-in-the-loop/python/openai-agents/.venv/bin/python \
        tools/explorer/tests/local_structured_signal_check.py
"""

from __future__ import annotations

import asyncio
import json
import shutil
import sys
import tempfile
import uuid
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from cadence import Client, workflow
from cadence.contrib.pydantic import PydanticDataConverter
from cadence.worker import Registry, Worker


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools" / "explorer"))

from explorer import Explorer  # noqa: E402


WORKFLOW = "StructuredSignalWorkflow"
REVISE_SIGNAL = "revise"
STATE_QUERY = "state"


@dataclass(frozen=True)
class RevisionRequest:
    expected_revision: int
    detail: str


@dataclass(frozen=True)
class RevisionState:
    expected_revision: int | None
    detail: str | None


class StructuredSignalWorkflow:
    def __init__(self) -> None:
        self._state = RevisionState(None, None)

    @workflow.signal(name=REVISE_SIGNAL)
    def revise(self, request: RevisionRequest) -> None:
        self._state = RevisionState(
            request.expected_revision, request.detail)

    @workflow.query(name=STATE_QUERY)
    def state(self) -> RevisionState:
        return self._state

    @workflow.run
    async def run(self, _seed: str) -> RevisionState:
        await workflow.wait_condition(lambda: False)
        return self._state


def registry() -> Registry:
    result = Registry()
    result.workflow(StructuredSignalWorkflow)
    return result


def metadata() -> str:
    return """version: 1
signals:
  - id: revise
    name: revise
    label: Revise
    description: Revise one version.
    intent: normal
    arguments:
      - name: request
        label: Request
        type: object
        required: true
        constraints: {}
        help: Enter revision details.
        fields:
          - name: expected_revision
            label: Expected revision
            type: integer
            required: true
            constraints:
              minimum: 0
              maximum: 1000
            help: Enter the current revision.
          - name: detail
            label: Detail
            type: string
            required: true
            constraints:
              min_length: 1
              max_length: 255
            help: Enter revision detail.
"""


async def observed_state(
    client: Client, workflow_id: str, run_id: str
) -> RevisionState:
    async with asyncio.timeout(30):
        while True:
            try:
                state = await client.query_workflow(
                    workflow_id, run_id, STATE_QUERY,
                    result_type=RevisionState)
                if state.expected_revision is not None:
                    return state
            except Exception:
                pass
            await asyncio.sleep(0.2)


async def main() -> None:
    with tempfile.TemporaryDirectory(
        prefix="explorer-structured-signal-"
    ) as temporary:
        checkout = Path(temporary)
        shutil.copytree(ROOT / "recipes", checkout / "recipes",
                        ignore=shutil.ignore_patterns(
                            ".venv", "venv", "__pycache__"))
        for name in ("README.md", "models.yaml", "classifiers.yaml"):
            shutil.copy(ROOT / name, checkout / name)
        (checkout / "recipes" / "ticket-routing" /
         "explorer.yaml").write_text(metadata())

        explorer = Explorer(checkout)
        selection = {
            "sample": "ticket-routing",
            "implementation": "recipes/ticket-routing/go",
            "mode": "demo",
            "classifier": "mock",
        }
        resolved = explorer.resolve(selection)
        workflow_id = "explorer-structured-check-" + uuid.uuid4().hex
        task_list = "explorer-structured-check-" + uuid.uuid4().hex
        client = Client(
            domain="cadence-ai-samples",
            target="localhost:7833",
            data_converter=PydanticDataConverter(),
        )
        try:
            async with Worker(client, task_list, registry()):
                execution = await client.start_workflow(
                    WORKFLOW,
                    "start",
                    workflow_id=workflow_id,
                    task_list=task_list,
                    execution_start_to_close_timeout=timedelta(minutes=3),
                    task_start_to_close_timeout=timedelta(seconds=30),
                )
                values = {
                    "expected_revision": 2,
                    "detail": "structured feedback",
                }
                result = await asyncio.to_thread(
                    explorer.workflow_signal,
                    {
                        **selection,
                        "workflow_id": workflow_id,
                        "run_id": execution.run_id,
                        "signal_id": "revise",
                        "signal_metadata_revision":
                            resolved["signal_metadata_revision"],
                        "arguments": {"request": values},
                    },
                )
                assert result["state"] == "accepted", result
                state = await observed_state(
                    client, workflow_id, execution.run_id)
                assert state == RevisionState(**values), state
                await client.cancel_workflow(
                    workflow_id, execution.run_id)
        finally:
            await client.close()

    print("PASS structured Signal: " + json.dumps({
        "workflow_id": workflow_id,
        "run_id": execution.run_id,
        "query_state": values,
    }, sort_keys=True))


if __name__ == "__main__":
    asyncio.run(main())
