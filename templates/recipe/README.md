# __RECIPE_TITLE__

## Problem

Describe the business problem this recipe solves.

## Solution

Replace the starter input, classifier question, model instruction, and result schema
with the smallest workflow that solves the problem.

## Architecture

The generated Python starter keeps nondeterministic calls in Activities:

`input → classifier Activity → optional model Activity → result`

## Requirements

- Python 3.12 or newer.
- A Cadence server at `localhost:7833` with domain `cadence-ai-samples`.
- Mock mode needs no credentials or external AI service.

## Quick start

From the repository root:

```bash
cd recipes/__RECIPE_SLUG__/python
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

Start the mock Worker in terminal 1:

```bash
python main.py worker
```

Start the example Workflow in terminal 2:

```bash
python main.py start --text "Investigate this example request."
```

The default task list is `__RECIPE_SLUG__` and the default Workflow ID is
`__RECIPE_SLUG__-demo`. Use unique IDs for repeated runs.

## Configuration

Worker and starter select entries from the repository-root `agents.yaml`,
`models.yaml`, and `classifiers.yaml` by ID. Mock mode is the default. Live mode
requires `--mode live --confirm-live` on the Worker and `--mode live` on `start`.
Use the same catalog IDs and task list in both terminals.

```bash
export MODEL_AI_KEY='your-model-key'
export CLASSIFIER_AI_KEY='your-jev-key'
python main.py \
  --agent-id google-adk \
  --model-id gemini-flash-lite \
  --classifier-id jev-default \
  worker --mode live --confirm-live
```

`MODEL_AI_KEY` is unnecessary for local Ollama. Credentials remain in the Worker
process and never enter Workflow inputs or history. Only `jev-default` is
implemented; other classifier catalog entries are rejected.

## Expected results

The starter prints the submitted Workflow ID. Cadence Web shows the classifier
Activity followed by the mock or selected model Activity when analysis is needed.

## Why this approach?

Use deterministic Workflow code for orchestration, Jev for the narrow decision,
and the selected agent/model only when flexible output is required.

## Reliability and failure handling

AI operations have bounded Activity retries. Authentication, configuration, and
schema failures are nonretryable. Replace these starter policies only when the
business workflow needs different behavior.

## Copying this recipe into your application

Edit `workflow.py` for business input, result, and deterministic orchestration.
Edit the classifier question in `live.py` and model instructions in `inference.py`.
Keep provider calls in Activities. The other files are deliberately local so the
recipe remains independently copyable.

Run the offline tests with `python -m unittest discover -s tests -v`.
