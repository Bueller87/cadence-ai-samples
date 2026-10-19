# __RECIPE_TITLE__

## Problem

Describe the business request this recipe's agent should handle.

## Solution

Replace the starter prompt and instruction with the smallest agent behavior that
solves the problem. The scaffold runs a one-shot Cadence Workflow:

`prompt → mock Activity or Worker-configured Google ADK agent Activity → result`

Mock mode is deterministic, makes no network calls, reads no catalog, and needs
no credentials. Live mode uses Google ADK with a model selected from
`models.yaml` by the Worker. This profile has no classifier integration and does
not read `classifiers.yaml`.

## Requirements

- Python 3.12 or 3.13.
- A Cadence server at `localhost:7833` with domain `cadence-ai-samples`.
- Live mode only: the repository-root `models.yaml`, or a copied catalog passed
  with `--catalog-dir`.

The dependencies use the same proven pins as Recurring AI Watch: Cadence Python
SDK PR #176 commit `2bc1af4207d20cd99ae64fa1ef82d803905af947`,
Google ADK 2.1.0, LiteLLM 1.83.7, Pydantic 2.12.5, and PyYAML 6.0.2.

## Setup

From the repository root:

```bash
cd recipes/__RECIPE_SLUG__/python/google-adk
./run.sh setup
```

## Mock run

Start the Worker in terminal 1:

```bash
./run.sh worker --mode mock
```

Start the example Workflow in terminal 2:

```bash
./run.sh start --mode mock \
  --prompt "Summarize the next step for this request."
```

Both commands default to domain `cadence-ai-samples`, task list
`__RECIPE_SLUG__-google-adk`, and Workflow ID
`__RECIPE_SLUG__-google-adk-demo`. Put global options before the command, for
example `./run.sh --task-list another-list worker`.

## Live run

The live Worker supports these model entries and validates the selected model's
ID, provider, and endpoint without making a provider call.

| Model ID | Provider | Credential |
| --- | --- | --- |
| `gemini-flash-lite` | Google Gemini | `MODEL_AI_KEY` |
| `llama3.2-local` | local Ollama | none |
| `openai-nano` | OpenAI through ADK LiteLLM | `MODEL_AI_KEY` |

`models.yaml` is the sole source of each provider, model name, and endpoint.
The Worker passes that endpoint to Gemini's `base_url` or the selected LiteLLM
transport. Remote credential-bearing providers require HTTPS; Ollama requires a
loopback endpoint. No endpoint is stored in Workflow input or history.

In terminal 1, set the credential only in the Worker environment and explicitly
confirm that the Worker may use a live provider:

```bash
export MODEL_AI_KEY='your-provider-key'
./run.sh --task-list __RECIPE_SLUG__-google-adk-live \
  worker --mode live --confirm-live --model-id gemini-flash-lite
```

For local Ollama, start Ollama and make `llama3.2:latest` available, omit
`MODEL_AI_KEY`, and select `--model-id llama3.2-local`.

In terminal 2, use the same task list. The starter has no catalog, model, or
credential options:

```bash
./run.sh --task-list __RECIPE_SLUG__-google-adk-live \
  start --mode live --prompt "Summarize the next step for this request."
```

Use `--catalog-dir /path/to/catalogs` when the recipe is copied away from this
repository. Never store credentials in YAML, Workflow input, commands committed
to source control, or Cadence history.

## Workflow ID reuse

`start` uses Cadence's `ALLOW_DUPLICATE` policy. After the stable demo Workflow
ID closes, another `start` creates a new Run and preserves the old history. If a
Run with that ID is still open, Cadence rejects the duplicate and the CLI reports
how to select another `--workflow-id`.

## Activity and failure behavior

The Workflow contains only deterministic orchestration. Mock behavior is a local
Activity. In live mode it schedules `__RECIPE_SLUG__.google-adk-agent`; that
Worker Activity owns the Google ADK runner, selected model, provider client, and
credential.

Model Activities have a 30-second schedule-to-close timeout and at most three
attempts with exponential backoff. Authentication, configuration, and response
schema failures are non-retryable. Provider bodies are not copied into mapped
failure messages.

## Tests

From `recipes/__RECIPE_SLUG__/python/google-adk`:

```bash
.venv/bin/python -m unittest discover -s tests -v
python -m py_compile config.py workflow.py inference.py main.py
```

The suite is offline. It tests Workflow and Activity behavior, catalog parsing,
all supported live selections, endpoint and credential validation, literal CLI
commands, Worker-local live configuration, Workflow ID reuse, duplicate-open-run reporting, and
failure paths. It does not need a Cadence server or provider credentials.

## Copying this recipe

Keep `config.py`, `workflow.py`, `inference.py`, and `main.py` together. Replace
the synthetic Activity and agent instruction with recipe-specific behavior while
keeping external calls inside Activities. Preserve a mock path so core tests stay
offline, and update `SPEC.md` before adding business behavior.

## Manual setup recovery

If the launcher cannot prepare the implementation, run:

```bash
python3.13 -m venv .venv
.venv/bin/python -m pip install -e .
```
