"""Worker and starter CLI for __RECIPE_TITLE__."""

import argparse
import asyncio
import logging
from collections.abc import Sequence
from datetime import timedelta

import cadence
from cadence.api.v1 import workflow_pb2
from cadence.contrib.pydantic import PydanticDataConverter
from cadence.error import WorkflowExecutionAlreadyStartedError
from cadence.worker import Worker

from workflow import WORKFLOW, RecipeInput, build_registry


DEFAULT_TASK_LIST = "__RECIPE_SLUG__"
DEFAULT_WORKFLOW_ID = "__RECIPE_SLUG__-demo"
DEFAULT_TEXT = "Investigate this example request."
# A closed demo Run may be replaced; Cadence still rejects a start while a Run is open.
WORKFLOW_ID_REUSE_POLICY = workflow_pb2.WORKFLOW_ID_REUSE_POLICY_ALLOW_DUPLICATE


def parser() -> argparse.ArgumentParser:
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--target", default="localhost:7833")
    cli.add_argument("--domain", default="cadence-ai-samples")
    cli.add_argument("--task-list", default=DEFAULT_TASK_LIST)
    cli.add_argument("--workflow-id", default=DEFAULT_WORKFLOW_ID)

    commands = cli.add_subparsers(dest="command", required=True)
    commands.add_parser("worker")
    start = commands.add_parser("start")
    start.add_argument("--text", default=DEFAULT_TEXT)
    return cli


def client(args: argparse.Namespace) -> cadence.Client:
    return cadence.Client(
        domain=args.domain,
        target=args.target,
        data_converter=PydanticDataConverter(),
    )


async def run_worker(args: argparse.Namespace) -> None:
    worker = Worker(client(args), args.task_list, build_registry())
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    async with worker:
        print(f"worker polling domain={args.domain} task-list={args.task_list}")
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
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("worker stopped")
