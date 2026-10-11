# __RECIPE_TITLE__

## Problem

Describe the business problem this recipe solves.

## Solution

Replace the starter input, Activities, and decision with the smallest Workflow
that solves the problem.

## Architecture

This recipe uses the `python/bare` profile: Python, no classifier integration,
and no agent framework. The starter keeps external work in Activities:

`input → assess Activity → optional follow-up Activity → result`

Both starter Activities are synthetic. They make no network calls and need no
credentials. Replace them with your real Activities and keep a synthetic version
for offline tests.

## Requirements

- Python 3.12 or 3.13. The recipe uses the published `cadence-python-client`
  0.4.1 release, which does not support Python 3.14; if `python3` is newer,
  create the virtual environment with `python3.13`.
- A Cadence server at `localhost:7833` with domain `cadence-ai-samples`.

## Quick start

From the repository root:

```bash
cd recipes/__RECIPE_SLUG__/python/bare
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

Start the Worker in terminal 1:

```bash
python main.py worker
```

Start the example Workflow in terminal 2:

```bash
python main.py start --text "Investigate this example request."
```

Both commands default to domain `cadence-ai-samples`, task list
`__RECIPE_SLUG__`, and Workflow ID `__RECIPE_SLUG__-demo`. Pass the same
`--domain` and `--task-list` to both commands when you change them.

## Workflow ID reuse

`start` uses the stable demo Workflow ID with Cadence's `ALLOW_DUPLICATE` reuse
policy. After the previous Run closes, running `start` again creates a new Run
with the same Workflow ID. While a Run is still open, Cadence rejects the
duplicate start and the command exits with an error. Wait for the open Run to
close or pass a different `--workflow-id`.

## Expected results

`start` prints the Workflow ID and Run ID. Cadence Web shows the `assess`
Activity, followed by the `follow-up` Activity when the text contains
"investigate".

## Reliability and failure handling

Each Activity has a 30-second schedule-to-close timeout and at most three
attempts with exponential backoff. `InvalidRequestError` (empty input) is not
retried. Replace these policies only when the business Workflow needs different
behavior.

## Tests

From `recipes/__RECIPE_SLUG__/python/bare` with the virtual environment active:

```bash
python -m unittest discover -s tests -v
```

The tests run offline. They need no Cadence server or credentials.

## Copying this recipe into your application

Edit `workflow.py` for business input, Activities, and deterministic
orchestration. Edit `main.py` only to change the command interface. Keep
external and nondeterministic calls in Activities. The files are deliberately
local so the recipe remains independently copyable.
