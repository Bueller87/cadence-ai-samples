# Human In The Loop implementation specification

## Problem statement

An AI agent can propose a ride, but a human must authorize the simulated booking
before the tool executes. The pause may last longer than a Worker process, so the
pending proposal and decision must be recoverable from Cadence history.

## Goals and non-goals

### Goals

- Produce a typed ride proposal from a user request.
- Pause durably until a human approves or rejects each pending call.
- Execute the simulated booking Activity only after approval.
- Preserve a representative, provider-free offline path.
- Keep provider calls and tool side effects in Cadence Activities.

### Non-goals

- Real booking, payment, dispatch, or other external side effects.
- A general approval platform or generalized Explorer Signal controls.
- Classifier integration.
- An application-level approval deadline.

## Business scenario and outcomes

The user asks for a ride with pickup, destination, and passenger details. The
agent proposes `book_ride`. The Workflow exposes the safe arguments and call ID,
then waits for a matching signal.

- `APPROVED_COMPLETED`: approval caused the simulated booking Activity to finish.
- `REJECTED`: rejection resumed the agent or deterministic mock path without
  executing the booking Activity.

An unknown or stale call ID has no effect. The first valid decision for a
pending call wins; later duplicate or conflicting signals have no effect.

## Repository implementation profile

Generated from the `python/openai-agents` profile defined by the
[recipe template system specification](https://github.com/cadence-workflow/cadence-ai-samples/blob/main/templates/SPEC-Recipe-Templates.md).

- **Cadence language:** Python
- **Classifier integration:** none
- **Agent integration:** OpenAI Agents
- **Implementation directory:** `python/openai-agents`
- **Runtime catalog use:** only a live Worker reads repository-root
  `models.yaml`; start and control commands remain configuration-free.
- **Live confirmation:** a live Worker requires `--confirm-live` before catalog,
  credential, or provider-client access.
- **Workflow ID reuse:** start uses `ALLOW_DUPLICATE`; a closed ID is reusable,
  while an open duplicate is rejected.
- **Recipe deviation:** the agent loop runs in Workflow code so interruption
  state is durable. The Cadence OpenAI adapter converts each model request into
  an Activity, and the function tool is also a Cadence Activity.

## Workflow architecture

Mock mode schedules a deterministic proposal Activity. Live mode runs the
OpenAI Agents control loop with a logical model name; the Cadence adapter
schedules model Activities that a live Worker resolves to its selected catalog
entry. Both paths normalize proposals into `PendingRide`, publish them through
queries, and wait with `workflow.wait_condition`.

The Workflow records pending call IDs, safe trip arguments, and first decisions.
On replay, completed Activity results and accepted signals are read from history.
The Workflow therefore reconstructs the same wait without repeating completed
model or tool Activities.

There is no approval timer. The Run remains pending until signaled or until its
configured Workflow execution timeout expires. Recovery after timeout requires a
new Run and a new decision.

## Activity and idempotency boundaries

- `human-in-the-loop.mock-proposal` creates fixed offline trip details.
- `OpenAIActivities.invoke_model` performs live model requests with Worker-local
  configuration.
- `human-in-the-loop.book-ride` returns a synthetic receipt and performs no
  external booking.

Workflow state prevents a duplicate or conflicting signal from scheduling the
tool twice for the same call ID. Cadence Activities are at least once, so a real
external booking integration would also require a business idempotency key. The
synthetic Activity must not be treated as an exactly-once integration example.

## Data contract

`RecipeInput` contains only request text and `mock` or `live` mode. Pending
status contains a call ID, tool name, pickup, destination, and passenger count.
`RecipeResult` contains an outcome, mode, safe trip details, and final message.

Model IDs, provider names, endpoints, credentials, raw provider responses, and
provider errors are excluded from Workflow input and query output.

## Configuration

- `--domain`: defaults to `cadence-ai-samples`.
- `--task-list`: defaults to `human-in-the-loop-openai-agents`.
- `--workflow-id`: defaults to `human-in-the-loop-openai-agents-demo`.
- Worker `--catalog-dir`: defaults to the checkout root.
- Worker `--model-id`: selects any compatible repository `models.yaml` entry.
- `MODEL_AI_KEY`: required by Google and OpenAI Workers, unused by mock and local
  Ollama modes.

`models.yaml` is the source of provider, model, and provider-native endpoint
values. Compatible Google, Ollama, and OpenAI entries are not restricted by a
recipe-owned model-ID allowlist. Remote credential-bearing endpoints require
HTTPS, and Ollama endpoints must be loopback.

## Failure behavior

Invalid input and invalid tool arguments fail before booking. Provider failures
are sanitized at the model Activity boundary. An open Workflow ID produces a
clear command error. A Worker may stop while approval is pending; a replacement
Worker on the same task list resumes from history.

## Acceptance and validation

- Offline tests cover proposal and booking Activities, pending queries, both
  decisions, unknown/stale/duplicate/conflicting signals, and booking-at-most-once.
- CLI tests prove mock, start, status, approve, and reject do not read catalogs.
- Provider tests validate compatible catalog entries without network calls and
  prove the selected model and endpoint stay Worker-local.
- Local Cadence checks cover both outcomes, restart while pending, and history
  counts for completed model/tool Activities.
- Explorer discovers the recipe and resolves generic Worker/start commands
  without importing recipe SDK modules.
- Live validation on this machine uses only local `llama3.2-local`. Remote model
  validation is deferred and does not narrow runtime catalog support.
