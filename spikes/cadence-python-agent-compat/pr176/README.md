# Isolated PR #176 experiment

PR: https://github.com/cadence-workflow/cadence-python-client/pull/176
Issue: https://github.com/cadence-workflow/cadence-python-client/issues/173
Pinned head: `2bc1af4207d20cd99ae64fa1ef82d803905af947` (open, unmerged when inspected).

The PR adds a recursive fallback when msgspec cannot compile a type union,
handles nested containers and dataclasses, and drops explicit nulls from
non-nullable optional TypedDict keys. Its tests cover malformed inputs,
variant selection, null handling, and decoder-hook failures. Six changed files
include the converter, benchmark, three unit-test files, and a model/tool/model
integration test. This tests the whole PR head, including its base changes
since v0.4.0; it is not a patch-only causal comparison.

## Actual results

Python 3.13 on Windows. Both sides used OpenAI 2.30.0, OpenAI Agents 0.12.5,
Pydantic 2.12.5, and msgspec 0.21.1.

| Check | Release 0.4.0 | PR head |
|---|---|---|
| Original Activity arguments: string input | FAIL: TypedDict union | PASS |
| Original Activity arguments: message-list input | FAIL: TypedDict union | PASS |
| Original typed ModelResponse result | FAIL: custom-type union | PASS |
| pip check | PASS | PASS |

The constraint protection was also checked with a pip `--dry-run` requesting
`cadence-python-client==0.4.0`: pip returned `ResolutionImpossible` without
installing anything. The normal spike suite remains **17 passed**.

Focused totals: release **3 failed**, PR **3 passed**. These are ordinary test
failures on the release, not xfails or assumed results. On the PR environment,
the three upstream converter unit-test files also produced **101 passed**.

The installed PR package reports `0.4.1.dev12+g2bc1af420`. Its `direct_url.json`
records the GitHub repository and exact commit; `provenance.py` checks both,
checks the source checkout HEAD, and compares the imported converter source
against that checkout (normalizing line endings). Installed converter SHA256:
`4d978a7cbe6efdbd2e24f63699f1be78d94eb856021fa981280f285c1b8f69f9`.

The regression imports the original SDK `OpenAIActivities.invoke_model`
descriptor, serializes its complete argument list, and calls the same
`signature.params_from_payload` boundary used by Cadence's Activity executor.
It separately decodes the declared Activity result type. It never imports our
JSON-safe bridge, constructs a provider client, or executes an Activity body.
Socket connections are forbidden in these tests.

These focused results verify serialization compatibility. The separately
authorized local end-to-end experiment below verifies actual Workflow execution;
it does not test worker-restart replay.

## Local end-to-end results

`run_local_e2e.py` extracts the exact upstream Workflow, `greet` Activity, and
scripted response generator from the pinned checkout using Python's AST
(parsed source definitions). This avoids importing the upstream Docker fixtures.
It adapts only the test infrastructure: the existing Cadence server on
127.0.0.1:7833 and a temporary loopback HTTP server instead of httpx MockTransport.
The original typed `OpenAIActivities.invoke_model` and runner are unchanged.
No app-local JSON-safe bridge is imported. The default Responses API is used,
as in the upstream test; this is not a Chat Completions portability test.

| Evidence | PR commit 2bc1af4207d20cd99ae64fa1ef82d803905af947 | Release 0.4.0 |
|---|---|---|
| Workflow/domain/task list | pr176-e2e-c844fc1c7e88 | pr176-e2e-e4767930c72b |
| Run ID | b8f7beca-27b7-4ba3-84c6-46463b23499a | 38e6b6f0-f4ff-443e-b7e6-8099fe102e8a |
| Workflow status | COMPLETED | FAILED |
| Workflow result | Done. | No result |
| Scheduled Activities | OpenAIActivities.invoke_model, greet, OpenAIActivities.invoke_model | OpenAIActivities.invoke_model |
| Completed / failed Activities | 3 / 0 | 0 / 1 |
| Mock HTTP requests | 2 | 0 |
| E2E assertion result | PASS | FAIL |

The PR Activity sequence was model → `greet` → model, represented in history
by `OpenAIActivities.invoke_model`, `greet`, and
`OpenAIActivities.invoke_model`.

History is fetched with pagination until a terminal event. The test checks
the final result, exact Activity sequence, completed/failed counts, and two
mock requests. It also checks that the second request includes a tool output.
The v0.4.0 failure occurs in Activity argument decoding before its body runs:
`TypeError: Type unions may not contain more than one TypedDict type`.

The mock endpoint and placeholder key are configured solely in the worker
process environment. Model HTTP requests are guarded to permit only the mock's
loopback host and port; tracing is disabled. Inputs contain synthetic text only.
No personal credentials or cloud endpoints are used.

The initial socket-level test guard caused a native Python crash during gRPC
channel construction on Windows/Python 3.13. A minimal unguarded client
readiness probe passed, and scoping the guard to httpx model requests resolved
the crash. This was test-harness behavior and was not established as a Cadence
SDK defect. No SDK or dependency changes were made. Those attempts created no
domains or Workflows.

Both successful-to-start runs reached terminal states. Worker contexts closed,
the HTTP server/thread stopped, and only these two test domains were deprecated.
Histories remain subject to their one-day retention. Existing containers and
spike workers were untouched. The runner now deprecates its domain on cleanup.

### Terminal C: explicit local E2E execution

Use the existing local Cadence server. Each invocation creates its own domain,
task list, Workflow ID, mock server, and temporary worker. No separate mock or
worker terminal is needed. Run from the spike directory:

```powershell
.\pr176\.venv\Scripts\python.exe .\pr176\provenance.py
.\pr176\.venv\Scripts\python.exe -X faulthandler -u .\pr176\run_local_e2e.py --run-local
# Release comparison: nonzero exit and FAILED Workflow are the observed baseline.
.\.venv\Scripts\python.exe -X faulthandler -u .\pr176\run_local_e2e.py --run-local
```

The explicit interpreter chooses the installed SDK; there is no package install
in these commands. `--run-local` is required to prevent accidental test Workflow
creation. Each Workflow has a 30-second execution timeout, and the history wait
is bounded to 50 seconds. End-to-end PASS is limited to this deterministic
mocked model/tool/model path; worker-restart replay and real-provider behavior
remain untested in this PR environment.

## Terminal A: normal release environment

A virtual environment has its own Python interpreter and installed packages.
Using its explicit interpreter path avoids accidentally installing into another
environment; activation is unnecessary. Run from the spike directory:

```powershell
cd C:\path\to\cadence-ai-samples\spikes\cadence-python-agent-compat
.\.venv\Scripts\python.exe -m pytest .\pr176\test_typed_boundary.py -q --tb=short
.\.venv\Scripts\python.exe -m pip check
```

Expect the documented three failures against the release. No packages are
installed or changed in the normal `.venv`.

## Terminal B: PR experiment only

The new environment is `pr176/.venv`; its inspected SDK checkout is inside it
at `pr176/.venv/source`. Both are covered by the existing `.venv/` ignore rule.
The files already exist locally. For a fresh checkout, create them with:

```powershell
cd C:\path\to\cadence-ai-samples\spikes\cadence-python-agent-compat
py -3.13 -m venv .\pr176\.venv
git clone --no-checkout https://github.com/cadence-workflow/cadence-python-client.git .\pr176\.venv\source
git -C .\pr176\.venv\source fetch origin refs/pull/176/head
git -C .\pr176\.venv\source checkout --detach 2bc1af4207d20cd99ae64fa1ef82d803905af947
```

Set a terminal-local pip constraint before installing. It keeps the SDK pinned
if a later pip command tries to install the normal project. That project
requires SDK 0.4.0, so installing it under this constraint must fail resolution
rather than silently replace the PR. Do not install `-e .` in this environment.
No resolver bypass is used.

```powershell
$env:PIP_CONSTRAINT = (Resolve-Path .\pr176\requirements.txt).Path
.\pr176\.venv\Scripts\python.exe -m pip install -r .\pr176\resolved-windows.txt
.\pr176\.venv\Scripts\python.exe .\pr176\provenance.py
.\pr176\.venv\Scripts\python.exe -m pip check
.\pr176\.venv\Scripts\python.exe -m pytest .\pr176\test_typed_boundary.py -q --tb=short
```

`requirements.txt` pins the SDK revision and direct test dependencies;
`resolved-windows.txt` captures every package installed in the tested Windows
environment. The constraint is per terminal, not permanent environment policy:
set it again in a new experiment terminal, and rerun provenance after installs.
Close Terminal B afterward, or remove its setting with
`Remove-Item Env:PIP_CONSTRAINT` before unrelated package work.

To repeat the upstream unit tests using the installed PR package, run from
the spike directory, not from the source checkout (which could shadow it):

```powershell
.\pr176\.venv\Scripts\python.exe -m pytest -c .\pyproject.toml --import-mode=importlib .\pr176\.venv\source\tests\cadence\data_converter_test.py .\pr176\.venv\source\tests\cadence\contrib\test_openai_data_converter.py .\pr176\.venv\source\tests\cadence\contrib\test_pydantic_data_converter.py -q
```

The local pytest config avoids unrelated upstream integration/plugin settings;
the upstream unit-test bodies are unchanged. No credentials, worker terminal,
Cadence server, or model endpoint are required for these commands.
