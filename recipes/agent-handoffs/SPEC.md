# Agent Handoffs implementation specification

## Problem statement

Multi-agent trip planning needs durable delegation. A coordinating agent must
hand off to specialists, complete synthetic bookings as Activities, and survive
Worker restarts without repeating completed model or booking work.

## Goals and non-goals

### Goals

- Route short local trips to one ride specialist and long inter-city trips to a
  flight plus airport-ride specialist.
- Preserve framework-native OpenAI Agents and Google ADK handoffs in live mode
  with Activity-backed model calls and synthetic booking tools.
- Provide deterministic mock runs for both routes without catalogs, credentials,
  or provider access.
- Keep model selection, endpoints, credentials, and raw provider failures
  Worker-local.

### Non-goals

- Real flight, ride, payment, mapping, or reservation integrations.
- Classifier integration or a shared handoff runtime across recipes.
- Upstream sample layout or CLI compatibility.
- Remote or paid inference as core acceptance evidence.

## Business scenario and outcomes

The user describes a trip with pickup and destination text. A coordinating trip
agent delegates to:

- a short-trip specialist that books one synthetic local ride; or
- a long-trip specialist that books a synthetic flight and synthetic rides
  connecting local addresses to airports.

Mock mode uses deterministic parsing and routing. Live mode uses the same trip
intent with the selected framework's native handoff primitive. All receipts are
labeled synthetic.

## Repository implementation profile

Generated from the `python/openai-agents` and `python/google-adk` profiles
defined by the
[recipe template system specification](https://github.com/cadence-workflow/cadence-ai-samples/blob/main/templates/SPEC-Recipe-Templates.md).

- **Cadence language:** Python
- **Classifier integration:** none
- **Agent integrations:** OpenAI Agents and Google ADK
- **Implementation directories:** `python/openai-agents` and `python/google-adk`
- **Runtime catalog use:** only a live Worker reads repository-root
  `models.yaml`; start remains configuration-free.
- **Live confirmation:** a live Worker requires `--confirm-live` before catalog,
  credential, or provider-client access.
- **Workflow ID reuse:** each implementation uses its framework-specific demo ID
  with `ALLOW_DUPLICATE`; a closed ID is reusable, while an open duplicate is
  rejected.
- **Recipe deviation:** both agent control loops and handoffs run in Workflow
  code. The Cadence adapters schedule model requests as Activities, and booking
  tools are separate Activities.

## Workflow architecture

Mock mode schedules `agent-handoffs.mock-route`, then books through typed
Activities based on the returned route. Live mode runs a coordinator with
handoffs to short- and long-trip specialists. OpenAI Agents uses `handoffs`;
Google ADK uses `sub_agents` and its injected `transfer_to_agent` tool. Both map
specialist tools to the same booking Activity names on separate task lists.

The Workflow records mode, route, and completion state. Replay reads completed
Activity results from history, so finished route, model, and booking Activities
are not intentionally rescheduled.

## Activity and idempotency boundaries

- `agent-handoffs.mock-route`: deterministic offline parsing and routing.
- `agent-handoffs.book-flight`: synthetic flight receipt, no airline contact.
- `agent-handoffs.book-ride`: synthetic ride receipt, no dispatch contact.
- `OpenAIActivities.invoke_model`: live model requests with Worker-local
  configuration.
- `GoogleADKActivities.generate_content_async`: Google ADK model requests with
  Worker-local configuration.

Cadence Activities are at least once. Synthetic bookings use fixed fixtures and
document that external integrations would need business idempotency keys.

## Data contract

`RecipeInput` contains request text and `mock` or `live` mode. `RecipeResult`
contains route (`SHORT_TRIP` or `LONG_TRIP`), the specialist agent name chosen by
handoff, mode, answer text, optional flight receipt, and ride receipts collected
from live tool Activities. Model IDs, provider names, endpoints, credentials,
and raw provider responses are excluded from Workflow input and result fields.

## Short-trip and long-trip contracts

**Short trip:** request text resolves to one local ride from a parsed start
address to a parsed destination. Mock mode schedules one `book-ride` Activity.

**Long trip:** request text resolves to inter-city travel (for example Seattle
and San Francisco in the same request, or an explicit flight). Mock mode
schedules one `book-flight` Activity and two `book-ride` Activities connecting
local addresses to airports.

Invalid or unparseable requests fail before booking Activities run.

## Configuration

- `--domain`: defaults to `cadence-ai-samples`.
- `--task-list`: defaults to `agent-handoffs-openai-agents` or
  `agent-handoffs-google-adk`.
- `--workflow-id`: defaults to the corresponding
  `agent-handoffs-<framework>-demo`.
- Worker `--catalog-dir`: defaults to the checkout root.
- Worker `--model-id`: selects a supported repository `models.yaml` entry.
- `MODEL_AI_KEY`: required by Google and OpenAI Workers, unused by mock and local
  Ollama modes.

Catalog entries compatible with the live Worker include `gemini-flash-lite`,
`llama3.2-local`, and `openai-nano`. Successful specialist handoffs and booking
tool calls with a real model are not proven for this recipe until
[Issue #47](https://github.com/cadence-workflow/cadence-ai-samples/issues/47).
Evaluation of alternate local Ollama models is tracked in
[Issue #48](https://github.com/cadence-workflow/cadence-ai-samples/issues/48).

## Failure behavior

Invalid input fails before booking. Provider failures are sanitized at the model
Activity boundary. Booking Activity failures use bounded retries. An exhausted
Activity failure fails the Workflow for operator inspection.

## Booking tool timeouts and retries

`book-flight` and `book-ride` use a 30-second schedule-to-close timeout and up to
three attempts with exponential backoff starting at one second.

## Acceptance and validation

### Validated OpenAI Agents implementation

- Offline unit tests and mock Workflow behavior, including both routing outcomes,
  live orchestration with mocked agent runs, model Activity registration, booking
  failures, mock parsing, and replay scheduling counts.
- Local Cadence mock durability via `python tests/local_cadence_checks.py`
  (Worker restart between route and booking, short and long mock Activity
  history).
- Generator drift and Explorer discovery for this recipe.

### Google ADK implementation validation

- Offline tests exercise the real ADK runner and built-in transfer machinery
  with controlled model Activity responses. They cover successful short and
  long transfers, missing and incorrect transfers, missing and duplicate tool
  calls, invalid arguments, typed receipts, Worker-local configuration, and
  exact booking Activity counts.
- `python/google-adk/tests/local_cadence_checks.py` demonstrates mock short and
  long runs and Worker restart/replay when local Cadence is available.
- `python/google-adk/tests/live_ollama_validation.py` is strict: it succeeds only
  when the actual ADK transfer event names the expected specialist and history
  contains the exact model and booking Activities. It exits nonzero and prints
  retained Workflow and Run IDs on a capability failure.

### Live behavior and known limitations

- Live mode is implemented with OpenAI Agents handoffs and Activity-backed
  model and booking tools. Live `RecipeResult` values include the specialist
  name and typed receipts only when the model completes booking tool Activities.
  The Workflow does not schedule deterministic booking fallback when a live model
  omits handoffs or tools.
- `python tests/live_ollama_validation.py` remains strict: it passes only when
  the short-trip specialist schedules one `book-ride` (no `book-flight`), or the
  long-trip specialist schedules one `book-flight` and two `book-ride` Activities,
  each with typed receipts in `RecipeResult`.
- **Documented capability gap:** with catalog ID `llama3.2-local`, the Worker
  reaches Ollama and `OpenAIActivities.invoke_model` runs, but `llama3.2:latest`
  did not perform specialist handoffs or booking tool calls in acceptance runs.
  Do not treat `llama3.2-local` as a working live model for this recipe.
- Successful real-model handoffs and tool-backed bookings are deferred to
  [Issue #47](https://github.com/cadence-workflow/cadence-ai-samples/issues/47).
  Alternate local Ollama model evaluation is tracked in
  [Issue #48](https://github.com/cadence-workflow/cadence-ai-samples/issues/48).
- Google ADK live mode derives route and specialist from
  `event.actions.transfer_to_agent`, then validates the tool receipts returned by
  the selected specialist. Deterministic parsing checks that the model selected
  the correct specialist, but never substitutes a transfer or schedules fallback
  bookings.
- Real-model Google ADK success is recorded separately from OpenAI Agents
  evidence. An unavailable or incapable local model is not replaced with remote
  inference.
- **Google ADK capability gap:** the Issue #26 local `llama3.2-local` acceptance
  run reached `GoogleADKActivities.generate_content_async` for both routes, but
  Ollama rejected each tool-bearing request with HTTP 400 before a transfer or
  booking Activity. The strict script exited nonzero and retained both histories.

### Non-goals for this closure

- Proving live handoffs with remote or paid catalog models (Issue #47).
- Selecting a replacement local Ollama model (Issue #48).

The recipe imports neither templates nor another recipe.
