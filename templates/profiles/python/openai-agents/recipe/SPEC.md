# __RECIPE_TITLE__ implementation specification

## Problem statement

[State the business problem that benefits from durable agent orchestration.]

## Goals and non-goals

### Goals

- [Define the measurable result produced by the agent.]
- Preserve a credential-free, provider-free offline path.
- Keep agent model requests in Cadence Activities.

### Non-goals

- Classifier integration. This profile intentionally has classifier `none`.
- Tools, handoffs, or structured output in the starter agent.
- Automatic live provider access without explicit Worker confirmation.

## Business scenario

[Describe the users, request text, operating constraints, and desired answer.]

## Repository implementation profile

Generated from the `python/openai-agents` profile defined by the
[recipe template system specification](https://github.com/cadence-workflow/cadence-ai-samples/blob/main/templates/SPEC-Recipe-Templates.md).

- **Cadence language:** Python
- **Classifier integration:** none
- **Agent integration:** OpenAI Agents
- **Implementation directory:** `python/openai-agents`
- **Mock and live behavior:** Mock is the default and uses one deterministic
  Activity without credentials or provider calls. Live mode requires a
  supported `models.yaml` selection, and the Worker requires `--confirm-live`
  before constructing provider Activities.
- **Runtime catalog use:** Live mode reads only repository-root `models.yaml`.
  This profile neither reads nor requires `classifiers.yaml`.
- **Workflow ID reuse:** `start` uses
  `__RECIPE_SLUG__-openai-agents-demo` with `ALLOW_DUPLICATE`. A new Run may
  start after the previous Run closes; an open duplicate is rejected.
- **Profile deviations:** None. [Record any recipe-specific deviation.]

## Language and dependencies

- **Runtime:** Python 3.12 or 3.13
- **Cadence:** `cadence-python-client` at commit
  `2bc1af4207d20cd99ae64fa1ef82d803905af947`
- **Agent framework:** `openai-agents` 0.12.5
- **Provider client:** `openai` 2.30.0
- **Serialization and catalog:** `pydantic` 2.12.5 and PyYAML 6.0.2

## Workflow architecture

The starter Workflow validates `mode`, then schedules the deterministic mock
Activity or runs the no-tool OpenAI Agents loop. The live `CadenceModel`
implementation turns every provider model request into a Cadence Activity. The
Workflow does not construct HTTP clients or read environment variables.

## AI model or agent responsibilities

The starter agent answers one text request and suggests one practical next step.
Replace that instruction with the narrow reasoning task ordinary code cannot
perform reliably. Keep validation, authorization, and irreversible decisions in
ordinary code.

## Activity boundaries

- `__RECIPE_SLUG__.mock-agent`: deterministic offline answer used by default.
- `OpenAIActivities.invoke_model`: typed native OpenAI Responses Activity.
- `OpenAICompatibleChatCompletions.invoke_model`: JSON-safe Google and Ollama
  compatibility Activity.

Provider clients, credentials, endpoints, model calls, and provider errors stay
on the Worker side of these boundaries.

## Input and output schemas

Workflow input:

```json
{
  "text": "non-empty request text",
  "mode": "mock or live",
  "model_name": "catalog model name in live mode, otherwise null",
  "model_provider": "google, ollama, or openai in live mode, otherwise null"
}
```

Workflow output:

```json
{
  "answer": "agent or mock answer",
  "mode": "mock or live"
}
```

Credentials and endpoints are never Workflow fields.

## Configuration

- `--domain`: defaults to `cadence-ai-samples`.
- `--task-list`: defaults to `__RECIPE_SLUG__-openai-agents`.
- `--workflow-id`: defaults to
  `__RECIPE_SLUG__-openai-agents-demo`.
- `--catalog-dir`: defaults to the generated checkout root.
- `--model-id`: defaults to `gemini-flash-lite`.
- `MODEL_AI_KEY`: required by Google and OpenAI live Workers, unused by mock and
  local Ollama modes.
- `--confirm-live`: required on live Worker startup.

Supported IDs are `gemini-flash-lite`, `llama3.2-local`, and `openai-nano`.
Catalog candidates outside that set, unsafe endpoints, unknown IDs, duplicate
IDs, and malformed entries fail before provider access. `models.yaml` is the
sole source of provider, model, and provider-native base endpoint values. The
Worker derives the adapter-owned OpenAI-compatible route: Google adds
`/v1beta/openai/`, Ollama adds `/v1/`, and OpenAI uses its configured base
unchanged. Remote credential-bearing providers require HTTPS, and Ollama
requires a loopback endpoint.

## Failure handling and retry behavior

Model Activities have a 30-second schedule-to-close timeout and three attempts
with exponential backoff. Authentication, configuration, and response-schema
errors are non-retryable. Other provider failures are sanitized and retryable.
An exhausted Activity failure fails the starter Workflow for operator
inspection and retry.

## Synthetic test data

Use invented request text and fixed model responses. Core tests patch model
Activities and HTTP transports, require no credentials, and make no paid calls.
Include empty text, unsupported model entries, unsafe endpoints, duplicate open
Workflow starts, and transient Activity failures.

## Tests and acceptance criteria

- Mock Worker and start commands are the defaults and do not load a catalog.
- Mock Workflow and Activity tests run without Cadence server or credentials.
- Every supported live catalog entry validates without provider calls.
- Safe catalog endpoint changes reach the Worker-local provider client without
  live calls.
- Live agent tests schedule only the expected model Activity.
- Live Worker startup fails without `--confirm-live`.
- Classifier catalogs and classifier flags are absent.
- A closed demo ID may be reused, while an open duplicate is reported.
- Generated files contain no unresolved placeholders or cross-recipe imports.

## Definition of done

- [ ] Replace the starter problem, instruction, and schemas.
- [ ] Keep an offline mock path with representative synthetic inputs.
- [ ] Keep all provider and model work at Activity boundaries.
- [ ] Document any tools, handoffs, side effects, and idempotency rules added.
- [ ] Run the focused offline suite.
- [ ] Verify Worker and start commands use matching selections.
- [ ] Keep credentials and proprietary data out of source and Workflow history.
