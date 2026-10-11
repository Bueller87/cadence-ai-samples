# __RECIPE_TITLE__

## Problem

Describe the business problem this recipe solves.

## Solution

Replace the starter input, Activities, and decision with the smallest Workflow
that solves the problem.

## Architecture

This recipe uses the `go/bare` profile: Go, no classifier integration, and no
agent framework. The starter keeps external work in Activities:

`input → assess Activity → optional follow-up Activity → result`

Both starter Activities are synthetic. They make no network calls and need no
credentials. Replace them with real Activities and keep a synthetic
implementation for offline tests.

Both `mock` and `live` modes register these same synthetic Activities. The
`live` mode is a scaffold lane for adding a provider-backed Activity later; the
generated bare recipe makes no provider calls in either mode.

## Requirements

- Go 1.23.
- A Cadence server at `127.0.0.1:7933` with domain `cadence-ai-samples`.

The module uses the published `go.uber.org/cadence` 1.3.1 and
`go.uber.org/yarpc` 1.55.0 releases already used by this repository.

## Quick start

From the repository root:

```bash
cd recipes/__RECIPE_SLUG__/go/bare
go mod download
```

Start the Worker in terminal 1:

```bash
go run . worker -mode mock
```

Start the example Workflow in terminal 2:

```bash
go run . start -mode mock -text "Investigate this example request."
```

Both commands default to domain `cadence-ai-samples`, task list
`__RECIPE_SLUG__`, and Workflow ID `__RECIPE_SLUG__-demo`. Pass the same
`-domain` and `-task-list` values to both commands when you change them. Both
actions require an explicit `-mode mock` or `-mode live`.

To exercise the scaffold lane without adding a provider:

```bash
go run . worker -mode live
go run . start -mode live
```

## Workflow ID reuse

`start` uses the stable demo Workflow ID with Cadence's `AllowDuplicate` reuse
policy. After the previous Run closes, running `start` again creates a new Run
with the same Workflow ID. While a Run is open, Cadence rejects the duplicate
start and the command reports the conflict. Wait for the open Run to close or
pass a different `-workflow-id`.

## Expected results

`start` prints the Workflow ID and Run ID. Cadence Web shows the
`__RECIPE_SLUG__.assess` Activity, followed by the
`__RECIPE_SLUG__.follow-up` Activity when the text contains `investigate`.

## Reliability and failure handling

Activities have schedule-to-start, start-to-close, and schedule-to-close
timeouts. They retry at most three times with exponential backoff.
`InvalidRequestError`, used for empty input, is non-retriable. The Workflow
returns an error when an Activity cannot complete.

## Tests

From `recipes/__RECIPE_SLUG__/go/bare`:

```bash
go test ./...
```

The focused tests cover Workflow branches, synthetic Activities, configuration,
command parsing, failures, stable Workflow ID reuse, and duplicate-open-run
handling. They run offline without a Cadence server or credentials.

## Copying this recipe into your application

Edit `workflow.go` for business input, Activities, and deterministic
orchestration. Edit `main.go` only to change configuration or the command
interface. Keep external and nondeterministic calls in Activities. All source
and dependencies are local to this directory so the recipe remains
independently copyable.
