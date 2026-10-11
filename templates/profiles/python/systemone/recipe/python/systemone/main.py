"""Worker and starter CLI for __RECIPE_TITLE__."""

import argparse
import asyncio
import logging
import os
from collections.abc import Sequence
from datetime import timedelta
from pathlib import Path

import cadence
from cadence.api.v1 import workflow_pb2
from cadence.contrib.pydantic import PydanticDataConverter
from cadence.error import WorkflowExecutionAlreadyStartedError
from cadence.worker import Worker

from config import load_classifier
from workflow import WORKFLOW, RecipeInput, build_registry


DEFAULT_TASK_LIST = "__RECIPE_SLUG__-systemone"
DEFAULT_WORKFLOW_ID = "__RECIPE_SLUG__-systemone-demo"
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
    worker.add_argument("--confirm-live", action="store_true")
    worker.add_argument(
        "--catalog-dir", type=Path, default=Path(__file__).parents[4]
    )
    worker.add_argument("--classifier-id", default="laya-local")
    start = commands.add_parser("start")
    start.add_argument("--mode", choices=("mock", "live"), required=True)
    start.add_argument("--text", default="Review this routine example request.")
    return cli


def client(args: argparse.Namespace) -> cadence.Client:
    return cadence.Client(
        domain=args.domain, target=args.target,
        data_converter=PydanticDataConverter(),
    )


def worker_registry(
    args: argparse.Namespace, environ: dict[str, str]
) -> object:
    registry = build_registry()
    if args.mode == "live":
        if not args.confirm_live:
            raise ValueError("live Worker requires --confirm-live")
        from classifier import SystemOneActivities
        selection = load_classifier(args.catalog_dir, args.classifier_id)
        registry = build_registry(
            SystemOneActivities(selection, environ.get("CLASSIFIER_AI_KEY", ""))
        )
    return registry


async def run_worker(args: argparse.Namespace) -> None:
    registry = worker_registry(args, os.environ)
    worker = Worker(client(args), args.task_list, registry)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    async with worker:
        print(f"{args.mode} Worker polling domain={args.domain} task-list={args.task_list}")
        await asyncio.Event().wait()


async def start(args: argparse.Namespace) -> None:
    cadence_client = client(args)
    try:
        execution = await cadence_client.start_workflow(
            WORKFLOW,
            RecipeInput(args.text, args.mode),
            task_list=args.task_list,
            workflow_id=args.workflow_id,
            workflow_id_reuse_policy=WORKFLOW_ID_REUSE_POLICY,
            execution_start_to_close_timeout=timedelta(minutes=10),
            task_start_to_close_timeout=timedelta(seconds=30),
        )
    except WorkflowExecutionAlreadyStartedError as error:
        raise ValueError(
            f"workflow-id={args.workflow_id} already has an open Run; wait for it "
            "to close or pass a different --workflow-id"
        ) from error
    finally:
        await cadence_client.close()
    print(f"started workflow-id={execution.workflow_id} run-id={execution.run_id}")


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
    asyncio.run(main())
