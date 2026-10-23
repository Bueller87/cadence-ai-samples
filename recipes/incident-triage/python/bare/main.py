"""Worker and starter CLI for Incident Triage."""

import argparse
import asyncio
import logging
import os
from collections.abc import Sequence
from datetime import timedelta

import cadence
from cadence.api.v1 import workflow_pb2
from cadence.contrib.pydantic import PydanticDataConverter
from cadence.error import WorkflowExecutionAlreadyStartedError
from cadence.worker import Worker

import live_model
from workflow import WORKFLOW, RecipeInput, build_registry


DEFAULT_TASK_LIST = "incident-triage"
DEFAULT_WORKFLOW_ID = "incident-triage-demo"
DEFAULT_TEXT = (
    '{"endpoint_id":"post-v1-checkout","service_name":"api-gateway",'
    '"trace_id":"4bf92f3577b34da6","status":500,"synthetic":true}'
)
WORKFLOW_EXECUTION_TIMEOUT = timedelta(days=7)
# A closed demo Run may be replaced; Cadence still rejects a start while a Run is open.
WORKFLOW_ID_REUSE_POLICY = workflow_pb2.WORKFLOW_ID_REUSE_POLICY_ALLOW_DUPLICATE


def parser() -> argparse.ArgumentParser:
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--target", default="localhost:7833")
    cli.add_argument("--domain", default="cadence-ai-samples")
    cli.add_argument("--task-list", default=DEFAULT_TASK_LIST)
    cli.add_argument("--workflow-id", default=DEFAULT_WORKFLOW_ID)

    commands = cli.add_subparsers(dest="command", required=True)
    worker = commands.add_parser("worker")
    worker.add_argument("--mode", choices=("mock", "live"), required=True)
    start = commands.add_parser("start")
    start.add_argument("--mode", choices=("mock", "live"), required=True)
    start.add_argument("--text", default=DEFAULT_TEXT)
    return cli


def client(args: argparse.Namespace) -> cadence.Client:
    return cadence.Client(
        domain=args.domain,
        target=args.target,
        data_converter=PydanticDataConverter(),
    )


def configure_mode(mode: str) -> str:
    if mode == "live":
        os.environ["INCIDENT_TRIAGE_MODEL"] = "live"
        selection = live_model.prepare()
        return (
            f"live model {selection.model} at {selection.endpoint}; "
            "spend lines print as each model call returns"
        )
    os.environ["INCIDENT_TRIAGE_MODEL"] = "scripted"
    return "scripted planner, including the inventory wrong turn"


async def run_worker(args: argparse.Namespace) -> None:
    mode_line = configure_mode(args.mode)
    worker = Worker(client(args), args.task_list, build_registry())
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    async with worker:
        print(mode_line, flush=True)
        print(
            f"{args.mode} incident worker polling domain={args.domain} "
            f"task-list={args.task_list}",
            flush=True,
        )
        await asyncio.Event().wait()


async def start(args: argparse.Namespace) -> None:
    cadence_client = client(args)
    try:
        execution = await cadence_client.start_workflow(
            WORKFLOW,
            RecipeInput(text=args.text),
            task_list=args.task_list,
            workflow_id=args.workflow_id,
            workflow_id_reuse_policy=WORKFLOW_ID_REUSE_POLICY,
            execution_start_to_close_timeout=WORKFLOW_EXECUTION_TIMEOUT,
            task_start_to_close_timeout=timedelta(seconds=30),
        )
    except WorkflowExecutionAlreadyStartedError as error:
        raise ValueError(
            f"workflow-id={args.workflow_id} already has an open Run; wait for it "
            "to close or pass a different --workflow-id"
        ) from error
    finally:
        await cadence_client.close()
    print(
        f"started {args.mode} synthetic incident workflow-id={execution.workflow_id} "
        f"run-id={execution.run_id}"
    )


async def main(argv: Sequence[str] | None = None) -> None:
    cli = parser()
    args = cli.parse_args(argv)
    try:
        if args.command == "worker":
            await run_worker(args)
        else:
            await start(args)
    except ValueError as error:
        cli.error(str(error))


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("worker stopped")
