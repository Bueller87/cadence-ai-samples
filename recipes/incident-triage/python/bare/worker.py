"""Start a workflow-only or activity-only Worker for durability rehearsals."""

from __future__ import annotations

import argparse
import asyncio
import os

import cadence
from cadence.contrib.pydantic import PydanticDataConverter
from cadence.worker import Worker

from workflow import build_registry


def parser() -> argparse.ArgumentParser:
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--role", choices=("workflow", "activity"), required=True)
    cli.add_argument("--id", required=True)
    cli.add_argument("--target", default="localhost:7833")
    cli.add_argument("--domain", default="cadence-ai-samples")
    cli.add_argument("--task-list", required=True)
    return cli


async def run() -> None:
    args = parser().parse_args()
    os.environ["INCIDENT_TRIAGE_WORKER_ID"] = args.id
    client = cadence.Client(
        domain=args.domain,
        target=args.target,
        identity=args.id,
        data_converter=PydanticDataConverter(),
    )
    options = {
        "identity": args.id,
        "disable_workflow_worker": args.role == "activity",
        "disable_activity_worker": args.role == "workflow",
    }
    async with Worker(client, args.task_list, build_registry(), **options):
        print(
            f"{args.id} polling {args.task_list} as {args.role}",
            flush=True,
        )
        await asyncio.Event().wait()


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass
