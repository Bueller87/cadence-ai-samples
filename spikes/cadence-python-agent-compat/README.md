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

Use staged history verification before stopping the worker. It reads every
history page and fails unless at least one model Activity completed and the
Workflow is still waiting without the resume Signal. Record the printed
`baseline-model-scheduled` value:

```bash
python compatibility_spike.py history \
  --workflow-id openai-replay-001 --case openai-openai \
  --stage before-restart
```

Then stop and restart the worker, send the Signal, and run the after-resume
check with the recorded value (shown as `1` here):

```bash
# stop the worker with Ctrl-C, then restart the same case and task list
python compatibility_spike.py --task-list agent-compat-openai worker \
  --case openai-openai

# in another terminal
python compatibility_spike.py resume --workflow-id openai-replay-001
python compatibility_spike.py history \
  --workflow-id openai-replay-001 --case openai-openai \
  --stage after-resume --baseline-model-scheduled 1
```

The after-resume check requires the resume Signal in history, successful
Workflow completion, and no increase in scheduled model Activities. These
facts together, plus the absence of another provider invocation in worker
logs, are the replay evidence. Unchanged counts alone do not prove that replay
succeeded. The command displays Activity names, Signal names, counts, and
terminal status only; it never decodes model payloads or credentials.

To exercise the harmless tool, add `--with-tool` to `start`. The agent is then
explicitly instructed to call `echo_token` exactly once with a fixed synthetic
token. Add `--expect-tool` to both staged history commands; verification fails
unless the `echo_token` Activity was scheduled and completed:

```bash
python compatibility_spike.py --task-list agent-compat-openai start \
  --case openai-openai --model YOUR_OPENAI_MODEL \
  --workflow-id openai-tool-replay-001 --pause-after-model \
  --with-tool --confirm-live

python compatibility_spike.py history \
  --workflow-id openai-tool-replay-001 --case openai-openai \
  --stage before-restart --expect-tool
```

After restart and resume, also include `--expect-tool` with
`--stage after-resume` and the recorded model-Activity baseline.

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
