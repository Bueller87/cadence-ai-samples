# StreamWave ticket routing

This recipe uses Cadence to route fictional customer-support tickets for StreamWave, a fictional streaming service. A classifier assigns a department, priority, and complexity; the parent Workflow then starts the matching department Child Workflow, which assigns a fixed fictional employee.

The recipe supports two classifier modes:

- `mock` is the default. It uses repeatable keyword rules, makes no network calls, and needs no credentials. Its confidence values are examples. They do not measure model quality, accuracy, token usage, or cost.
- `jev` is an explicit opt-in for TypeSafe System One. It sends one request containing three independent Choice questions and records the returned choices, probability distributions, confidence values, model identifier, and token usage.

## Architecture

Cadence Web provides a Custom Workflow Control (CWC) for acknowledging an assignment. Each assignment also has a service-level agreement (SLA) timer.

```text
TicketIntakeWorkflow
  → ClassifyTicket Activity (mock or real Jev)
  → Billing | Technical | Account | Content Child Workflow
  → fixed fictional employee assignment
  → `ticket-assignment` CWC query in Cadence Web
  → wait for matching acknowledgment Signal or durable SLA timer
  → TicketResult (ACKNOWLEDGED, SLA_TIMEOUT, or UNROUTABLE)
```

External classification runs in a Cadence Activity. Workflow code uses only the recorded typed result, so replay does not call the provider again after a successful Activity result has been persisted.

Each assignment has a deterministic identifier. The Child Workflow accepts an acknowledgment only when its ticket ID, employee ID, and assignment ID all match the current assignment. The SLA defaults are 5 seconds for `critical`, 10 seconds for `high`, 20 seconds for `normal`, and 30 seconds for `low`; use `-sla-critical`, `-sla-high`, `-sla-normal`, and `-sla-low` to configure shorter or longer deadlines for the existing demos. The one-ticket manual demo uses `-manual-sla`, which defaults to two minutes.

## Requirements

- Go 1.23 or newer.
- A local Cadence server for the runnable demonstrations.
- A registered Cadence domain named `cadence-ai-samples`.
- Cadence Web v4.0.14 or newer for Custom Workflow Controls.
- A TypeSafe API key only for the optional live demonstration.

The Cadence project's public Docker Compose configuration can run a local server and Cadence Web. From a separate clone of [cadence-workflow/cadence](https://github.com/cadence-workflow/cadence):

```powershell
docker compose -f docker/docker-compose.yml up -d
cadence --domain cadence-ai-samples domain register
```

Cadence Web is normally available at <http://localhost:8088>. Unit tests use the Cadence test environment and local HTTP test servers; they do not need a Cadence server, Jev credentials, or paid API calls.

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

Run the four fictional demo tickets in the second terminal:

```powershell
go run . -mode demo
```

No API key is read or required in mock mode. Without acknowledgment Signals, these four workflows complete with `SLA_TIMEOUT`.

## Manual acknowledgment demonstration

Use three PowerShell terminals. From the repository root, change into the Go module directory in each terminal:

```powershell
Set-Location .\recipes\ticket-routing\go
```

In terminal 1, start the worker with mock classification:

```powershell
$env:AI_PROVIDER = "mock"
go run . -mode worker
```

In terminal 2, start one synthetic billing ticket with a two-minute acknowledgment SLA:

```powershell
$env:AI_PROVIDER = "mock"
go run . -mode start-ticket -ticket-id manual-billing-001 -manual-sla 2m
```

The command prints the parent and Child Workflow IDs, employee ID, assignment ID, Signal name, SLA, and a ready-to-copy acknowledgment command. It then waits and prints the final workflow status. Wait until the Child Workflow appears in Cadence Web, then run this command in terminal 3 before the deadline:

```powershell
go run . -mode acknowledge -ticket-id "manual-billing-001" -department "billing" -employee-id "SW-BILLING-101" -assignment-id "manual-billing-001:billing:SW-BILLING-101"
```

Terminal 2 then reports `ACKNOWLEDGED`.

To demonstrate timeout, start a different ticket with a short SLA and do not run the acknowledgment command:

```powershell
go run . -mode start-ticket -ticket-id manual-timeout-001 -manual-sla 15s
```

After the durable timer fires, the command reports `SLA_TIMEOUT`. Ticket IDs must be unique for separate demonstrations.

## Cadence Web CWC demonstration

The CWC query follows Cadence's [formatted Markdown response and Signal-button contract](https://cadenceworkflow.io/docs/concepts/workflow-queries-formatted-data) and requires Cadence Web v4.0.14 or newer. It assumes the default local Cadence Web cluster name, `cluster0`.

Use two PowerShell terminals. In terminal 1, start the mock worker:

```powershell
Set-Location .\recipes\ticket-routing\go
$env:AI_PROVIDER = "mock"
go run . -mode worker
```

In terminal 2, start one ticket with enough time to use Cadence Web:

```powershell
Set-Location .\recipes\ticket-routing\go
$env:AI_PROVIDER = "mock"
go run . -mode start-ticket -ticket-id cwc-billing-001 -manual-sla 2m
```

The starter prints the department Child Workflow ID and waits for the final result. Open Cadence Web from PowerShell:

```powershell
Start-Process "http://localhost:8088"
```

In Cadence Web, select the `cadence-ai-samples` domain, open the Child Workflow ID printed by the starter (`ticket-routing-cwc-billing-001-child-billing`), open its **Queries** tab, select `ticket-assignment`, and run the query. Confirm the ticket, department, employee, priority, SLA duration/deadline, and `AWAITING_ACKNOWLEDGMENT` status, then click **Acknowledge Ticket**.

The button sends `acknowledge-assignment` to that Child Workflow with the same ticket, employee, and assignment identifiers used by the command-line interface (CLI). It acknowledges the employee assignment; it does not resolve the customer's support request. Terminal 2 then displays:

```text
Workflow status: Completed
Ticket ID: cwc-billing-001
Department: billing
Assigned employee: SW-BILLING-101
Business status: ACKNOWLEDGED
SLA met: YES
```

Cadence Web shows the Child and parent Workflows as completed. Running `ticket-assignment` against the completed Child Workflow returns terminal Markdown without an acknowledgment button.

To demonstrate the missed-SLA outcome, start a new ticket and do not click the button:

```powershell
go run . -mode start-ticket -ticket-id cwc-timeout-001 -manual-sla 20s
```

Terminal 2 reports `Business status: SLA_TIMEOUT` and `SLA met: NO`. A timeout still completes the workflow execution successfully. CWC rendering and clicking require live Cadence Web. Automated tests cover the response envelope, Markdown action, payload, shared Signal path, terminal no-action state, and workflow results. Browser rendering requires a manual check.

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

The output identifies the result as real Jev classification and displays department, priority, complexity, all three confidence values, the returned model, reported token usage, selected Child Workflow, fictional employee, business status, and whether the SLA was met.

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

The Billing Child Workflow completed successfully, with an observed duration of 28 ms before the acknowledgment wait was added. That duration does not measure Jev inference or the full workflow. This single call does not provide enough data for a performance benchmark. The estimated inference cost excludes infrastructure and any additional API calls.

## Reliability and result interpretation

The Jev HTTP call has a finite client timeout and runs inside an Activity with bounded exponential-backoff retries. Network failures, HTTP `429`, HTTP `529`, and retryable `5xx` responses can cause another Activity attempt. Authentication failures, request-schema failures, other client errors, and malformed successful responses are nonretryable.

A failed or timed-out Activity attempt may have reached Jev and can consume additional API usage when Cadence retries it. Only a successfully completed Activity result recorded in workflow history is reused without another inference request during replay.

`UNROUTABLE` is a successful business result. Jev returned a response, but the department label was unsupported or its confidence was below `0.65`, so no department Child Workflow started. A workflow execution fails when classification cannot produce a valid result because of authentication, HTTP, timeout, or malformed-response errors. Low-confidence priority or complexity values remain informational and are marked uncertain instead of failing the Activity.

For a routed ticket, `ACKNOWLEDGED` means the Child Workflow received a valid matching Signal before its SLA timer fired and returns `sla_met: true`. `SLA_TIMEOUT` means the durable timer won and returns `sla_met: false`, after which the workflow completes without reassignment or escalation. Both are completed workflow executions. If a Signal and timer are both ready on the same Workflow task, timeout wins deterministically.

## Synthetic ticket dataset

[testdata/tickets.jsonl](testdata/tickets.jsonl) contains 40 fictional StreamWave requests, balanced across billing, technical, account, and content. Each JSON Lines (JSONL) record uses the runtime `ticket_id` and `message` fields plus test-only `expected_department`, `expected_priority`, and `expected_complexity` labels. The fixture includes varied lengths and writing styles, misspellings, incomplete requests, and ambiguous or multi-intent cases.

The expected labels are provisional. Review them, especially the ambiguous cases, before using them as ground truth in classification-accuracy benchmarks or quality claims. [PROMPT.md](PROMPT.md) provides a reusable prompt for generating additional schema-compatible synthetic requests without adding a generation dependency to the application.

Validate the included fixture from `recipes/ticket-routing/go`:

```powershell
go test ./... -run TestSyntheticTicketDataset
```

This checks JSONL parsing, the exact 40-record count, unique ticket IDs, valid labels, and the department distribution.

## Bounded mock batch

The `batch` mode starts one independent `TicketIntakeWorkflow` per fixture record. It validates the JSONL dataset before submission, repeats the unchanged 40-record fixture when `-count` is larger than 40, and gives every execution a unique ticket and workflow ID. The expected labels are only provisional fixture metadata: the runner does not use them to route tickets or calculate classification accuracy.

Start the mock worker in one PowerShell terminal, from the repository root:

```powershell
Set-Location .\recipes\ticket-routing\go
$env:AI_PROVIDER = "mock"
go run . -mode worker
```

In another terminal, change to the same `go` directory and run 40 tickets with the default one-second SLA and at most 10 workflows in flight:

```powershell
Set-Location .\recipes\ticket-routing\go
go run . -mode batch -count 40 -concurrency 10 -batch-sla 1s
```

To repeat the fixture and run 1,000 mock tickets with a bounded 25-workflow concurrency:

```powershell
Set-Location .\recipes\ticket-routing\go
go run . -mode batch -count 1000 -concurrency 25 -batch-sla 1s
```

No acknowledgment Signals are sent in batch mode. Routed tickets therefore complete with `SLA_TIMEOUT`; workflow-engine failures are counted separately. The terminal summary reports submitted, completed, failed, `UNROUTABLE`, `ACKNOWLEDGED`, and `SLA_TIMEOUT` totals. It also reports department totals, peak in-flight executions, wall-clock duration, per-execution client wait times, and completed workflows per second. Every failed execution includes its workflow ID, ticket ID, start time, elapsed time, and Cadence error. The one-second business SLA does not impose a one-second workflow execution timeout. Bounded parent and Child Workflow timeouts include scheduling and execution overhead so delayed workflow tasks can still process the durable SLA timer. Use this batch to try the flow. It does not measure performance, classification accuracy, provider cost, or latency.

## Build and test

From `recipes/ticket-routing/go`:

```powershell
gofmt -w main.go workflow.go workflow_test.go batch.go batch_test.go
go build ./...
go test ./...
```

Tests cover:

- CWC responses, acknowledgment commands, timer cancellation, SLA results, and the signal/deadline race.
- Parent and Child Workflows, all four routes, `UNROUTABLE` results, acknowledgment, timeout, and invalid or duplicate Signals.
- Mock classification, dataset validation, repeated fixtures, bounded concurrency, invalid batch configuration, aggregation, and failures.
- Jev requests, response parsing, token usage, missing keys, HTTP errors, network failures, and malformed responses.

Automated tests never contact Cadence or TypeSafe.

## Copying the recipe

Start with `go/workflow.go` and `go/main.go`. `workflow.go` contains the Workflow, Activities, typed data, mock rules, and small Jev HTTP implementation; `main.go` selects and registers the Activity implementation. No shared repository package or third-party AI client library is required.

## Deferred features

The recipe still intentionally does not include automatic employee acknowledgment simulation, reassignment, skill matching, workload balancing, multi-level escalation, classification-accuracy scoring, provider cost or latency benchmarking, or production performance claims. Those remain deferred to later phases.
