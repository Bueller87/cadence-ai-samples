#!/usr/bin/env bash
set -euo pipefail

script_dir=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo_root=$(CDPATH= cd -- "$script_dir/.." && pwd)
explorer_dir="$repo_root/tools/explorer"
explorer_venv="$explorer_dir/.venv"

if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3.10+ is required. Install python3 and its venv support, then rerun this script." >&2
  exit 1
fi
if ! python3 -c 'import sys; sys.exit(sys.version_info < (3, 10))'; then
  echo "Python 3.10+ is required for the explorer." >&2
  exit 1
fi
if [[ ! -x "$explorer_venv/bin/python" ]]; then
  echo "Preparing the explorer's isolated Python environment…"
  if ! python3 -m venv "$explorer_venv"; then
    echo "Could not create a virtual environment. On Debian/Ubuntu, install python3-venv and retry." >&2
    exit 1
  fi
fi
if ! "$explorer_venv/bin/python" -c 'import yaml; assert yaml.__version__ == "6.0.2"' >/dev/null 2>&1; then
  if ! "$explorer_venv/bin/python" -m pip install --disable-pip-version-check -r "$explorer_dir/requirements.txt"; then
    echo "Configured package index failed; retrying with public PyPI as a fallback." >&2
    "$explorer_venv/bin/python" -m pip install --disable-pip-version-check \
      --extra-index-url https://pypi.org/simple -r "$explorer_dir/requirements.txt"
  fi
fi
exec "$explorer_venv/bin/python" "$explorer_dir/server.py" "$@"
