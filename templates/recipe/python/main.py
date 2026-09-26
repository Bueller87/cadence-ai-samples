"""Worker and starter CLI for __RECIPE_TITLE__."""

import argparse
import asyncio
import logging
import os
from collections.abc import Sequence
from datetime import timedelta
from pathlib import Path

import cadence
from cadence.contrib.pydantic import PydanticDataConverter
from cadence.worker import Worker

from config import CatalogSelection, load_selection
from workflow import WORKFLOW, RecipeInput, build_registry


DEFAULT_TASK_LIST = "__RECIPE_SLUG__"
DEFAULT_WORKFLOW_ID = "__RECIPE_SLUG__-demo"


def parser() -> argparse.ArgumentParser:
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--target", default="localhost:7833")
    cli.add_argument("--domain", default="cadence-ai-samples")
    cli.add_argument("--task-list", default=DEFAULT_TASK_LIST)
    cli.add_argument("--workflow-id", default=DEFAULT_WORKFLOW_ID)
    cli.add_argument("--catalog-dir", type=Path, default=Path(__file__).parents[3])
    cli.add_argument("--agent-id", default="google-adk")
    cli.add_argument("--model-id", default="gemini-flash-lite")
    cli.add_argument("--classifier-id", default="jev-default")

    commands = cli.add_subparsers(dest="command", required=True)
    worker = commands.add_parser("worker")
    worker.add_argument("--mode", choices=("mock", "live"), default="mock")
    worker.add_argument("--confirm-live", action="store_true")
    start = commands.add_parser("start")
    start.add_argument("--mode", choices=("mock", "live"), default="mock")
    start.add_argument("--text", required=True)
    return cli


def client(args: argparse.Namespace) -> cadence.Client:
    return cadence.Client(
        domain=args.domain,
        target=args.target,
        data_converter=PydanticDataConverter(),
    )


async def run_worker(args: argparse.Namespace, selection: CatalogSelection) -> None:
    registry = build_registry()
    if args.mode == "live":
        if not args.confirm_live:
            raise ValueError("live worker requires --confirm-live")
        from live import live_activities

        classifier, model_activities = live_activities(selection, os.environ)
        registry = build_registry(classifier.classify, model_activities)

    worker = Worker(client(args), args.task_list, registry)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    async with worker:
        print(f"{args.mode} worker polling task-list={args.task_list}")
        await asyncio.Event().wait()


async def start(args: argparse.Namespace, selection: CatalogSelection) -> None:
    if args.mode == "live":
        from live import validate_live

        validate_live(selection)

    cadence_client = client(args)
    try:
        execution = await cadence_client.start_workflow(
            WORKFLOW,
            RecipeInput(
                text=args.text,
                mode=args.mode,
                agent_framework=selection.agent.framework,
                model_provider=selection.model.provider,
                model_name=selection.model.model if args.mode == "live" else None,
            ),
            task_list=args.task_list,
            workflow_id=args.workflow_id,
            execution_start_to_close_timeout=timedelta(minutes=10),
            task_start_to_close_timeout=timedelta(seconds=30),
        )
    finally:
        await cadence_client.close()
    print(f"started {args.mode} workflow-id={execution.workflow_id}")


async def main(argv: Sequence[str] | None = None) -> None:
    cli = parser()
    args = cli.parse_args(argv)
    try:
        selection = load_selection(
            args.catalog_dir,
            agent_id=args.agent_id,
            model_id=args.model_id,
            classifier_id=args.classifier_id,
        )
        if selection.classifier.id != "jev-default":
            raise ValueError("only jev-default is implemented")
        if args.command == "worker":
            await run_worker(args, selection)
        else:
            await start(args, selection)
    except ValueError as error:
        cli.error(str(error))


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("worker stopped")
