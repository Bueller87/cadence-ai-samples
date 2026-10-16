# __RECIPE_TITLE__

## Problem

This starter demonstrates durable request classification without putting an
external classifier call in Workflow code. Replace the synthetic request and
the two starter labels with the business decision your recipe needs.

## Architecture

This recipe uses the `go/systemone` profile: Go 1.23, a SystemOne classifier
Activity, and no agent framework:

`request → ClassifyText Activity → recorded Classification → result`

The default `mock` Activity is deterministic, offline, and credential-free.
Live selections come from the repository-root `classifiers.yaml`. The Worker,
not the starter, constructs the HTTP client and makes provider calls. Starters
never read the catalog or credentials.

## Requirements

- Go 1.23.
- A Cadence frontend at `127.0.0.1:7933` with domain
  `cadence-ai-samples`.
- For live runs, a supported `classifiers.yaml` entry and its local service or
  provider access.

## Setup and offline run

From the repository root:

```bash
cd recipes/__RECIPE_SLUG__/go/systemone
./run.sh setup
go test ./...
```

Start the mock Worker in terminal 1:

```bash
./run.sh worker -mode mock
```

Start the mock Workflow in terminal 2:

```bash
./run.sh start -mode mock -text "Investigate this urgent example request."
```

Both commands default to domain `cadence-ai-samples`, task list
`__RECIPE_SLUG__`, and Workflow ID `__RECIPE_SLUG__-demo`. Mock Workers
register the local classifier without reading `classifiers.yaml` or
`CLASSIFIER_AI_KEY`. When changing the domain or task list, pass the same values
to the Worker and starter. Both actions require an explicit `-mode mock` or
`-mode live`.

## Live SystemOne classifiers

Only `typesafe` and `laya` catalog providers are implemented. Unknown
providers are catalog candidates and are rejected. Endpoints cannot contain
credentials, query strings, or fragments. TypeSafe requires HTTPS. Laya is
restricted to a loopback host. A live Worker requires `-confirm-live`; a live
start does not because it makes no provider call and reads no live
configuration.

Use a dedicated task list for each live Worker and its starts.

For local Laya:

```bash
./run.sh worker -mode live -classifier-id laya-local \
  -task-list __RECIPE_SLUG__-laya -confirm-live
```

```bash
./run.sh start -mode live -task-list __RECIPE_SLUG__-laya \
  -workflow-id __RECIPE_SLUG__-laya-demo
```

For TypeSafe Jev, export the key only in the Worker shell:

```bash
export CLASSIFIER_AI_KEY='your-personal-key'
./run.sh worker -mode live -classifier-id jev-default \
  -task-list __RECIPE_SLUG__-jev -confirm-live
```

The starter does not need the key:

```bash
./run.sh start -mode live -task-list __RECIPE_SLUG__-jev \
  -workflow-id __RECIPE_SLUG__-jev-demo
```

Unset the key afterward. The key is never placed in flags, logs, or Workflow
history. The Worker does not load `.env.example`.

The default `-catalog-dir ../../../..` resolves to the repository root from this
implementation directory. After copying the recipe elsewhere, copy
`classifiers.yaml` or pass `-catalog-dir` to the directory that contains it.
The selected catalog entry is the sole source of the classifier provider,
model, and endpoint. The Worker passes that endpoint unchanged to the HTTP
request transport.

## Workflow ID reuse

The starter uses Cadence's `ALLOW_DUPLICATE` policy. A closed Run may be
followed by another Run with the same stable Workflow ID. Cadence still rejects
a duplicate start while a Run is open. Wait for the open Run to close or pass a
different `-workflow-id`. The live examples use classifier-specific Workflow
IDs to keep runs separate.

## Failure and retry behavior

Classification has a two-minute schedule-to-close limit, a 30-second
start-to-close limit per attempt, and bounded exponential backoff. Network
failures, HTTP 429, HTTP 529, and server errors are retryable. Configuration,
authentication, other client errors, and malformed successful responses are
nonretryable. A timed-out attempt might have reached a live provider and
consumed usage before Cadence retries it.

## Tests

```bash
go test ./...
```

Tests use the mock Activity, Cadence's in-memory test environment, temporary
catalogs, and an in-process HTTP server. They verify that a safe catalog
endpoint change reaches the request transport without contacting Cadence or a
live classifier provider.

## Adapting the starter

Edit `workflow.go` to replace `RecipeInput`, the route criteria, and
`RecipeResult`. Keep provider calls in Activities and retain a mock
implementation for offline development. Edit `main.go` only when the Worker or
starter interface changes. The implementation imports no other recipe and is
intended to be copied as a unit.

## Manual setup recovery

If the launcher cannot prepare the implementation, run:

```bash
go mod download
```
