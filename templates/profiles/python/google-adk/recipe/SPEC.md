# __RECIPE_TITLE__ implementation specification

## Purpose

Provide an independently runnable starting point for a recipe that coordinates a
Google ADK agent through Cadence. The generated code demonstrates the runtime
boundaries and safe defaults; recipe authors replace the generic prompt handling
with their approved business scenario.

## Goals

- Run a one-shot prompt through durable Cadence orchestration.
- Provide a deterministic offline Activity with no catalog or credentials.
- Support only validated `models.yaml` selections in live mode.
- Keep provider clients, credentials, and model calls at Activity boundaries.
- Remain copyable without imports from another recipe.

## Non-goals

- Classifier integration or any use of `classifiers.yaml`.
- Agent tools, memory across Workflows, signals, queries, or scheduled recurrence.
- Provider calls in core tests.
- A shared runtime used by other recipes.

## Repository implementation profile

Generated from the `python/google-adk` profile defined by the
[recipe template system specification](https://github.com/cadence-workflow/cadence-ai-samples/blob/main/templates/SPEC-Recipe-Templates.md).

- **Cadence language:** Python
- **Classifier integration:** none
- **Agent integration:** Google ADK
- **Implementation directory:** `python/google-adk`
- **Mock behavior:** `__RECIPE_SLUG__.mock-agent` returns a deterministic response
  and performs no external work.
- **Live behavior:** the Worker-configured
  `__RECIPE_SLUG__.google-adk-agent` Activity runs Google ADK with the selected
  model.
- **Runtime catalog use:** only the live Worker reads `models.yaml`; start is
  configuration-free.
- **Live confirmation:** a live Worker requires `--confirm-live` before reading
  catalogs, credentials, or constructing provider clients.
- **Workflow ID reuse:** the stable ID `__RECIPE_SLUG__-google-adk-demo` uses
  `ALLOW_DUPLICATE`; a closed Run may be followed by a new Run, while a duplicate
  start during an open Run is rejected.
- **Profile deviations:** none.

## Language and dependencies

- Python 3.12 or 3.13.
- Cadence Python SDK commit
  `2bc1af4207d20cd99ae64fa1ef82d803905af947`.
- Google ADK 2.1.0 and LiteLLM 1.83.7.
- Pydantic 2.12.5 and PyYAML 6.0.2.

These are the supported pins proven by the existing Recurring AI Watch Google ADK
implementation.

## Workflow architecture

`__RECIPE_CLASS__Workflow` validates one `AgentInput`. In mock mode it executes
the recipe-scoped mock Activity. In live mode it schedules one stable Google ADK
agent Activity whose Worker implementation owns model configuration and provider
calls. The Workflow returns one `AgentResult` and closes.

## Agent responsibility

The starter agent answers one user prompt briefly without tools or claims of
external action. A recipe must replace this instruction with a narrow business
responsibility that ordinary deterministic code cannot perform adequately.

## Activity boundaries

- `__RECIPE_SLUG__.mock-agent`: deterministic offline stand-in.
- `__RECIPE_SLUG__.google-adk-agent`: live Google ADK execution and provider
  request, performed with Worker-local catalog selection, client, and credential.

Workflow code does not make network calls, read environment variables, or read
catalog files. Retried provider calls may incur additional usage.

## Schemas

Workflow input:

```json
{
  "prompt": "non-empty string",
  "mode": "mock or live"
}
```

Workflow output:

```json
{
  "response": "agent response",
  "mode": "mock or live"
}
```

Model IDs, provider names, endpoints, and credentials are not stored in
Workflow history.

## Configuration

- Domain: `cadence-ai-samples`.
- Task list: `__RECIPE_SLUG__-google-adk`.
- Workflow ID: `__RECIPE_SLUG__-google-adk-demo`.
- Target: `localhost:7833`.
- Worker model ID: `gemini-flash-lite`.
- Worker catalog directory: repository root, override with `--catalog-dir`.
- Credential: `MODEL_AI_KEY`, required by Google and OpenAI, unused by Ollama.

Supported ID/provider pairs are `gemini-flash-lite/google`,
`llama3.2-local/ollama`, and `openai-nano/openai`. `models.yaml` is the sole
source of provider, model, and endpoint values. The Worker passes the selected
endpoint to Gemini or the selected LiteLLM transport. Remote credential-bearing
providers require HTTPS, and Ollama requires a loopback endpoint.

## Failure and retry behavior

Model Activities use a 30-second schedule-to-close timeout, exponential backoff,
and at most three attempts. Authentication, configuration, and schema failures
are non-retryable. Empty prompts fail before an Activity. A model failure fails
the Workflow; the caller may inspect Cadence history and start a new Run after
the old Run closes.

## Synthetic test data

Core tests use short invented prompts and fixed provider responses. HTTP clients
are not invoked. No fixture contains real customer data, credentials, or
proprietary material.

## Acceptance criteria

- Mock Worker and both start modes require no catalog, credential, or provider
  configuration.
- All current supported model selections validate offline.
- Safe catalog endpoint changes reach the Worker-local provider transport
  without live calls.
- Unsupported IDs, provider mismatches, unsafe endpoints, and missing required
  credentials fail before live Worker polling.
- Live Worker startup fails before catalog access unless `--confirm-live` is
  present.
- Workflow, Activities, command parsing, retries, duplicate starts, and failure
  behavior have focused offline tests.
- Generated files have no unresolved placeholders beyond generator-supported
  recipe markers and no cross-recipe imports.
