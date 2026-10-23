# Incident Triage implementation specification

## Problem statement

Ordinary in-process agent loops lose progress when a process exits, keep a
process alive while waiting for a person, and require handwritten retry,
checkpoint, and deduplication behavior. The recipe must make that failure mode
visible without weakening the raw implementation or claiming that orchestration
makes synthetic work faster.

## Goals and non-goals

### Goals

- Run one relatable synthetic checkout incident through equivalent raw asyncio
  and Cadence paths.
- Inject the same HTTP 429, loop, changed brief, wrong answer, approval wait,
  and process or Worker interruption at matching logical points.
- Record observed wall time, model-call ledger entries, repeated tool calls,
  setup work redone, writes, duplicates, waiting time, zero-worker time, and
  auditable retry/state/deduplication line counts.
- Provide offline tests plus a separate local-Cadence rehearsal and five bounded
  endings.

### Non-goals

- Mock mode, the rehearsal, the variants, and the comparison do not call a
  model provider or invent token prices.
- Live mode calls one catalog model from the existing plan and revise
  activities and prints reported usage. It does not replace the scripted
  inventory wrong turn.
- No production data, autonomous remediation, or real paging, ticketing, or
  monitoring system.
- No claim that Cadence is faster or that an Activity executes exactly once.
- No OpenAI Agents or Google ADK dependency in this slice.

## Business scenario

An on-call engineer receives one failed checkout trace. The agent reads gateway
logs and a dependency graph, follows downstream errors, accepts operator
guidance and a changed output brief, waits for approval, and writes a conclusion
under a stable idempotency key.

## Repository implementation profile

Generated from the `python/bare` profile defined by the
[recipe template system specification](https://github.com/cadence-workflow/cadence-ai-samples/blob/main/templates/SPEC-Recipe-Templates.md).

- **Cadence language:** Python
- **Classifier integration:** none
- **Agent integration:** none
- **Implementation directory:** `python/bare`
- **Mock and live behavior:** Mock runs the scripted planner, including the
  inventory wrong turn modeled on the real run. Live reads `models.yaml` and
  calls that model from the plan and revise activities. The default live model
  is `llama3.2-local`. Start does not select a model.
- **Runtime catalog use:** Only the live Worker reads repository-root
  `models.yaml`.
- **Workflow ID reuse:** `start` uses the stable demo Workflow ID
  `incident-triage-demo` with `ALLOW_DUPLICATE`. A new Run may start after the
  previous Run closes; a start while a Run is open is rejected and reported.
- **Profile deviations:** The generic CLI remains exactly `worker|start` with
  `mock|live`. Separate recipe-local scripts run comparison, rehearsal, and
  variants because those are acceptance evidence, not Explorer actions.

## Language and dependencies

- **Language:** Python
- **Runtime version:** Python 3.12 or 3.13
- **Dependencies:** `cadence-python-client` 0.4.1 (published release) for the
  Worker, client, and offline test environment; `pydantic` for the data
  converter.

## Workflow architecture

1. Parse and store the synthetic trace in Activities.
2. Read gateway logs and the dependency graph concurrently.
3. Run the scripted planner with a repeated-call guard and bounded step count.
4. Expose the wrong first draft and accept `revise` and `guidance` Signals.
5. Replan only the agent hops while retaining completed setup in Workflow
   history, then reapply the changed brief to the corrected draft.
6. Wait on an approval Signal or durable timer.
7. Write the approved conclusion in an idempotent Activity.

All state transitions are deterministic Workflow code. Fixture I/O, ledger I/O,
SQLite writes, wall-clock timestamps, sleeps that model tool latency, and
scripted model calls are Activities.

## AI model or agent responsibilities

The deterministic `story.plan_next` function stands in for an agent planner. It
selects the next log tool call and drafts a conclusion. It intentionally prefers
one misleading warning until operator guidance points it at the payment error.
The script makes the orchestration behavior reproducible; it is not a model
quality or usage benchmark.

## Activity boundaries

- `identify-input`: parse the fixture.
- `store-trace`: record the input under `<workflow-id>/<run-id>/input`.
- `get-logs`: read one service fixture and exercise retry/fault behavior.
- `fetch-graph`: read the dependency fixture.
- `plan-next`: execute one scripted planner call.
- `revise-draft`: apply one changed-brief request.
- `upload-conclusion`: write once under `<workflow-id>/<run-id>/conclusion`; a retry
  returns that Run's stored receipt.

## Input and output schemas

### Workflow input

```json
{
  "text": "{\"endpoint_id\":\"post-v1-checkout\", ...}",
  "limits": {
    "max_steps": 6,
    "repeat_limit": 2,
    "approval_window_seconds": 259200
  }
}
```

### Workflow output

```json
{
  "outcome": "conclusion_uploaded",
  "conclusion": {
    "call_path": ["api-gateway", "checkout", "payments", "ledger"],
    "root_cause": "payments v2.14 exhausted its ledger connection pool (50 of 50)"
  },
  "revision": 2,
  "receipt": "receipt-<workflow-id>-conclusion"
}
```

## Configuration

- Generic target: `localhost:7833`.
- Domain: `cadence-ai-samples`.
- Task list: `incident-triage`.
- Workflow ID: `incident-triage-demo`.
- `INCIDENT_TRIAGE_STATE` is an integration-harness-only path for synthetic
  ledgers, faults, and SQLite state.
- No credentials or catalogs are read.

## Failure handling and retry behavior

Activities have bounded schedule-to-close timeouts and at most three attempts
with exponential backoff. `NonRetryableHttpError` includes synthetic 400, 401,
and 404 responses. A synthetic 429 is retryable. The final write is keyed and
returns its first receipt if a reply is lost after commit.

Killing every Worker does not discard Workflow state or the durable approval
timer. The rehearsal verifies that no Activity runs during the zero-worker
interval and that the final write occurs on the replacement Activity Worker.

## Synthetic test data

`python/bare/fixtures` contains one fictional trace, dependency graph, dashboard
context, and service logs for api-gateway, checkout, inventory, payments, and
ledger. The data is labeled synthetic and contains no credentials, private
URLs, people, customers, or deployed-system claims.

## Tests and acceptance criteria

- Offline unit and command tests require no server or key.
- Explorer discovers `recipes/incident-triage/python/bare`, both modes, both
  generic actions, and the declared Signals.
- The local rehearsal proves 429 recovery, bounded looping, changed-brief
  replay, guidance without setup re-execution, a zero-worker durable wait, a
  correct final conclusion, and one keyed write.
- Five variants prove expiration, rejection, graph 404 classification, budget
  exhaustion, and lost-reply write idempotency.
- Three fresh comparisons retain wall-time variation and match deterministic
  counters.
- `durability-lines.json` passes twice and classifies each physical line at most
  once under the issue's shared rules.
- A clean-copy run proves the recipe does not depend on another recipe.
- The Python 3.13 runtime-launcher suite passes. The optional Explorer DOM check
  discovers this recipe, then hits the same `refresh checks edited catalog`
  timeout on unmodified base commit `c0d4b33`; this is retained as a visible
  baseline limitation rather than hidden or attributed to this recipe.

## Definition of done

- [x] The recipe is independently runnable.
- [x] The selected implementation profile and deviations are documented.
- [x] Setup and execution instructions are complete.
- [x] Workflow ID reuse behavior is explicit and tested.
- [x] Workflow and Activity behavior has offline coverage.
- [x] Synthetic fixtures cover the demonstrated paths.
- [x] Reliability and evidence-provenance decisions are documented.
- [x] No credentials, proprietary data, or dependencies on other recipes are
  included.
- [x] Fresh comparison, Explorer discovery, clean-copy, runtime-launcher, and
  recipe-isolation checks pass; the reproduced base-branch DOM limitation is
  documented above.
