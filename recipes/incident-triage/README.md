# Incident Triage

This recipe follows one fictional checkout failure twice. First, a capable
plain-asyncio agent loses its in-memory work after a process interruption.
Then the same synthetic incident, scripted planner, tools, guidance, changed
brief, approval, and fault schedule run as a Cadence Workflow.

The teaching result is preserved state and less repeated work. It is not a
speed, token, cost, or exactly-once claim.

## What the agent does

The agent starts from one failed API trace. It reads gateway logs and a
dependency graph, follows errors through checkout, payments, and ledger, accepts
operator guidance and a changed output brief, waits for approval, and writes one
keyed conclusion.

Mock mode uses a deterministic synthetic planner. That planner keeps the
inventory wrong turn from the real run. Live mode calls a local model. No paid
API or production data is used.

## Requirements

- Python 3.12 or 3.13.
- The published `cadence-python-client` 0.4.1 release.
- Cadence at `localhost:7833` with domain `cadence-ai-samples` for Worker,
  rehearsal, variant, and comparison commands.

## Setup and offline tests

From this directory:

```bash
./run.sh setup
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python durability_manifest.py
.venv/bin/python check_clean_copy.py
```

The tests and line-manifest check are offline.

## Run through the generic recipe interface

Terminal 1:

```bash
./run.sh worker --mode mock
```

Terminal 2:

```bash
./run.sh start --mode mock
```

`mock` keeps the scripted planner. `live` calls catalog model `llama3.2-local`
from the plan and revise activities. Ollama must be running at
`http://localhost:11434` with `llama3.2:latest` pulled. Set
`INCIDENT_TRIAGE_MODEL_ID` on the Worker to select another catalog model. With
Ollama, each request carries a JSON schema that allows only the fixture's
services and bounded text. It also requires a lookup before the first draft,
skips services already looked up, and blocks drafting while an ERROR line
names a service nobody has looked at. The model still chooses which service to
check next. Each model activity
prints one `SPEND` line: reported input and output tokens, a running total, and
an external API charge of $0. Local compute is unpriced. A missing token count
stays unavailable.

```bash
./run.sh worker --mode live
./run.sh start --mode live
```

The comparison command stays on the scripted planner and prints the raw asyncio
column beside local Cadence.

The Workflow waits first for a `guidance` Signal and later for an `approval`
Signal. `explorer.yaml` exposes guidance, revision, and approval controls in the
Local Samples Explorer.

## Retry-policy exercise

[`python/bare/exercises/retry_policy_live`](python/bare/exercises/retry_policy_live/README.md)
runs this Workflow with a real model and prints each Cadence Web link. It adds
a temporary 429, a permanent 404, an injected repeated call, and a revise and
guidance pass. The one edit is the gateway lookup's retry policy.

## Run the durability rehearsal

```bash
.venv/bin/python run_rehearsal.py --evidence .evidence/rehearsal
```

The self-checking rehearsal injects one HTTP 429, bounds the tool loop, changes
the brief, corrects the wrong answer with guidance, kills every Worker during
the approval wait, restarts Workers, and approves the conclusion. It fails
unless setup Activities remain recorded, no Activity runs during the
zero-worker interval, the final answer is correct, and one keyed write occurs.

Run the five bounded endings:

```bash
.venv/bin/python run_variants.py --evidence .evidence/variants.json
```

They cover approval expiration, rejection, a nonretryable graph 404, planning
budget exhaustion, and a committed write whose first reply is lost.

## Compare raw asyncio with Cadence

```bash
.venv/bin/python run_compare.py
.venv/bin/python verify_comparisons.py --count 3
```

The command runs raw first and then the local Cadence rehearsal. It prints one
human-readable table and writes a machine-readable record under
`.evidence/compare-runs/`. The checked-in
[`evidence/comparison-baseline.json`](evidence/comparison-baseline.json) is a
summarized baseline from three runs. It keeps the command, runtime, and
deterministic counters. It does not keep per-run ledgers or process output.

Both paths use six planner steps and a repeated-call limit of two. Expected
deterministic counters for this fault schedule are:

- observed scripted model calls: raw 16, Cadence 11;
- repeated tool calls: raw 14, Cadence 3;
- completed setup steps redone after interruption: raw 8, Cadence 0;
- upload attempts: one on each path;
- observed final writes: one committed write on each path;
- duplicate uploads: zero on each path.

Wall time is measured, retained, and expected to vary. Local Cadence is slower
in the current short scripted harness because orchestration overhead dominates
subsecond synthetic work. The table says so. No fixed per-call multiplier is
reported as tokens or cost. The useful provenance is the observed `plan_next`
and `revise_draft` ledger entries.

## Fairness and line counts

The raw baseline has the same step limit and repeated-call guard as the Cadence
workflow, plus bounded retry with backoff, exception handling, an in-process
changed-brief task, in-memory state, process-local write deduplication, and a
live approval wait. It is not deliberately weakened.

`durability-lines.json` classifies retry, state, and deduplication lines in
`raw_agent.py` and `workflow.py` using the same rules. Every entry includes its
file, line range, category, and rationale; the validator rejects overlaps,
comments, blank lines, invalid ranges, and paths outside the recipe. These
counts describe handwritten orchestration plumbing, not total code size or SDK
internals. They are evidence, not a promise that every Cadence example has fewer
source lines.

## Workflow ID reuse

`start` uses `incident-triage-demo` with `ALLOW_DUPLICATE`. A new Run may reuse
the ID after the previous Run closes. Cadence rejects a duplicate while a Run is
open, and the CLI reports that condition. Use another `--workflow-id` when you
want concurrent runs. Stored input and the conclusion receipt include the Run
ID, so the new Run does not read the earlier Run's rows.

## Copying the recipe

Copy the complete `recipes/incident-triage` directory. The implementation does
not import another recipe or repository-only runtime code. Keep nondeterministic
work and side effects in Activities, preserve the idempotency key on the final
write, and replace the synthetic fixtures only with data safe for your
application.

If the launcher cannot prepare the environment, run:

```bash
python3.13 -m venv .venv
.venv/bin/python -m pip install -e .
```
