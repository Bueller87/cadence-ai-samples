# Recurring AI Watch

A fictional application's background jobs depend on a library with three scripted
release notes. Cadence checks immediately, then every 15 seconds:

`scripted update → Jev: relevant? → optional impact report → durable wait → repeat`

Mock mode is the default and needs no credentials. The live path supports only
the framework/model paths below, with TypeSafe Jev classification. There is no real
release polling or agent tool use.

## Setup

Use Python 3.12 or newer and a running Cadence server at `localhost:7833`, with
the existing domain `cadence-ai-samples`. Run from the repository root:

```bash
cd recipes/recurring-ai-watch/python
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

The sample pins the Cadence Python SDK to PR #176 commit
`2bc1af4207d20cd99ae64fa1ef82d803905af947` (an experimental build), Google ADK
2.1.0, OpenAI Agents 0.12.5, OpenAI 2.30.0, and LiteLLM 1.83.7. The SDK pin fixes
native OpenAI Activity argument decoding; released 0.4.0 cannot run that path.
Use this sample's virtual environment so the Phase 5 spike stays unchanged.

## Mock run

Terminal 1 (leave the Worker running):

```bash
python main.py --task-list recurring-ai-watch-mock worker
```

Terminal 2 (same directory and interpreter selection):

```bash
python main.py --task-list recurring-ai-watch-mock start
python main.py status
python main.py check-now
python main.py stop
```

The default Workflow ID is `recurring-ai-watch-demo`. Supply `--workflow-id`
before the command to operate on another Watch. `start --interval 15` sets the
interval in seconds. `start` submits the Workflow and returns immediately.

## Live run

In Terminal 1, set `MODEL_AI_KEY` to the selected Google or OpenAI API key and
`CLASSIFIER_AI_KEY` to a valid TypeSafe Jev key using your local credential setup.
These variables belong only in the Worker session. Do not put their values in
commands saved to the repository, catalog files, or Workflow inputs.

```bash
export MODEL_AI_KEY='your-google-gemini-key'
export CLASSIFIER_AI_KEY='your-typesafe-jev-key'
python main.py --domain cadence-ai-samples \
    --task-list recurring-ai-watch-live \
    --agent-id google-adk --model-id gemini-flash-lite --classifier-id jev-default \
    worker --mode live --confirm-live
```

Terminal 2:

```bash
python main.py --task-list recurring-ai-watch-live \
    --agent-id google-adk --model-id gemini-flash-lite --classifier-id jev-default \
    start --mode live
python main.py status
python main.py check-now
python main.py stop
```

Worker/start resolve command-line IDs through `agents.yaml`, `models.yaml`, and
`classifiers.yaml`; `--catalog-dir` supports a copied sample. The selected entries
provide endpoints, while credentials remain Worker-local. Only `jev-default` is
implemented; other classifier catalog entries are candidates and are rejected.
Keep mock and live Workers on separate task lists, and use a separate task list
for each live pair. Use the same IDs and task list for its Worker and `start`.

| Agent ID | Model ID | Model Activity and transport |
|---|---|---|
| `google-adk` | `gemini-flash-lite` | `GoogleADKActivities.generate_content_async`, native Gemini |
| `google-adk` | `llama3.2-local` | Same ADK Activity, LiteLLM `ollama_chat/llama3.2:latest` |
| `google-adk` | `openai-nano` | Same ADK Activity, LiteLLM `openai/gpt-5-nano` |
| `openai-agents` | `gemini-flash-lite` | `OpenAICompatibleChatCompletions.invoke_model`, Chat Completions |
| `openai-agents` | `llama3.2-local` | Same Chat Completions Activity |
| `openai-agents` | `openai-nano` | Native `OpenAIActivities.invoke_model`, Responses |

All six combinations completed Watch runs with live Jev and the selected model,
including report generation, check-now, and graceful stop. See
[matrix evidence](MATRIX_RESULTS.md) for execution IDs and limits. Earlier restart
replay tests in the [Phase 5 findings](../../spikes/cadence-python-agent-compat/FINDINGS.md)
are separate evidence; this matrix did not repeat those restart tests.

`inference.py` translates the logical catalog model only at the framework boundary.
For OpenAI Agents, it derives Google's `/v1beta/openai/` and Ollama's `/v1/`
Chat Completions URLs from their native catalog endpoints. Native Google requires
the official endpoint, and Ollama requires a loopback endpoint. The OpenAI endpoint
is used as supplied, including its `/v1` path. Ollama needs no `MODEL_AI_KEY`;
`CLASSIFIER_AI_KEY` is still required for Jev. Start the local Ollama server and
make `llama3.2:latest` available before starting either Ollama Worker.

The selected framework, provider, and model name travel with the Workflow across
Continue-As-New. Replay does not reread YAML. Endpoints and credential aliases
are configured only in the Worker. All model requests use Cadence Activities.

For an automated CLI smoke, keep the matching Worker running and execute this
**as a script file** in Terminal 2:

```bash
MODE=live TASK_LIST=recurring-ai-watch-live ./smoke.sh
# Or, with the mock Worker:
MODE=mock TASK_LIST=recurring-ai-watch-mock ./smoke.sh
```

Set `PYTHON=/path/to/python` to use a different Python executable. On Windows,
run these commands from WSL or another Bash environment.

It uses a unique Workflow ID, waits for a report, sends check-now, stops while
waiting, and checks the count stays fixed. Command errors and assertion failures
exit with code 1 before PASS. On failure, inspect the printed Workflow ID in
Cadence Web; if still open, stop it with `--workflow-id <id> stop`.

In Cadence Web, select `cadence-ai-samples` and inspect that Workflow's history.
Expect `recurring-watch.classify-update` first and the selected model Activity
from the table only after a relevant decision.
The status Query is `watch-status`. Its state is application state: after a fatal
Workflow failure it may still show the last `CHECKING` state. Use the execution's
terminal status/history to determine success or failure.

## Durability and failures

- One cycle runs at a time. A stop during a check finishes that check; a stop
  during waiting completes without another check.
- AI Activities have at most three attempts with exponential backoff. An
  exhausted transient failure leaves the release position unchanged. The next
  cycle repeats Jev, even if report generation was the operation that failed.
- Authentication, configuration, and schema errors fail the Watch. Google can
  report an invalid key as HTTP 400; this is treated as authentication failure.
- After all releases are consumed, checks retain the latest report without more
  AI calls. Continue-As-New every 20 checks carries essential state and waits
  the normal interval before the next check. Each run has a 365-day timeout.
- A generated report is advice to review, not verified knowledge about the
  fictional application. Retried provider calls may incur additional usage.

## Validation

```bash
python -m unittest discover -s tests -v
python -m py_compile config.py workflow.py live.py inference.py main.py
# Optional: existing local Cadence server; isolated mock Worker, no AI calls.
python tests/local_cadence_checks.py
```

For an explicit local Ollama check with mock classification and an isolated Worker:

```bash
python tests/live_matrix_check.py --agent-id google-adk --model-id llama3.2-local --local-ollama
python tests/live_matrix_check.py --agent-id openai-agents --model-id llama3.2-local --local-ollama
```

For a full live check, first start the matching live Worker with credentials in a
separate terminal, then run `tests/live_matrix_check.py` with matching `--agent-id`,
`--model-id`, and `--task-list` (omit `--local-ollama`). The checker starts a unique
Watch, verifies model Activity completion and Signals, and stops that Watch.

The released SDK's in-memory tests auto-fire timers, so they cannot faithfully
inject stop while timer-waiting or demonstrate server-side Activity retries.
The smoke and local-server checks cover those boundaries. Local checks exercise
Continue-As-New, exhausted-retry recovery, and stop during an active Activity.
