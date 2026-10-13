# Auto Research

Auto Research builds a reviewable Markdown briefing from clearly labeled
synthetic source briefs. Cadence durably records planning, specialist research,
synthesis, human revisions, approval, and one local publication. This is an
agent orchestration example, not factual web research.

## Architecture

```text
topic
  → planner
  → up to four specialist passes
      → simulated source search Activity
      → two simulated source-read Activities
  → synthesis
  → revision-aware human review
      → revise, expand, or approve
  → idempotent local publication Activity
```

Mock and live modes use the same Workflow state machine. Mock role Activities
return deterministic fixtures. Live role Activities run OpenAI Agents with the
model selected by the Worker from repository-root `models.yaml`. Provider,
model, endpoint, credentials, and `AUTO_RESEARCH_OUTPUT_DIR` never enter
Workflow input or history.

## Requirements

- Python 3.12 or 3.13.
- Cadence at `localhost:7833`, including the `cadence-ai-samples` domain.
- Live local validation only: Ollama at the catalog endpoint with
  `llama3.2:latest`.

## Setup

From the repository root:

```bash
cd recipes/auto-research/python/openai-agents
python3.13 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

Use a publication directory outside the checkout:

```bash
export AUTO_RESEARCH_OUTPUT_DIR="$(mktemp -d)"
```

The Worker defaults to `research_reports` when this variable is absent.

## Offline mock walkthrough

Terminal 1:

```bash
python main.py worker --mode mock
```

Terminal 2:

```bash
python main.py start --mode mock \
  --text "The impact of AI on drug discovery"
python main.py status
```

The initial draft is revision `0`. Read the current revision before every
action:

```bash
python main.py revise --expected-revision 0 \
  --feedback "Make the executive summary shorter."
python main.py status

python main.py expand --expected-revision 1 \
  --topic "Operational adoption constraints"
python main.py status

python main.py approve --expected-revision 2
```

Only the first eligible action for a revision wins. Early, stale, duplicate,
conflicting, empty, and over-limit actions have no side effects. At most three
revise or expand actions are accepted; approval remains available after that
limit.

## Local Samples Explorer

Start the Explorer from the repository root:

```bash
tools/explorer/.venv/bin/python tools/explorer/server.py
```

Select Auto Research, Python, OpenAI Agents, and mock mode. Start the Worker and
Workflow, resolve the current Run, then open Queries to read the draft and
revision. The rendered revise and expand controls each send one typed object
containing `expected_revision` and the bounded `feedback` or `topic` field.
Approve sends its typed integer revision. All three controls require
confirmation and are enabled only for the exact current resolved Run.

## Local Ollama walkthrough

No remote provider is authorized by this recipe's local validation. Start
Ollama and make sure the catalog model exists:

```bash
ollama pull llama3.2:latest
ollama list
```

Terminal 1:

```bash
python main.py worker --mode live --confirm-live \
  --model-id llama3.2-local
```

Terminal 2:

```bash
python main.py start --mode live \
  --text "The impact of AI on drug discovery"
python main.py status
python main.py approve --expected-revision 0
```

This live path uses the complete production state machine: planner, every
specialist's simulated source Activities, synthesis, review, and publication.
There is no reduced validation path. Stop and record the observed provider
failure if the local model cannot complete a bounded role output. Do not fall
back to a remote model.

## Durability, output, and reuse

The Workflow waits without an application timer while its 24-hour execution
timeout remains open. Restarting a Worker on the same task list reconstructs
completed role, tool, and publication Activities from history. A pending review
remains queryable and signalable.

Publication uses a deterministic ID derived from Workflow ID, Run ID, and final
revision. Retries atomically replace the same stable filename and return a
portable receipt containing only publication ID, filename, revision, and
content hash. The absolute output path remains Worker-local.

The default Workflow ID is `auto-research-openai-agents-demo` with
`ALLOW_DUPLICATE`. A closed ID can be reused; an open duplicate is rejected.
Override `--workflow-id` when running multiple walkthroughs.

## Limits and failures

- Topic: 500 characters.
- Initial plan: one to four questions.
- Feedback: 1,000 characters.
- Expansion topic: 300 characters.
- Accepted revise/expand rounds: three.
- Specialist summary: 5,000 characters.
- Complete report: 20,000 characters.

Model Activities have a three-minute schedule-to-close timeout and up to three
attempts. Authentication, configuration, and schema failures are non-retryable.
Provider bodies are sanitized before failures enter history. Simulated
search/read Activities make no network requests.

## Tests

From `recipes/auto-research/python/openai-agents`:

```bash
python -m unittest discover -s tests -v
python tests/local_cadence_checks.py
```

Repository suites:

```bash
python3.13 -m unittest discover -s scripts/tests -v
tools/explorer/.venv/bin/python -m unittest discover \
  -s tools/explorer/tests -v
node tools/explorer/tests/ui.cjs
```

Remove the temporary publication directory, generated reports, caches, and
virtual environments before recording validation evidence.

## Copying this recipe

Copy the whole recipe directory. It imports neither templates nor another
recipe. Preserve Activity boundaries for model calls, simulated tools, progress,
and publication; keep Worker-local configuration out of Workflow data; and
replace synthetic fixtures and role instructions together.
