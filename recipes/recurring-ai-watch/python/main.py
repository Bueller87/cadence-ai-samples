r"""CLI for the mock-first Recurring AI Watch sample.

PowerShell examples:
  python .\main.py worker
  python .\main.py start
  python .\main.py status
  python .\main.py check-now
  python .\main.py stop
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
from collections.abc import MutableMapping
from datetime import timedelta
from pathlib import Path
from typing import Sequence

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


DEFAULT_DOMAIN = "default"
DEFAULT_TARGET = "localhost:7833"
DEFAULT_TASK_LIST = "recurring-ai-watch"
DEFAULT_WORKFLOW_ID = "recurring-ai-watch-demo"


def default_catalog_dir() -> Path:
    """Locate this repository's root catalogs; portable callers may override it."""

    return Path(__file__).resolve().parents[3]


def positive_seconds(value: str) -> int:
    seconds = int(value)
    if seconds <= 0:
        raise argparse.ArgumentTypeError("interval must be greater than zero")
    return seconds


def parser() -> argparse.ArgumentParser:
    command_parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    command_parser.add_argument("--target", default=DEFAULT_TARGET)
    command_parser.add_argument("--domain", default=DEFAULT_DOMAIN)
    command_parser.add_argument("--task-list", default=DEFAULT_TASK_LIST)
    command_parser.add_argument("--workflow-id", default=DEFAULT_WORKFLOW_ID)
    command_parser.add_argument("--catalog-dir", type=Path, default=default_catalog_dir())
    command_parser.add_argument("--agent-id", default="google-adk")
    command_parser.add_argument("--model-id", default="gemini-flash-lite")
    command_parser.add_argument("--classifier-id", default="jev-default")

    commands = command_parser.add_subparsers(dest="command", required=True)
    worker = commands.add_parser("worker", help="start a mock or explicitly live Worker")
    worker.add_argument("--mode", choices=("mock", "live"), default="mock")
    worker.add_argument("--confirm-live", action="store_true")

    start = commands.add_parser("start", help="start a mock Watch")
    start.add_argument("--interval", type=positive_seconds, default=15)
    start.add_argument("--mode", choices=("mock", "live"), default="mock")

    commands.add_parser("status", help="query the current Watch state")
    commands.add_parser("check-now", help="send the check-now Signal")
    commands.add_parser("stop", help="send the stop-watch Signal")
    return command_parser


def resolve_catalog_selection(args: argparse.Namespace) -> CatalogSelection:
    return load_selection(
        args.catalog_dir,
        agent_id=args.agent_id,
        model_id=args.model_id,
        classifier_id=args.classifier_id,
    )


def format_status(status: WatchStatus) -> str:
    update = status.latest_update.version if status.latest_update else "none"
    report = status.latest_report or "none"
    return "\n".join(
        (
            f"state: {status.state}",
            f"check count: {status.check_count}",
            f"latest update: {update}",
            f"latest report: {report}",
        )
    )


def build_client(args: argparse.Namespace) -> cadence.Client:
    return cadence.Client(
        domain=args.domain,
        target=args.target,
        data_converter=PydanticDataConverter(),
    )


def worker_registry(
    args: argparse.Namespace,
    selection: CatalogSelection,
    environ: MutableMapping[str, str] = os.environ,
):
    if args.mode == "mock":
        return build_registry()
    if not args.confirm_live:
        raise ValueError("live worker requires --confirm-live")

    from live import configure_live_worker

    classifier, model_activities = configure_live_worker(selection, environ)
    return build_registry(
        mode="live",
        live_classifier=classifier,
        live_model_activities=model_activities,
    )


async def run_worker(args: argparse.Namespace, selection: CatalogSelection) -> None:
    registry = worker_registry(args, selection)
    client = build_client(args)
    worker = Worker(client, args.task_list, registry)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    async with worker:
        print(
            f"{args.mode} worker polling "
            f"domain={args.domain} task-list={args.task_list} "
            f"agent={selection.agent.id} model={selection.model.id} "
            f"classifier={selection.classifier.id}"
        )
        await asyncio.Event().wait()


async def start_watch(args: argparse.Namespace) -> None:
    selection = resolve_catalog_selection(args)
    if args.mode == "live":
        from live import validate_live_selection

        validate_live_selection(selection)
    client = build_client(args)
    try:
        execution = await client.start_workflow(
            WATCH_WORKFLOW,
            WatchInput(
                interval=timedelta(seconds=args.interval),
                mode=args.mode,
                model_name=selection.model.model if args.mode == "live" else None,
            ),
            task_list=args.task_list,
            workflow_id=args.workflow_id,
            execution_start_to_close_timeout=timedelta(days=365),
            task_start_to_close_timeout=timedelta(seconds=30),
        )
    finally:
        await client.close()

    print(
        f"started {args.mode} watch "
        f"workflow-id={execution.workflow_id} run-id={execution.run_id}"
    )


async def show_status(args: argparse.Namespace) -> None:
    client = build_client(args)
    try:
        status = await client.query_workflow(
            args.workflow_id,
            "",
            WATCH_STATUS_QUERY,
            result_type=WatchStatus,
        )
    finally:
        await client.close()

    print(format_status(status))


async def send_signal(args: argparse.Namespace, signal_name: str) -> None:
    client = build_client(args)
    try:
        await client.signal_workflow(args.workflow_id, "", signal_name)
    finally:
        await client.close()

    print(f"sent {signal_name} to workflow-id={args.workflow_id}")


async def main(argv: Sequence[str] | None = None) -> None:
    command_parser = parser()
    args = command_parser.parse_args(argv)
    try:
        selection = resolve_catalog_selection(args)
    except ValueError as error:
        command_parser.error(str(error))

    try:
        if args.command == "worker":
            await run_worker(args, selection)
        elif args.command == "start":
            await start_watch(args)
        elif args.command == "status":
            await show_status(args)
        elif args.command == "check-now":
            await send_signal(args, CHECK_NOW_SIGNAL)
        elif args.command == "stop":
            await send_signal(args, STOP_WATCH_SIGNAL)
    except ValueError as error:
        command_parser.error(str(error))


def run_cli() -> None:
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("worker stopped")


if __name__ == "__main__":
    run_cli()
