"""Small CLI for the Recurring AI Watch sample."""

from __future__ import annotations

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
from workflow import (
    CHECK_NOW_SIGNAL,
    STOP_WATCH_SIGNAL,
    WATCH_STATUS_QUERY,
    WATCH_WORKFLOW,
    WatchInput,
    WatchStatus,
    build_registry,
)


DEFAULT_TASK_LIST = "recurring-ai-watch"
DEFAULT_WORKFLOW_ID = "recurring-ai-watch-demo"


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
    start.add_argument("--interval", type=_positive, default=15)
    commands.add_parser("status")
    commands.add_parser("check-now")
    commands.add_parser("stop")
    return cli


def _positive(value: str) -> int:
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("interval must be greater than zero")
    return number


def format_status(status: WatchStatus) -> str:
    update = status.latest_update.version if status.latest_update else "none"
    return "\n".join(
        (
            f"state: {status.state}",
            f"check count: {status.check_count}",
            f"latest update: {update}",
            f"latest report: {status.latest_report or 'none'}",
        )
    )


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
        print(f"{args.mode} worker polling task-list={args.task_list} "
              f"agent={selection.agent.id} model={selection.model.id}")
        await asyncio.Event().wait()


async def start(args: argparse.Namespace, selection: CatalogSelection) -> None:
    if args.mode == "live":
        from live import validate_live

        validate_live(selection)
    cadence_client = client(args)
    try:
        execution = await cadence_client.start_workflow(
            WATCH_WORKFLOW,
            WatchInput(
                interval=timedelta(seconds=args.interval),
                mode=args.mode,
                model_name=selection.model.model if args.mode == "live" else None,
                agent_framework=selection.agent.framework,
                model_provider=selection.model.provider,
            ),
            task_list=args.task_list,
            workflow_id=args.workflow_id,
            execution_start_to_close_timeout=timedelta(days=365),
            task_start_to_close_timeout=timedelta(seconds=30),
        )
    finally:
        await cadence_client.close()
    print(f"started {args.mode} watch workflow-id={execution.workflow_id}")


async def status(args: argparse.Namespace) -> None:
    cadence_client = client(args)
    try:
        result = await cadence_client.query_workflow(
            args.workflow_id, "", WATCH_STATUS_QUERY, result_type=WatchStatus
        )
    finally:
        await cadence_client.close()
    print(format_status(result))


async def signal(args: argparse.Namespace, name: str) -> None:
    cadence_client = client(args)
    try:
        await cadence_client.signal_workflow(args.workflow_id, "", name)
    finally:
        await cadence_client.close()
    print(f"sent {name} to workflow-id={args.workflow_id}")


async def main(argv: Sequence[str] | None = None) -> None:
    cli = parser()
    args = cli.parse_args(argv)
    try:
        if args.command in {"worker", "start"}:
            selection = load_selection(
                args.catalog_dir,
                agent_id=args.agent_id,
                model_id=args.model_id,
                classifier_id=args.classifier_id,
            )
            if selection.classifier.id != "jev-default" or selection.classifier.provider != "typesafe":
                raise ValueError("only jev-default is implemented; other classifiers are catalog candidates")
        if args.command == "worker":
            await run_worker(args, selection)
        elif args.command == "start":
            await start(args, selection)
        elif args.command == "status":
            await status(args)
        else:
            await signal(
                args,
                CHECK_NOW_SIGNAL if args.command == "check-now" else STOP_WATCH_SIGNAL,
            )
    except ValueError as error:
        cli.error(str(error))


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("worker stopped")
