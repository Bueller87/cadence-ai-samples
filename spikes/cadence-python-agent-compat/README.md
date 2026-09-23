# Cadence Python agent/model compatibility spike

This is a small, removable technical spike. It is separate from the Go
ticket-routing recipe and contains no incident-triage logic, provider adapter,
or model/configuration catalog.

See [FINDINGS.md](FINDINGS.md) for source-review conclusions and the separate
execution-results table.

## Setup and offline tests

Use Python 3.12 or 3.13. The base environment does not make model calls.

```bash
cd spikes/cadence-python-agent-compat
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
pytest
```

PowerShell activation is `.venv\Scripts\Activate.ps1`.

## Explicit live runs

Start a worker on a dedicated task list. Set cloud credentials only in this
worker terminal; never pass them to `start`.

```bash
export OPENAI_API_KEY='your-key'
python compatibility_spike.py --task-list agent-compat-openai worker \
  --case openai-openai
```

In another terminal, a cloud-backed start requires `--confirm-live` and an
explicit model. It prints only workflow identifiers, never credentials or
model output.

```bash
python compatibility_spike.py --task-list agent-compat-openai start \
  --case openai-openai --model YOUR_OPENAI_MODEL \
  --workflow-id openai-replay-001 --pause-after-model --confirm-live
```

After the model Activity is complete, stop and restart the worker, then resume
the workflow and inspect history. Record the model Activity counts before the
restart, then confirm they are unchanged after resuming:

```bash
# before restart: record scheduled/completed counts
python compatibility_spike.py history --workflow-id openai-replay-001

# stop the worker with Ctrl-C, then start the same case and task list again
python compatibility_spike.py --task-list agent-compat-openai worker \
  --case openai-openai

python compatibility_spike.py resume --workflow-id openai-replay-001
python compatibility_spike.py history --workflow-id openai-replay-001
```

An unchanged count for `OpenAIActivities.invoke_model` (or
`GoogleADKActivities.generate_content_async`) proves Cadence replay reused the
completed Activity result; the restarted worker must not emit another provider
invocation. Add `--with-tool` to a start command only to include the harmless
`echo_token` Activity.

For Gemini, use `--case adk-gemini`, a separate task list, an explicit Gemini
model, and the appropriate Google AI or Vertex credentials in the worker.

For local Ollama only, install the optional group and use ADK's required
`ollama_chat/` model prefix:

```bash
python -m pip install -e '.[dev,ollama]'
export OLLAMA_API_BASE='http://localhost:11434'
python compatibility_spike.py --task-list agent-compat-ollama worker \
  --case adk-ollama
python compatibility_spike.py --task-list agent-compat-ollama start \
  --case adk-ollama --model ollama_chat/YOUR_LOCAL_MODEL \
  --workflow-id ollama-replay-001 --pause-after-model
```

`openai-claude` intentionally fails before a Cadence client is created. The
released integration cannot route that case durably without an SDK change.

For Gemini, start the worker with `--case adk-gemini`. Selecting a worker case
registers only that released integration, so an ADK-only worker does not
require an OpenAI credential merely to start.

## Windows PowerShell

Use the same commands with `python` replaced by your installed Python launcher
if needed. For example:

```powershell
cd spikes\cadence-python-agent-compat
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e '.[dev]'
pytest
```
