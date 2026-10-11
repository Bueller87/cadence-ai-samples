# __RECIPE_TITLE__

## Problem

Describe the business problem that needs durable agent orchestration.

## Solution

This starter accepts a text request and returns one concise answer. Mock mode
runs entirely offline. Live mode runs OpenAI Agents inside one Worker-configured
Cadence Activity.

## Architecture

This recipe uses the `python/openai-agents` profile: Python, no classifier, and
OpenAI Agents.

`request → Workflow → mock Activity or Worker-configured agent Activity → result`

The Workflow contains no provider client, endpoint, credential, or model
selection. Live Worker startup reads the selected entry from the repository-root
`models.yaml` and constructs the provider client. Workflow history carries only
the request text and mode.

## Requirements

- Python 3.12 or 3.13.
- A Cadence server at `localhost:7833` with domain `cadence-ai-samples`.
- Live mode only: a supported entry in repository-root `models.yaml`.

The dependency pins match the proven Recurring AI Watch OpenAI Agents
implementation: Cadence Python client commit
`2bc1af4207d20cd99ae64fa1ef82d803905af947`, OpenAI Agents `0.12.5`, OpenAI
`2.30.0`, Pydantic `2.12.5`, and PyYAML `6.0.2`.

## Quick start: offline mock

From the repository root:

```bash
cd recipes/__RECIPE_SLUG__/python/openai-agents
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

Start the mock Worker in terminal 1:

```bash
python main.py worker --mode mock
```

Start the mock Workflow in terminal 2:

```bash
python main.py start --mode mock --text "Review this example request."
```

Mock mode does not read `models.yaml`, inspect credentials, or make network
requests to an AI provider.

## Live mode

Supported root catalog selections are:

- `gemini-flash-lite`, using the configured HTTPS endpoint
- `llama3.2-local`, using a loopback Ollama endpoint
- `openai-nano`, using the configured HTTPS endpoint

Other catalog entries fail validation rather than being guessed. Google and
OpenAI require `MODEL_AI_KEY`. Ollama needs no key and must use a loopback URL.
Never put a secret in `models.yaml`, command arguments, Workflow input, or source
control.

`models.yaml` is the sole source of provider, model, and provider-native base
endpoint values. The Worker derives only the OpenAI-compatible protocol route:
Google adds `/v1beta/openai/`, Ollama adds `/v1/`, and OpenAI uses its configured
base unchanged. The Workflow receives only the provider and model name needed
for deterministic orchestration.

Live Workers can contact a provider and may incur cost. Export the credential
when needed, explicitly confirm live use, then select the Worker model:

```bash
export MODEL_AI_KEY="replace-with-your-key"
python main.py worker --mode live --confirm-live \
  --model-id gemini-flash-lite
```

Start a matching live Workflow in another terminal:

```bash
python main.py start --mode live \
  --text "Review this request and suggest a next step."
```

For local Ollama, select `llama3.2-local` on the Worker and omit `MODEL_AI_KEY`.
The start command intentionally has no catalog, model, endpoint, or credential
options. Worker and start commands must use the same `--domain` and
`--task-list`.

## Workflow ID reuse

Both modes default to the stable Workflow ID
`__RECIPE_SLUG__-openai-agents-demo` with Cadence's `ALLOW_DUPLICATE` policy.
After a Run closes, the same ID can start a new Run. If a Run is still open,
Cadence rejects the duplicate and the command reports that you must wait or pass
a different `--workflow-id`.

## Reliability and failures

Model Activities use a 30-second schedule-to-close timeout and at most three
attempts with exponential backoff. Authentication, configuration, and schema
errors are non-retryable. Provider error bodies are not copied into Activity
failures because they may contain sensitive request details.

## Tests

From `recipes/__RECIPE_SLUG__/python/openai-agents`:

```bash
python -m unittest discover -s tests -v
```

The suite is fully offline. It tests Workflow and Activity behavior, model
catalog validation, all supported live selections without provider calls,
command parsing, Worker-local live configuration, Workflow ID reuse, and
failures.

## Copying this recipe

Copy the recipe with its `models.yaml` catalog convention, then replace the
starter input, instruction, and mock Activity. Keep provider clients and model
requests at Activity boundaries. Add tools or handoffs only with matching
Activity registration, serialization tests, retry policy, and documentation.
