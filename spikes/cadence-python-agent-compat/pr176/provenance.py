"""Verify installed VCS provenance and converter bytes against pinned checkout."""

import hashlib
import importlib.metadata as metadata
import json
from pathlib import Path
import subprocess

import cadence.data_converter

SHA = "2bc1af4207d20cd99ae64fa1ef82d803905af947"
dist = metadata.distribution("cadence-python-client")
direct = json.loads(dist.read_text("direct_url.json") or "{}")
assert direct.get("vcs_info", {}).get("commit_id") == SHA, direct
assert direct.get("url") == "https://github.com/cadence-workflow/cadence-python-client.git"
checkout = Path(__file__).parent / ".venv" / "source"
head = subprocess.check_output(["git", "-C", str(checkout), "rev-parse", "HEAD"], text=True).strip()
assert head == SHA
installed = Path(cadence.data_converter.__file__)
expected = checkout / "cadence" / "data_converter.py"
# Git on Windows may change line endings without changing the source.
assert installed.read_text() == expected.read_text()
print("SDK version:", dist.version)
print("VCS provenance:", json.dumps(direct, sort_keys=True))
print("Imported converter:", installed)
print("Converter source matches pinned checkout:", head)
print("Installed converter SHA256:", hashlib.sha256(installed.read_bytes()).hexdigest())
for name in ("openai", "openai-agents", "pydantic", "msgspec", "pytest"):
    print(f"{name}=={metadata.version(name)}")
