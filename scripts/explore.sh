#!/usr/bin/env bash
set -euo pipefail

script_dir=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo_root=$(CDPATH= cd -- "$script_dir/.." && pwd)
explorer_dir="$repo_root/tools/explorer"
explorer_venv="$explorer_dir/.venv"

python_compatible() {
  "$1" -c 'import sys; sys.exit(sys.version_info < (3, 10))' >/dev/null 2>&1
}

# A prepared environment remains usable when macOS's default python3 is older.
if [[ -x "$explorer_venv/bin/python" ]]; then
  if ! python_compatible "$explorer_venv/bin/python"; then
    echo "The Explorer environment needs Python 3.10+. Remove tools/explorer/.venv and rerun this script with a compatible Python installed." >&2
    exit 1
  fi
else
  explorer_python=
  for candidate in python3 python3.14 python3.13 python3.12 python3.11 python3.10; do
    if command -v "$candidate" >/dev/null 2>&1 && python_compatible "$candidate"; then
      explorer_python=$(command -v "$candidate")
      break
    fi
  done
  if [[ -z "$explorer_python" ]]; then
    echo "Python 3.10+ is required. Install a compatible Python with venv support, then rerun this script." >&2
    exit 1
  fi
fi
if [[ ! -x "$explorer_venv/bin/python" ]]; then
  echo "Preparing the explorer's isolated Python environment…" >&2
  if ! "$explorer_python" -m venv "$explorer_venv"; then
    echo "Could not create a virtual environment. On Debian/Ubuntu, install python3-venv and retry." >&2
    exit 1
  fi
fi
if ! "$explorer_venv/bin/python" -c 'import yaml; assert yaml.__version__ == "6.0.2"' >/dev/null 2>&1; then
  if ! "$explorer_venv/bin/python" -m pip install --disable-pip-version-check -r "$explorer_dir/requirements.txt" >&2; then
    echo "Configured package index failed; retrying with public PyPI as a fallback." >&2
    "$explorer_venv/bin/python" -m pip install --disable-pip-version-check \
      --extra-index-url https://pypi.org/simple -r "$explorer_dir/requirements.txt" >&2
  fi
fi
exec "$explorer_venv/bin/python" "$explorer_dir/server.py" "$@"
