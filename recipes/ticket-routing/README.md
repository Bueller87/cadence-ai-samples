# StreamWave ticket routing

This recipe uses Cadence to route fictional customer-support tickets for StreamWave, a fictional streaming service. A classifier assigns a department, priority, and complexity; the parent Workflow then starts the matching department Child Workflow, which assigns a fixed fictional employee.

Phase 2 supports two classifier modes:

- `mock` is the default. It uses repeatable keyword rules, makes no network calls, and needs no credentials. Its confidence values are illustrative—not model quality, accuracy, token usage, or cost measurements.
- `jev` is an explicit opt-in. It sends one request containing three independent Choice questions to TypeSafe System One and records the returned choices, probability distributions, confidence values, model identifier, and token usage.

## Architecture

```text
TicketIntakeWorkflow
  → ClassifyTicket Activity (mock or real Jev)
  → Billing | Technical | Account | Content Child Workflow
  → fixed fictional employee assignment
  → TicketResult (ASSIGNED or UNROUTABLE)
```

External classification runs in a Cadence Activity. Workflow code uses only the recorded typed result, so replay does not call the provider again after a successful Activity result has been persisted.

## Requirements

- Go 1.23 or newer.
- A local Cadence server for the runnable demonstrations.
- A registered Cadence domain named `cadence-ai-samples`.
- A TypeSafe API key only for the optional live demonstration.

The Cadence project's public Docker Compose configuration can run a local server and Cadence Web. From a separate clone of [cadence-workflow/cadence](https://github.com/cadence-workflow/cadence):

```powershell
docker compose -f docker/docker-compose.yml up -d
cadence --domain cadence-ai-samples domain register
```

Cadence Web is normally available at <http://localhost:8088>. Unit tests use the SDK test environment and local HTTP test servers; they do not need a Cadence server, Jev credentials, or paid API calls.

## Run with the mock classifier

From the repository root, change into the Go module directory in two PowerShell terminals:

```powershell
Set-Location .\recipes\ticket-routing\go
```

The mock is selected when `AI_PROVIDER` is unset or set to `mock`. Start the worker in the first terminal:

```powershell
$env:AI_PROVIDER = "mock"
go run . -mode worker
```

Run the four fictional Phase 1 tickets in the second terminal:

```powershell
go run . -mode demo
```

No API key is read or required in mock mode.

## Run one ticket with real Jev

Live inference consumes TypeSafe API usage. Start with the single synthetic ticket provided by `live-demo`; it never submits the four-ticket mock demonstration automatically.

In the worker terminal, set temporary process environment variables and start the worker:

```powershell
Set-Location .\recipes\ticket-routing\go
$env:AI_PROVIDER = "jev"
$env:TYPESAFE_API_KEY = "paste-your-personal-key-here"
go run . -mode worker
```

The worker fails during startup if `TYPESAFE_API_KEY` is missing. The key is read only by the worker and is not printed, written to files, or placed in workflow history.

In a second terminal, explicitly opt into the one-ticket live starter. This terminal does not need the API key:

```powershell
Set-Location .\recipes\ticket-routing\go
$env:AI_PROVIDER = "jev"
go run . -mode live-demo
```

The output identifies the result as real Jev classification and displays department, priority, complexity, all three confidence values, the returned model, reported token usage, selected Child Workflow, fictional employee, and final result.

Remove the temporary worker-terminal variables when finished:

```powershell
Remove-Item Env:AI_PROVIDER -ErrorAction SilentlyContinue
Remove-Item Env:TYPESAFE_API_KEY -ErrorAction SilentlyContinue
```

## First Live Jev Test

The first live integration test used one real Jev API call with a synthetic billing request. It produced these observed results:

| Field | Observed value |
|---|---:|
| Model | `jev-1.13.0` |
| Department | `billing` |
| Department confidence | `1.00` |
| Priority | `high` |
| Priority confidence | `0.97` |
| Complexity | `tier1` |
| Complexity confidence | `0.55` |
| Input tokens | `670` |
| Output tokens | `128` |
| Published Jev input price | `$0.042 / million tokens` |
| Estimated input inference cost | `$0.00002814` |
| Estimated output inference cost | `$0.00` |

The Billing Child Workflow completed successfully, with an observed Child Workflow duration of 28 ms. That duration is neither Jev inference latency nor end-to-end workflow latency. This single-call observation is not a performance benchmark, and the estimated inference cost excludes infrastructure and any additional API calls.

## Reliability and result interpretation

The Jev HTTP call has a finite client timeout and runs inside an Activity with bounded exponential-backoff retries. Network failures, HTTP `429`, HTTP `529`, and retryable `5xx` responses can cause another Activity attempt. Authentication failures, request-schema failures, other client errors, and malformed successful responses are nonretryable.

A failed or timed-out Activity attempt may have reached Jev and can consume additional API usage when Cadence retries it. Only a successfully completed Activity result recorded in workflow history is reused without another inference request during replay.

`UNROUTABLE` is a successful business result: Jev returned a response, but the department label was unsupported or its confidence was below `0.65`, so no department Child Workflow started. A workflow execution failure means classification could not produce a valid result—for example because of authentication, HTTP, timeout, or malformed-response errors. Low-confidence priority or complexity values remain informational and are marked uncertain rather than converted into provider failures.

## Build and test

From `recipes/ticket-routing/go`:

```powershell
gofmt -w main.go workflow.go workflow_test.go
go build ./...
go test ./...
```

Tests cover the existing parent and Child Workflows, all four routing branches, invalid classifications, mock behavior, the complete Jev request structure, response parsing, token usage, missing-key handling, transient and nonretryable HTTP errors, network failures, and malformed responses. Automated tests never contact TypeSafe.

## Copying the recipe

Start with `go/workflow.go` and `go/main.go`. `workflow.go` contains the Workflow, Activities, typed data, mock rules, and small Jev HTTP implementation; `main.go` selects and registers the Activity implementation. No shared repository package or third-party AI SDK is required.

## Deferred features

This phase intentionally does not include acknowledgment Signals, SLA timers, Custom Workflow Controls, automatic employee simulation, synthetic dataset generation, batch execution, metrics, or benchmarks. Those remain specified for later phases in [SPEC.md](SPEC.md).
