"""Worker and starter CLI for Human In The Loop."""

import argparse
import asyncio
import json
import logging
import os
from collections.abc import Sequence
from dataclasses import asdict
from datetime import timedelta
from pathlib import Path

import cadence
from cadence.api.v1 import workflow_pb2
from cadence.contrib.pydantic import PydanticDataConverter
from cadence.error import WorkflowExecutionAlreadyStartedError
from cadence.worker import Worker

from config import load_model
from workflow import (
    APPROVE_SIGNAL,
    REJECT_SIGNAL,
    STATUS_QUERY,
    WORKFLOW,
    RecipeInput,
    WorkflowStatus,
    build_registry,
)


DEFAULT_TASK_LIST = "human-in-the-loop-openai-agents"
DEFAULT_WORKFLOW_ID = "human-in-the-loop-openai-agents-demo"
DEFAULT_TEXT = (
    "Book a ride from 123 Market Street to 1 Ferry Building for one passenger."
)
WORKFLOW_ID_REUSE_POLICY = (
    workflow_pb2.WORKFLOW_ID_REUSE_POLICY_ALLOW_DUPLICATE
)


def parser() -> argparse.ArgumentParser:
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--target", default="localhost:7833")
    cli.add_argument("--domain", default="cadence-ai-samples")
    cli.add_argument("--task-list", default=DEFAULT_TASK_LIST)
    cli.add_argument("--workflow-id", default=DEFAULT_WORKFLOW_ID)
    commands = cli.add_subparsers(dest="command", required=True)
    worker = commands.add_parser("worker")
    worker.add_argument("--mode", choices=("mock", "live"), required=True)
    worker.add_argument("--confirm-live", action="store_true")
    worker.add_argument("--catalog-dir", type=Path, default=Path(__file__).parents[4])
    worker.add_argument("--model-id", default="gemini-flash-lite")
    start = commands.add_parser("start")
    start.add_argument("--mode", choices=("mock", "live"), required=True)
    start.add_argument("--text", default=DEFAULT_TEXT)
    commands.add_parser("status")
    approve = commands.add_parser("approve")
    approve.add_argument("--call-id", required=True)
    reject = commands.add_parser("reject")
    reject.add_argument("--call-id", required=True)
    return cli


def client(args: argparse.Namespace) -> cadence.Client:
    return cadence.Client(
        domain=args.domain,
        target=args.target,
        data_converter=PydanticDataConverter(),
    )


async def run_worker(args: argparse.Namespace) -> None:
    registry = build_registry()
    model_label = "offline-mock"
    if args.mode == "live":
        if not args.confirm_live:
            raise ValueError("live worker requires --confirm-live")
        from live import live_model_activities

        selection = load_model(args.catalog_dir, args.model_id)
        registry = build_registry(live_model_activities(selection, os.environ))
        model_label = selection.id
    worker = Worker(client(args), args.task_list, registry)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    async with worker:
        print(
            f"{args.mode} worker polling domain={args.domain} "
            f"task-list={args.task_list} model={model_label}"
        )
        await asyncio.Event().wait()


async def start(args: argparse.Namespace) -> None:
    cadence_client = client(args)
    try:
        execution = await cadence_client.start_workflow(
            WORKFLOW,
            RecipeInput(
                text=args.text,
                mode=args.mode,
            ),
            task_list=args.task_list,
            workflow_id=args.workflow_id,
            workflow_id_reuse_policy=WORKFLOW_ID_REUSE_POLICY,
            execution_start_to_close_timeout=timedelta(hours=24),
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
        f"started {args.mode} workflow-id={execution.workflow_id} "
        f"run-id={execution.run_id}"
    )


async def status(args: argparse.Namespace) -> None:
    cadence_client = client(args)
    try:
        result = await cadence_client.query_workflow(
            args.workflow_id,
            "",
            STATUS_QUERY,
            result_type=WorkflowStatus,
        )
    finally:
        await cadence_client.close()
    print(json.dumps(asdict(result), indent=2))


async def decide(args: argparse.Namespace) -> None:
    cadence_client = client(args)
    signal = APPROVE_SIGNAL if args.command == "approve" else REJECT_SIGNAL
    try:
        await cadence_client.signal_workflow(
            args.workflow_id,
            "",
            signal,
            args.call_id,
        )
    finally:
        await cadence_client.close()
    print(f"sent {args.command} call-id={args.call_id}")


async def main(argv: Sequence[str] | None = None) -> None:
    cli = parser()
    args = cli.parse_args(argv)
    try:
        if args.command == "worker":
            await run_worker(args)
        elif args.command == "start":
            await start(args)
        elif args.command == "status":
            await status(args)
        else:
            await decide(args)
    except ValueError as error:
        cli.error(str(error))


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("worker stopped")
