# Human In The Loop

## Problem

A ride-booking agent can prepare a trip, but a human must authorize the
simulated booking. The approval pause must survive Worker restarts without
repeating completed model work or executing the tool after rejection.

## Solution

The Workflow owns pending call IDs and decisions. It waits durably for
`approve_tool_call` or `reject_tool_call`, then either runs the synthetic booking
Activity or completes as rejected. Mock mode follows the same query and signal
contract without reading catalogs or contacting a provider.

## Architecture

`request → model/proposal Activity → Workflow wait → human Signal → booking or rejection`

In live mode, OpenAI Agents runs its control loop in Workflow code. Cadence's
OpenAI adapter turns each provider request into `OpenAIActivities.invoke_model`;
`human-in-the-loop.book-ride` is a separate Activity. The Worker resolves the
Workflow's logical model name to its selected `models.yaml` entry. Provider,
model, endpoint, and credentials never enter Workflow input or query output.

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
cd recipes/human-in-the-loop/python/openai-agents
python3.13 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

If your default package index requires unavailable credentials, use the public
index for this public project:

```bash
PIP_INDEX_URL=https://pypi.org/simple python -m pip install -e .
```

## Offline mock walkthrough

Start one mock Worker and keep it running:

```bash
python main.py worker --mode mock
```

In another terminal, start an approval run:

```bash
python main.py --workflow-id ride-approve start --mode mock \
  --text "Book a ride from 123 Market Street to 1 Ferry Building."
python main.py --workflow-id ride-approve status
```

Status is JSON. Copy the pending `call_id`, then approve it:

```bash
python main.py --workflow-id ride-approve approve --call-id mock-ride-1
```

Repeat with a different Workflow ID to exercise rejection:

```bash
python main.py --workflow-id ride-reject start --mode mock
python main.py --workflow-id ride-reject status
python main.py --workflow-id ride-reject reject --call-id mock-ride-1
```

The approve Run finishes `APPROVED_COMPLETED`; the reject Run finishes
`REJECTED` without scheduling the booking Activity. Mock mode does not read
`models.yaml`, inspect credentials, or contact an AI provider.

Cadence Web can run the `get_interruptions` query. Its markdown result includes
buttons for the same approve and reject Signals.

### Explorer approval and rejection

The optional recipe-root [`explorer.yaml`](explorer.yaml) declares
`approve_tool_call` and `reject_tool_call` for the Local Samples Explorer. After
starting a Run, use **Open queries** or `python main.py status` to obtain the
pending `call_id`. Resolve the exact current Run in the Explorer, enter that ID
in the rendered field, choose approve or reject, and complete the separate
confirmation step. The Explorer sends the value as the Signal handler's one
positional string argument.

The call ID is submitted in a same-origin POST body. It is not added to Explorer
URLs, logs, saved browser state, durable validation evidence, or sanitized error
text. Cadence-Web acceptance still does not prove the Workflow processed the
decision; confirm the resulting status or history.

## Local Ollama live walkthrough

Install and start Ollama, then make sure the catalog model is available:

```bash
ollama pull llama3.2:latest
curl -fsS http://localhost:11434/api/tags
```

Start the live Worker. No API key is used for local Ollama:

```bash
python main.py worker --mode live --confirm-live \
  --model-id llama3.2-local
```

Start, inspect, and decide a live Run from another terminal:

```bash
python main.py --workflow-id ride-live start --mode live \
  --text "Book a ride from 123 Market Street to 1 Ferry Building for one passenger."
python main.py --workflow-id ride-live status
python main.py --workflow-id ride-live approve --call-id CALL_ID_FROM_STATUS
```

## Other catalog models

Live mode accepts every compatible Google, Ollama, or OpenAI entry in
repository-root `models.yaml`; the recipe has no model-ID allowlist. Google and
OpenAI entries require `MODEL_AI_KEY` and an HTTPS endpoint. Ollama entries
require a loopback endpoint and no key.

```bash
export MODEL_AI_KEY="replace-with-your-key"
python main.py worker --mode live --confirm-live \
  --model-id gemini-flash-lite
```

Live providers may incur cost. Never put a secret in `models.yaml`, command
arguments, Workflow input, or source control. The start and decision commands
intentionally have no model, endpoint, or credential options.

## Restart and Workflow ID behavior

While status shows `WAITING_FOR_DECISION`, stop the Worker with Ctrl-C. Start a
replacement Worker with the same mode, domain, and task list. The status command
returns the same pending call from history, and the replacement Worker processes
the eventual decision. Completed Activities are not called again during replay.

Runs use a 24-hour Workflow execution timeout but no application approval timer.
If that timeout closes a pending Run, start a new Run and obtain a new call ID.

The default ID is `human-in-the-loop-openai-agents-demo` with `ALLOW_DUPLICATE`.
It can be reused after a Run closes. An open duplicate is rejected; wait for it
to close or pass a different `--workflow-id`.

## Reliability and failures

Unknown and stale call IDs are ignored. The first valid decision wins, including
conflicting duplicate Signals. Rejection never executes the booking Activity.
The synthetic Activity demonstrates orchestration, not exactly-once external
side effects; a real booking API would need its own idempotency key.

Unsafe endpoints, missing remote credentials, malformed model responses, empty
input, and unsupported providers fail clearly. Provider errors are sanitized
before entering Workflow history.

## Tests

From `recipes/human-in-the-loop/python/openai-agents`:

```bash
python -m unittest discover -s tests -v
python tests/local_cadence_checks.py
```

The unittest suite is offline. The second command requires local Cadence and
checks both decisions, duplicate/stale behavior, Worker replacement, and history
counts. Repository-level generator and Explorer suites also cover recipe
independence and command discovery.

## Copying this recipe

Copy the entire `recipes/human-in-the-loop` directory and retain the
`models.yaml` catalog convention. Do not import templates or another recipe.
Keep provider calls and tools at Activity boundaries, and replace the synthetic
booking with a real integration only after adding an idempotency key, secret
handling, retry policy, and explicit authorization controls.

Retain `explorer.yaml` only if the copied Workflow keeps the exact approve and
reject Signal names, one positional string `call_id`, and the same confirmation
semantics. Modify and test the declaration with any handler change, or remove it
to keep the copied recipe runnable without Explorer Signal controls. The file
must not contain commands, Workflow IDs, model or provider configuration,
endpoints, credentials, query results, or raw JSON templates.
