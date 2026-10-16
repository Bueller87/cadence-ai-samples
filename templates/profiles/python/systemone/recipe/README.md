# __RECIPE_TITLE__

This generated recipe runs a mock-first Cadence Workflow using Python 3.12.
Profile: `systemone`. The live path classifies through SystemOne.

## Setup

From `python/systemone`:

```bash
./run.sh setup
```

The mock path reads no catalogs or credentials:

```bash
./run.sh worker --mode mock
./run.sh start --mode mock
.venv/bin/python -m unittest discover -s tests -v
```

For a live Worker, run from this checkout so `--catalog-dir` can point at the
directory containing `classifiers.yaml` only. The Worker selects an entry with
`--classifier-id`.
`CLASSIFIER_AI_KEY` is required only for the remote SystemOne provider.
`--catalog-dir` and `--classifier-id` are Worker-only options; `start` rejects
inference configuration.


```bash
./run.sh worker --mode live --confirm-live --classifier-id laya-local
./run.sh start --mode live
```

Provider, model, and endpoint values come only from the selected catalog
entries. Remote credential-bearing endpoints require HTTPS, local endpoints
must be loopback, and URLs with credentials, queries, or fragments are rejected.
Provider, model, endpoint, and credential configuration stays on the Worker and
never enters Workflow input or history. The starter reads no catalog.

The stable demo Workflow ID uses `ALLOW_DUPLICATE`, so a closed run may be
started again. Cadence still rejects a duplicate while a run is open; pass a
different `--workflow-id` or wait for it to close.

## Manual setup recovery

If the launcher cannot prepare the implementation, run:

```bash
python3.13 -m venv .venv
.venv/bin/python -m pip install -e .
```
