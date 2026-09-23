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

Use a dedicated task list for each case. Set provider credentials only in the
worker terminal; never pass them to `start`. Workflows started by this harness
have a 60-minute execution timeout and a 30-second workflow-task timeout.

The Google ADK + Gemini and Google ADK + local Ollama paths have passed the
staged live replay test. OpenAI Agents + OpenAI has not been live-tested in
this harness. The two app-local Chat Completions paths described below are
implemented and offline-tested but have not yet been run live. The optional
`echo_token` tool path also remains unverified.

### 1. Google ADK + Gemini

Configure the appropriate Google AI or Vertex credentials in the worker
terminal, then start the dedicated worker:

```bash
python compatibility_spike.py --task-list agent-compat-gemini worker \
  --case adk-gemini
```

In another terminal, a cloud-backed start requires `--confirm-live` and an
explicit model. It prints only workflow identifiers, never credentials or
model output.

```bash
python compatibility_spike.py --task-list agent-compat-gemini start \
  --case adk-gemini --model gemini-3.5-flash-lite \
  --workflow-id gemini-replay-004 --pause-after-model --confirm-live
```

Use staged history verification before stopping the worker. It reads every
history page and fails unless at least one model Activity completed and the
Workflow is still waiting without the resume Signal. Record the printed
`baseline-model-scheduled` value:

```bash
python compatibility_spike.py history \
  --workflow-id gemini-replay-004 --case adk-gemini \
  --stage before-restart
```

Then stop and restart the worker, send the Signal, and run the after-resume
check with the recorded value (shown as `1` here):

```bash
# stop the worker with Ctrl-C, then restart the same case and task list
python compatibility_spike.py --task-list agent-compat-gemini worker \
  --case adk-gemini

# in another terminal
python compatibility_spike.py resume --workflow-id gemini-replay-004
python compatibility_spike.py history \
  --workflow-id gemini-replay-004 --case adk-gemini \
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
token. This optional live tool path has not been tested. Add `--expect-tool`
to both staged history commands; verification fails unless the `echo_token`
Activity was scheduled and completed:

```bash
python compatibility_spike.py --task-list agent-compat-gemini start \
  --case adk-gemini --model gemini-3.5-flash-lite \
  --workflow-id gemini-tool-replay-001 --pause-after-model \
  --with-tool --confirm-live

python compatibility_spike.py history \
  --workflow-id gemini-tool-replay-001 --case adk-gemini \
  --stage before-restart --expect-tool
```

After restart and resume, also include `--expect-tool` with
`--stage after-resume` and the recorded model-Activity baseline.

### 2. Google ADK + local Ollama

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

### 3. OpenAI Agents SDK + OpenAI

Set the OpenAI credential only in the dedicated worker terminal:

```bash
export OPENAI_API_KEY='your-key'
python compatibility_spike.py --task-list agent-compat-openai worker \
  --case openai-openai
```

Keep that worker running. In a different terminal, start the Workflow:

```bash
python compatibility_spike.py --task-list agent-compat-openai start \
  --case openai-openai --model YOUR_OPENAI_MODEL \
  --workflow-id openai-replay-001 --pause-after-model --confirm-live
```

### 4. OpenAI Agents SDK + Gemini through Chat Completions

This case uses the spike's local `OpenAICompatibleChatCompletions.invoke_model`
Activity. The released Cadence integration remains unchanged. Set the Gemini
credential only in the worker terminal; the official OpenAI-compatible base
URL is the default and can be overridden with `GEMINI_OPENAI_BASE_URL`.

```bash
export GEMINI_API_KEY='your-key'
python compatibility_spike.py --task-list agent-compat-openai-gemini worker \
  --case openai-gemini
```

In another terminal, explicitly opt in to the cloud-backed Workflow:

```bash
python compatibility_spike.py --task-list agent-compat-openai-gemini start \
  --case openai-gemini --model gemini-3.5-flash-lite \
  --workflow-id openai-gemini-replay-001 --pause-after-model --confirm-live
```

Use the same staged history procedure as above, with `--case openai-gemini`.
The expected model Activity is
`OpenAICompatibleChatCompletions.invoke_model`. Do not add `--with-tool`;
tool-call compatibility has not been validated for this path.

### 5. OpenAI Agents SDK + local Ollama through Chat Completions

This path uses the same local Cadence Activity with Chat Completions forced.
It defaults to `http://localhost:11434/v1/` and accepts only a loopback URL via
`OLLAMA_OPENAI_BASE_URL`. The placeholder client key defaults to `ollama`; it
is not a real credential and local Ollama does not authenticate it. This path
does not require the optional LiteLLM dependency used by Google ADK + Ollama.

```bash
ollama pull llama3.2:latest
python compatibility_spike.py --task-list agent-compat-openai-ollama worker \
  --case openai-ollama
```

In another terminal:

```bash
python compatibility_spike.py --task-list agent-compat-openai-ollama start \
  --case openai-ollama --model llama3.2:latest \
  --workflow-id openai-ollama-replay-001 --pause-after-model
```

Use the staged history procedure with `--case openai-ollama`. Do not add
`--with-tool`; the initial compatibility claim is intentionally no-tool.

`openai-claude` intentionally fails before a Cadence client is created. The
released integration cannot route that case durably without an SDK change.

Selecting a worker case registers only that released integration, so an
ADK-only worker does not require an OpenAI credential merely to start.

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
