"""Copy only this recipe and rerun its offline checks."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path


HERE = Path(__file__).resolve().parent
RECIPE_ROOT = HERE.parents[1]


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="incident-triage-clean-copy-") as temp:
        destination = Path(temp) / "incident-triage"
        shutil.copytree(
            RECIPE_ROOT,
            destination,
            ignore=shutil.ignore_patterns(
                ".venv",
                ".evidence",
                ".run-state",
                ".state",
                "__pycache__",
                "*.egg-info",
            ),
        )
        implementation = destination / "python" / "bare"
        environment = {
            **os.environ,
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        setup = subprocess.run(
            ["./run.sh", "setup"],
            cwd=implementation,
            env=environment,
            capture_output=True,
            text=True,
            timeout=180,
        )
        if setup.returncode:
            raise RuntimeError(setup.stdout + setup.stderr)
        python = implementation / ".venv" / "bin" / "python"
        commands = (
            [str(python), "-m", "unittest", "discover", "-s", "tests", "-v"],
            [str(python), "durability_manifest.py", "--json"],
        )
        for command in commands:
            result = subprocess.run(
                command,
                cwd=implementation,
                env=environment,
                capture_output=True,
                text=True,
                timeout=120,
            )
            if result.returncode:
                raise RuntimeError(result.stdout + result.stderr)
        print("CLEAN COPY PASSED: copied setup, tests, and manifest")


if __name__ == "__main__":
    main()
