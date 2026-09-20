# StreamWave ticket routing — Phase 1

This recipe uses Cadence to route fictional customer-support tickets for StreamWave, a fictional streaming service. A mock Jev classifier assigns a department, priority, and complexity; the parent Workflow then starts the matching department Child Workflow, which assigns a fixed fictional employee.

Phase 1 demonstrates the smallest durable architecture: external classification work runs in an Activity, routing stays deterministic in Workflow code, department handling is isolated in Child Workflows, and typed results flow through the complete execution. The mock makes no network calls and needs no AI credentials.

## Architecture

```text
TicketIntakeWorkflow
  → ClassifyTicket Activity (mock Jev)
  → Billing | Technical | Account | Content Child Workflow
  → fixed fictional employee assignment
  → TicketResult (ASSIGNED or UNROUTABLE)
```

The mock uses documented keyword rules so its output is repeatable. Its confidence values are illustrative and are not model quality, accuracy, token usage, or cost measurements.

## Requirements

- Go 1.23 or newer.
- A local Cadence server for the runnable demonstration.
- A registered Cadence domain named `cadence-ai-samples`.

The Cadence project's public Docker Compose configuration can run a local server and Cadence Web. From a separate clone of [cadence-workflow/cadence](https://github.com/cadence-workflow/cadence):

```powershell
docker compose -f docker/docker-compose.yml up -d
cadence --domain cadence-ai-samples domain register
```

Cadence Web is normally available at <http://localhost:8088>. Unit tests use the SDK test environment and do not need a server.

## Run the example

From the repository root, change into the Go module directory in two PowerShell terminals:

```powershell
Set-Location .\recipes\ticket-routing\go
```

Start the worker in the first terminal:

```powershell
go run . -mode worker
```

Run four fictional example tickets in the second terminal:

```powershell
go run . -mode demo
```

The demo prints each incoming request, its mock classification, selected department Child Workflow, assigned employee, and completed result. Use `-address`, `-domain`, or `-task-list` to override the local defaults.

## Build and test

From `recipes/ticket-routing/go`:

```powershell
go build ./...
go test ./...
```

The tests cover parent execution, all four routing branches, each department Child Workflow, completed assignment results, invalid classifications, and the mock classifier. They require no Jev or other paid-service credentials.

## Why this approach?

The classification boundary is an Activity because model calls are external and nondeterministic. The parent Workflow uses only the recorded typed result and deterministic routing. Separate Child Workflows establish the durable department boundary needed by later phases while keeping Phase 1 deliberately small and easy to copy.

To copy this foundation into another application, start with `go/workflow.go` and `go/main.go`, then replace the mock `ClassifyTicket` Activity implementation with the approved provider-specific implementation. No shared repository package is required.

## Deferred features

Phase 1 intentionally does not include live Jev calls, acknowledgment Signals, SLA timers, Custom Workflow Controls, automatic signal simulation, synthetic dataset generation, batch execution, metrics, or benchmarks. Those remain specified for later phases in [SPEC.md](SPEC.md).
