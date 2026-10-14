# Agent Handoffs

## Problem

Trip planning can require multiple specialists: a local ride for a short trip, or
a flight plus airport rides for a long trip. Delegation and completed bookings
must survive Worker restarts without repeating model or tool Activities.

## Solution

A coordinating OpenAI Agents trip planner hands off to short- or long-trip
specialists. Synthetic `book-flight` and `book-ride` tools are Cadence
Activities. Mock mode routes deterministically and never reads catalogs or
contacts a provider.

## Architecture

`request → mock route or live handoffs → synthetic booking Activities → result`

In live mode, OpenAI Agents runs in Workflow code with framework handoffs.
Cadence's OpenAI adapter turns each provider request into
`OpenAIActivities.invoke_model`; booking tools are separate Activities. The
Worker resolves the Workflow's logical model name to its selected `models.yaml`
entry.

## Requirements

- Python 3.12 or 3.13.
- A Cadence server at `localhost:7833` with domain `cadence-ai-samples`.
- Live mode only: a compatible entry in repository-root `models.yaml`.

The dependency pins use Cadence Python client commit
`2bc1af4207d20cd99ae64fa1ef82d803905af947`, OpenAI Agents `0.12.5`, OpenAI
`2.30.0`, Pydantic `2.12.5`, and PyYAML `6.0.2`.

## Fresh-checkout setup

From the repository root:

```bash
cd recipes/agent-handoffs/python/openai-agents
python3.13 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

If your default package index requires unavailable credentials, use the public
index:

```bash
PIP_INDEX_URL=https://pypi.org/simple python -m pip install -e .
```

## Offline mock walkthrough

Start one mock Worker and keep it running:

```bash
python main.py worker --mode mock
```

In another terminal, start a short local trip:

```bash
python main.py --workflow-id handoffs-short start --mode mock \
  --text "Book a ride from 123 Market Street to 1 Ferry Building in San Francisco."
```

Start a long inter-city trip with a different Workflow ID:

```bash
python main.py --workflow-id handoffs-long start --mode mock \
  --text "Book a trip for me from Uber Seattle Office to Uber San Francisco Office tomorrow at 10:00 AM."
```

Mock mode does not read `models.yaml`, inspect credentials, or call an AI or
booking provider.

## Live mode

Live mode runs OpenAI Agents handoffs in Workflow code with Activity-backed model
and booking tools. Google and OpenAI catalog entries require `MODEL_AI_KEY` and
an HTTPS endpoint. The live Worker accepts compatible catalog entries but
**successful specialist handoffs with a real model are not proven for this
recipe** until
[Issue #47](https://github.com/cadence-workflow/cadence-ai-samples/issues/47).

**Do not use `llama3.2-local` expecting working live handoffs here.** Acceptance
runs reached Ollama and invoked the model, but `llama3.2:latest` did not hand off
to specialists or call booking tools. There is no deterministic fallback that
masks that gap. Alternate local Ollama models are tracked in
[Issue #48](https://github.com/cadence-workflow/cadence-ai-samples/issues/48).

Example live Worker with a remote catalog entry (requires authorization and a
key):

```bash
export MODEL_AI_KEY="replace-with-your-key"
python main.py worker --mode live --confirm-live --model-id gemini-flash-lite
```

In another terminal:

```bash
python main.py start --mode live \
  --text "Book a trip from Uber Seattle Office to Uber San Francisco Office tomorrow at 10:00 AM."
```

Worker and start commands must use the same `--domain` and `--task-list`. The
start command has no catalog, model, endpoint, or credential options. For local
Ollama, the Worker sets an internal `OPENAI_API_KEY` compatibility shim for the
OpenAI Agents SDK (same pattern as Human-in-the-Loop); operators do not export a
key for loopback Ollama.

## Workflow ID reuse

The default Workflow ID is `agent-handoffs-openai-agents-demo` with
`ALLOW_DUPLICATE`. After a Run closes, the same ID can start again. An open
duplicate is rejected with a clear error.

## Reliability and failures

Model Activities use a 30-second schedule-to-close timeout and up to three
attempts with exponential backoff. Synthetic booking Activities use the same
30-second timeout and three-attempt retry policy. Authentication, configuration,
and schema errors are non-retryable. Provider error bodies are not copied into
Activity failures.

## Tests

From `recipes/agent-handoffs/python/openai-agents`:

```bash
python -m unittest discover -s tests -v
```

Local Cadence mock durability (requires Cadence at `localhost:7833`):

```bash
python tests/local_cadence_checks.py
```

## Strict live capability check (optional)

`python tests/live_ollama_validation.py` runs live short and long requests against
`llama3.2-local` when Ollama serves `llama3.2:latest` at `localhost:11434` and
Cadence is at `localhost:7833`. The script **must exit non-zero** when the model
omits required specialist handoffs or booking tool Activities. That is the
expected result today for `llama3.2-local` on this recipe.

Issue #16 closure accepts offline tests and local Cadence mock durability as
validated; real-model handoffs remain unproven pending Issue #47.

The unit suite is fully offline except for the optional local scripts above. It
covers both handoff routes, live orchestration with mocked agent runs, invalid
input, Activity failures, configuration isolation, and scheduling counts.

## Copying this recipe

Copy the entire `recipes/agent-handoffs` directory and keep provider clients and
model requests at Activity boundaries. Replace trip parsing, handoff instructions,
and synthetic booking fixtures for your domain.
