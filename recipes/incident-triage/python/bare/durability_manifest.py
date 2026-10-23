"""Validate and summarize the checked-in durability line classification."""

from __future__ import annotations

import argparse
import collections
import json
from pathlib import Path
from typing import Any


CATEGORIES = ("retry", "state", "deduplication")
HERE = Path(__file__).resolve().parent
DEFAULT_MANIFEST = HERE / "durability-lines.json"


def validate_manifest(
    root: Path = HERE, manifest_path: Path = DEFAULT_MANIFEST
) -> dict[str, dict[str, int]]:
    document = json.loads(manifest_path.read_text(encoding="utf-8"))
    entries = document.get("entries")
    if not isinstance(entries, list) or not entries:
        raise ValueError("durability manifest must contain entries")

    occupied: set[tuple[str, int]] = set()
    counts: dict[str, collections.Counter[str]] = {}
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise ValueError(f"entry {index} must be an object")
        file_name = entry.get("file")
        category = entry.get("category")
        start = entry.get("start")
        end = entry.get("end")
        rationale = entry.get("rationale")
        if category not in CATEGORIES:
            raise ValueError(f"entry {index} has invalid category")
        if not isinstance(file_name, str) or Path(file_name).is_absolute():
            raise ValueError(f"entry {index} has invalid file")
        if not isinstance(start, int) or not isinstance(end, int) or start > end:
            raise ValueError(f"entry {index} has invalid line range")
        if not isinstance(rationale, str) or not rationale.strip():
            raise ValueError(f"entry {index} needs a rationale")
        path = (root / file_name).resolve()
        if not path.is_relative_to(root.resolve()) or not path.is_file():
            raise ValueError(f"entry {index} points outside the recipe")
        lines = path.read_text(encoding="utf-8").splitlines()
        if end > len(lines):
            raise ValueError(f"entry {index} extends past {file_name}")
        for line_number in range(start, end + 1):
            key = (file_name, line_number)
            if key in occupied:
                raise ValueError(f"line classified twice: {file_name}:{line_number}")
            text = lines[line_number - 1].strip()
            if not text or text.startswith("#"):
                raise ValueError(
                    f"classified line is blank or a comment: {file_name}:{line_number}"
                )
            occupied.add(key)
            side = "raw" if file_name == "raw_agent.py" else "cadence"
            counts.setdefault(side, collections.Counter())[category] += 1

    return {
        side: {category: counter.get(category, 0) for category in CATEGORIES}
        for side, counter in sorted(counts.items())
    }


def report() -> dict[str, Any]:
    return {
        "method": (
            "Developer-authored orchestration plumbing only. Shared business "
            "logic, tests, docs, imports, blanks, comments, generated code, and "
            "library internals are excluded. Each physical line is counted once."
        ),
        "priority": ["deduplication", "retry", "state"],
        "counts": validate_manifest(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    result = report()
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
        return
    for side, counts in result["counts"].items():
        print(
            f"{side}: "
            + ", ".join(f"{category}={value}" for category, value in counts.items())
        )


if __name__ == "__main__":
    main()
