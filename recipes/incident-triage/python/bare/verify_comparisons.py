"""Verify deterministic counters across retained comparison runs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


HERE = Path(__file__).resolve().parent
DETERMINISTIC_FIELDS = (
    "observed_model_calls",
    "repeated_tool_calls",
    "completed_setup_steps_redone",
    "upload_attempts",
    "duplicate_uploads",
    "final_writes",
    "final_correct",
    "outcome",
    "ledger",
)


def deterministic_view(run: dict[str, Any]) -> dict[str, Any]:
    return {
        side: {field: run[side][field] for field in DETERMINISTIC_FIELDS}
        for side in ("raw", "cadence")
    } | {"durability_lines": run["durability_lines"]}


def verify(root: Path, count: int) -> dict[str, Any]:
    paths = sorted(
        root.glob("run-*/compare.json"),
        key=lambda path: int(path.parent.name.removeprefix("run-")),
    )
    if len(paths) < count:
        raise ValueError(f"need {count} retained runs, found {len(paths)}")
    selected = paths[-count:]
    runs = [json.loads(path.read_text(encoding="utf-8")) for path in selected]
    views = [deterministic_view(run) for run in runs]
    if any(view != views[0] for view in views[1:]):
        raise AssertionError("deterministic counters differ across retained runs")
    wall_times = {
        side: [run[side]["wall_seconds"] for run in runs]
        for side in ("raw", "cadence")
    }
    if any(len(set(values)) == 1 for values in wall_times.values()):
        raise AssertionError("wall-clock values were normalized instead of retained")
    return {
        "runs": [path.parent.name for path in selected],
        "deterministic_counters_equal": True,
        "wall_clock_values_seconds": wall_times,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--evidence", type=Path, default=HERE / ".evidence" / "compare-runs"
    )
    parser.add_argument("--count", type=int, default=3)
    args = parser.parse_args()
    print(json.dumps(verify(args.evidence, args.count), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
