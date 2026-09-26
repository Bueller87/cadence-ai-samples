"""Explicit runtime check, outside offline discovery.

By default use an already-running, human-started live Worker (--task-list).
--local-ollama starts an isolated Worker with mock classification and real Ollama.
That proves local inference, not live Jev. No credentials are read by this script.
"""

import argparse
import asyncio
from contextlib import AsyncExitStack
from datetime import timedelta
import json
from pathlib import Path
import sys
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cadence import Client
from cadence.contrib.pydantic import PydanticDataConverter
from cadence.worker import Worker
from config import load_selection
from local_cadence_checks import history
from workflow import (
    CHECK_NOW_SIGNAL, STOP_WATCH_SIGNAL, WATCH_STATUS_QUERY, WATCH_WORKFLOW,
    WatchInput, WatchStatus, build_registry,
)


async def run(args):
    selection = load_selection(Path(__file__).resolve().parents[4],
        agent_id=args.agent_id, model_id=args.model_id, classifier_id="jev-default")
    suffix = uuid4().hex
    task_list = args.task_list or f"watch-local-ollama-{suffix}"
    workflow_id = f"watch-matrix-{args.agent_id}-{args.model_id}-{suffix}"
    expected = ("GoogleADKActivities.generate_content_async" if args.agent_id == "google-adk"
                else "OpenAIActivities.invoke_model" if selection.model.provider == "openai"
                else "OpenAICompatibleChatCompletions.invoke_model")
    async with AsyncExitStack() as stack:
        client = await stack.enter_async_context(Client(domain="cadence-ai-samples",
            target="localhost:7833", data_converter=PydanticDataConverter()))
        if args.local_ollama:
            if args.model_id != "llama3.2-local":
                raise ValueError("--local-ollama is restricted to llama3.2-local")
            import os
            from unittest.mock import patch
            from inference import build_model_activities
            from live import validate_live
            validate_live(selection)
            stack.enter_context(patch.dict(os.environ, {}, clear=True))
            activities = build_model_activities(selection, os.environ)
            await stack.enter_async_context(Worker(client, task_list,
                build_registry(model_activities=activities)))
        elif not args.task_list:
            raise ValueError("--task-list must identify the human-started live Worker")
        execution = await client.start_workflow(WATCH_WORKFLOW, WatchInput(
            mode="live", model_name=selection.model.model,
            agent_framework=selection.agent.framework, model_provider=selection.model.provider,
        ), task_list=task_list, workflow_id=workflow_id,
            execution_start_to_close_timeout=timedelta(minutes=5),
            task_start_to_close_timeout=timedelta(seconds=30))
        print(f"started workflow-id={workflow_id} run-id={execution.run_id}", flush=True)

        async def wait_for(predicate):
            async with asyncio.timeout(120):
                while True:
                    events = await history(client, execution)
                    if any(e.HasField("workflow_execution_failed_event_attributes")
                           or e.HasField("workflow_execution_timed_out_event_attributes") for e in events):
                        raise AssertionError("Workflow failed; inspect sanitized Activity evidence")
                    status = await client.query_workflow(workflow_id, "", WATCH_STATUS_QUERY,
                                                        result_type=WatchStatus)
                    if predicate(status):
                        return status
                    await asyncio.sleep(0.5)

        try:
            status = await wait_for(lambda s: s.state == "WAITING" and bool(s.latest_report))
            print(f"report present; checks={status.check_count}; update={status.latest_update.version}", flush=True)
            before = status.check_count
            await client.signal_workflow(workflow_id, "", CHECK_NOW_SIGNAL)
            status = await wait_for(lambda s: s.state == "WAITING" and s.check_count > before)
            assert status.check_count == before + 1, "check-now did not produce exactly one check"
            await client.signal_workflow(workflow_id, "", STOP_WATCH_SIGNAL)
            status = await wait_for(lambda s: s.state == "STOPPED")
            assert status.check_count == before + 1, "extra check during stop"
            await asyncio.sleep(16)
            status = await wait_for(lambda s: s.state == "STOPPED")
            assert status.check_count == before + 1, "extra check after stop"
            events = await history(client, execution)
            scheduled = {e.event_id: e.activity_task_scheduled_event_attributes.activity_type.name
                         for e in events if e.HasField("activity_task_scheduled_event_attributes")}
            completed = [scheduled[e.activity_task_completed_event_attributes.scheduled_event_id]
                         for e in events if e.HasField("activity_task_completed_event_attributes")]
            assert expected in completed, "selected model Activity did not complete"
            assert completed.index("recurring-watch.classify-update") < completed.index(expected)
            final = next(e for e in events if e.HasField("workflow_execution_completed_event_attributes"))
            result = client.data_converter.from_data(final.workflow_execution_completed_event_attributes.result,
                                                     [WatchStatus])[0]
            assert result.state == "STOPPED" and result.latest_report
            print(json.dumps({"result": "PASS", "agent": args.agent_id, "model": args.model_id,
                "classifier": "mock" if args.local_ollama else "jev-default",
                "workflow_id": workflow_id, "run_id": execution.run_id,
                "scheduled": list(scheduled.values()), "completed": completed,
                "checks": result.check_count, "state": result.state}, indent=2), flush=True)
        finally:
            # Request graceful stop even if an assertion fails. Never touch another Watch.
            events = await history(client, execution)
            if not any(e.WhichOneof("attributes") in {
                "workflow_execution_completed_event_attributes", "workflow_execution_failed_event_attributes",
                "workflow_execution_timed_out_event_attributes",
            } for e in events):
                await client.signal_workflow(workflow_id, "", STOP_WATCH_SIGNAL)
                await wait_for(lambda s: s.state == "STOPPED")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent-id", choices=("google-adk", "openai-agents"), required=True)
    parser.add_argument("--model-id", required=True)
    parser.add_argument("--task-list")
    parser.add_argument("--local-ollama", action="store_true")
    asyncio.run(run(parser.parse_args()))
