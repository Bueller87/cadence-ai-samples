# StreamWave ticket routing

This recipe uses Cadence to route fictional customer-support tickets for StreamWave, a fictional streaming service. A classifier assigns a department, priority, and complexity; the parent Workflow then starts the matching department Child Workflow, which assigns a fixed fictional employee.

Select the classifier with `-classifier-id`:

- `mock` is the default. It uses repeatable keyword rules, makes no network calls, and needs no credentials. Its confidence values are examples. They do not measure model quality, accuracy, token usage, or cost.
- Any implemented entry in the repository-root [`classifiers.yaml`](../../classifiers.yaml) is an explicit live opt-in. `jev-default` calls TypeSafe System One and needs `CLASSIFIER_AI_KEY`. `laya-local` calls a local [Laya](../../README.md#local-ai-services) server with no key. Both receive one request containing three independent Choice questions and return choices, probability distributions, confidence values, a model identifier, and token usage.

`-catalog-dir` points at the directory containing `classifiers.yaml` and defaults to the repository root.

## Architecture

Cadence Web provides a Custom Workflow Control (CWC) for acknowledging an assignment. Each assignment also has a service-level agreement (SLA) timer.

```text
TicketIntakeWorkflow
  → ClassifyTicket Activity (mock, Jev, or local Laya)
  → Billing | Technical | Account | Content Child Workflow
  → fixed fictional employee assignment
  → `ticket-assignment` CWC query in Cadence Web
  → wait for matching acknowledgment Signal or durable SLA timer
  → TicketResult (ACKNOWLEDGED, SLA_TIMEOUT, or UNROUTABLE)
```

External classification runs in a Cadence Activity. Workflow code uses only the recorded typed result, so replay does not call the provider again after a successful Activity result has been persisted.

Each assignment has a deterministic identifier. The Child Workflow accepts an acknowledgment only when its ticket ID, employee ID, and assignment ID all match the current assignment. The SLA defaults are 5 seconds for `critical`, 10 seconds for `high`, 20 seconds for `normal`, and 30 seconds for `low`. These durations are demo policy, not real customer SLAs. Use `-sla-critical`, `-sla-high`, `-sla-normal`, and `-sla-low` to configure shorter or longer deadlines for the existing demos. The one-ticket manual demo uses `-manual-sla`, which defaults to two minutes.

## Requirements

- Go 1.23 or newer.
- A local Cadence server for the runnable demonstrations.
- A registered Cadence domain named `cadence-ai-samples`.
- Cadence Web v4.0.14 or newer for Custom Workflow Controls.
- A TypeSafe API key only for the optional live demonstration.

The Cadence project's public Docker Compose configuration can run a local server and Cadence Web. From a separate clone of [cadence-workflow/cadence](https://github.com/cadence-workflow/cadence):

```bash
docker compose -f docker/docker-compose.yml up -d
cadence --domain cadence-ai-samples domain register
```

Cadence Web is normally available at <http://localhost:8088>. Unit tests use the Cadence test environment and local HTTP test servers; they do not need a Cadence server, Jev credentials, or paid API calls. Commands below use Bash. In PowerShell, use `Set-Location` for `cd`, `$env:NAME = "value"` for `export NAME=value`, and `Remove-Item Env:NAME` for `unset NAME`.

## Run with the mock classifier

From the repository root, change into the Go module directory in two terminals:

```bash
cd recipes/ticket-routing/go
```

The mock is selected when `-classifier-id` is omitted or set to `mock`. Start the worker in the first terminal:

```bash
go run . -mode worker
```

Run the four fictional demo tickets in the second terminal:

```bash
go run . -mode demo
```

No API key is read or required in mock mode. Without acknowledgment Signals, these four workflows complete with `SLA_TIMEOUT`.

## Manual acknowledgment demonstration

Use three terminals. From the repository root, change into the Go module directory in each terminal:

```bash
cd recipes/ticket-routing/go
```

In terminal 1, start the worker with mock classification:

```bash
go run . -mode worker
```

In terminal 2, start one synthetic billing ticket with a two-minute acknowledgment SLA:

```bash
go run . -mode start-ticket -ticket-id manual-billing-001 -manual-sla 2m
```

The command prints the parent and Child Workflow IDs, employee ID, assignment ID, Signal name, SLA, and a ready-to-copy acknowledgment command. It then waits and prints the final workflow status. Wait until the Child Workflow appears in Cadence Web, then run this command in terminal 3 before the deadline:

```bash
go run . -mode acknowledge -ticket-id "manual-billing-001" -department "billing" -employee-id "SW-BILLING-101" -assignment-id "manual-billing-001:billing:SW-BILLING-101"
```

Terminal 2 then reports `ACKNOWLEDGED`.

To demonstrate timeout, start a different ticket with a short SLA and do not run the acknowledgment command:

```bash
go run . -mode start-ticket -ticket-id manual-timeout-001 -manual-sla 15s
```

After the durable timer fires, the command reports `SLA_TIMEOUT`. Ticket IDs must be unique for separate demonstrations.

## Cadence Web CWC demonstration

The CWC query follows Cadence's [formatted Markdown response and Signal-button contract](https://cadenceworkflow.io/docs/concepts/workflow-queries-formatted-data) and requires Cadence Web v4.0.14 or newer. It assumes the default local Cadence Web cluster name, `cluster0`.

Use two terminals. In terminal 1, start the mock worker:

```bash
cd recipes/ticket-routing/go
go run . -mode worker
```

In terminal 2, start one ticket with enough time to use Cadence Web:

```bash
cd recipes/ticket-routing/go
go run . -mode start-ticket -ticket-id cwc-billing-001 -manual-sla 2m
```

The starter prints the department Child Workflow ID and waits for the final result. Open <http://localhost:8088> in a browser.

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

```bash
go run . -mode start-ticket -ticket-id cwc-timeout-001 -manual-sla 20s
```

Terminal 2 reports `Business status: SLA_TIMEOUT` and `SLA met: NO`. A timeout still completes the workflow execution successfully. CWC rendering and clicking require live Cadence Web. Automated tests cover the response envelope, Markdown action, payload, shared Signal path, terminal no-action state, and workflow results. Browser rendering requires a manual check.

## Run one ticket with a live classifier

Start with the single synthetic ticket provided by `live-demo`; it never submits the four-ticket mock demonstration automatically. Use the same `-classifier-id` and a dedicated non-default `-task-list` for the worker and the starter.

### Fully local with Laya

No API key or usage cost. Start Laya on port 8008 and warm it up first; see [Local AI services](../../README.md#local-ai-services).

```bash
cd recipes/ticket-routing/go
go run . -mode worker -classifier-id laya-local -task-list ticket-routing-laya
```

In a second terminal:

```bash
cd recipes/ticket-routing/go
go run . -mode live-demo -classifier-id laya-local -task-list ticket-routing-laya
```

### TypeSafe Jev

Live inference consumes TypeSafe API usage. In the worker terminal, set a temporary key and start the worker:

```bash
cd recipes/ticket-routing/go
export CLASSIFIER_AI_KEY='paste-your-personal-key-here'
go run . -mode worker -classifier-id jev-default -task-list ticket-routing-jev
```

The worker fails during startup if `CLASSIFIER_AI_KEY` is missing. The key is read only by the worker and is not printed, written to files, or placed in workflow history. [`.env.example`](go/.env.example) names the variable. The worker does not load that file.

In a second terminal, which does not need the key:

```bash
cd recipes/ticket-routing/go
go run . -mode live-demo -classifier-id jev-default -task-list ticket-routing-jev
```

Remove the key when finished with `unset CLASSIFIER_AI_KEY`.

The output identifies the selected classifier and displays department, priority, complexity, all three confidence values, `priority-uncertain` and `complexity-uncertain`, the returned model, reported token usage, selected Child Workflow, fictional employee, business status, and whether the SLA was met.

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
| Complexity uncertain | `true` |
| Input tokens | `670` |
| Output tokens | `128` |
| Published Jev input price | `$0.042 / million tokens` |
| Estimated input inference cost | `$0.00002814` |
| Estimated output inference cost | `$0.00` |

Complexity confidence `0.55` is below the `0.65` informational threshold, so this result is `complexity-uncertain`. The chosen `tier1` value stays on the result. Department confidence `1.00` and priority confidence `0.97` are not uncertain. The Billing Child Workflow completed successfully, with an observed duration of 28 ms before the acknowledgment wait was added. That duration does not measure Jev inference or the full workflow. This single call does not provide enough data for a performance benchmark. The estimated inference cost excludes infrastructure and any additional API calls. This result is historical evidence from the public POC, preserved because live Jev is not rerun from this machine.

## Reliability and result interpretation

The live classifier HTTP call has a finite client timeout and runs inside an Activity with bounded exponential-backoff retries. Network failures, HTTP `429`, HTTP `529`, and retryable `5xx` responses can cause another Activity attempt. Authentication failures, request-schema failures, other client errors, and malformed successful responses are nonretryable.

A failed or timed-out Activity attempt may have reached the classifier and can consume additional API usage when Cadence retries it. Only a successfully completed Activity result recorded in workflow history is reused without another inference request during replay.

`UNROUTABLE` is a successful business result. The classifier returned a response, but the department label was unsupported or its confidence was below `0.65`, so no department Child Workflow started. A workflow execution fails when classification cannot produce a valid result because of authentication, HTTP, timeout, or malformed-response errors. Low-confidence priority or complexity values remain informational and are marked uncertain instead of failing the Activity.

For a routed ticket, `ACKNOWLEDGED` means the Child Workflow received a valid matching Signal before its SLA timer fired and returns `sla_met: true`. `SLA_TIMEOUT` means the durable timer won and returns `sla_met: false`, after which the workflow completes without reassignment or escalation. Both are completed workflow executions. If a Signal and timer are both ready on the same Workflow task, timeout wins deterministically.

## Synthetic ticket dataset

[testdata/tickets.jsonl](testdata/tickets.jsonl) contains 40 fictional StreamWave requests, balanced across billing, technical, account, and content. Each JSON Lines (JSONL) record uses the runtime `ticket_id` and `message` fields plus test-only `expected_department`, `expected_priority`, and `expected_complexity` labels. The fixture includes varied lengths and writing styles, misspellings, incomplete requests, and ambiguous or multi-intent cases.

The expected labels are provisional. Review them, especially the ambiguous cases, before using them as ground truth in classification-accuracy benchmarks or quality claims. [PROMPT.md](PROMPT.md) provides a reusable prompt for generating additional schema-compatible synthetic requests without adding a generation dependency to the application.

Validate the included fixture from `recipes/ticket-routing/go`:

```bash
go test ./... -run TestSyntheticTicketDataset
```

This checks JSONL parsing, the exact 40-record count, unique ticket IDs, valid labels, and the department distribution.

## Bounded mock batch

The `batch` mode starts one independent `TicketIntakeWorkflow` per fixture record. It validates the JSONL dataset before submission, repeats the unchanged 40-record fixture when `-count` is larger than 40, and gives every execution a unique ticket and workflow ID. The expected labels are only provisional fixture metadata: the runner does not use them to route tickets or calculate classification accuracy.

Start the mock worker in one terminal, from the repository root:

```bash
cd recipes/ticket-routing/go
go run . -mode worker
```

In another terminal, change to the same `go` directory and run 40 tickets with the default one-second SLA and at most 10 workflows in flight:

```bash
cd recipes/ticket-routing/go
go run . -mode batch -count 40 -concurrency 10 -batch-sla 1s
```

To repeat the fixture and run 1,000 mock tickets with a bounded 25-workflow concurrency:

```bash
cd recipes/ticket-routing/go
go run . -mode batch -count 1000 -concurrency 25 -batch-sla 1s
```

No acknowledgment Signals are sent in batch mode. Routed tickets therefore complete with `SLA_TIMEOUT`; workflow-engine failures are counted separately. The terminal summary reports submitted, completed, failed, `UNROUTABLE`, `ACKNOWLEDGED`, and `SLA_TIMEOUT` totals. It also reports department totals, peak in-flight executions, wall-clock duration, per-execution client wait times, and completed workflows per second. Every failed execution includes its workflow ID, ticket ID, start time, elapsed time, and Cadence error. The one-second business SLA is demo policy, not a real customer SLA, and it does not impose a one-second workflow execution timeout. Bounded parent and Child Workflow timeouts include scheduling and execution overhead so delayed workflow tasks can still process the durable SLA timer. Use this batch to try the flow. It does not measure performance, classification accuracy, provider cost, or latency.

## Explicit live classifier batch

`live-batch` reuses the same bounded worker pool and synthetic dataset, but classification runs through the selected live classifier Activity. It requires a live `-classifier-id`, explicit `-count` and `-concurrency` flags, a dedicated non-default task list, and `-confirm-live`. One invocation is limited in code to 10 tickets and five concurrent workflows. The mock `batch` mode remains mock-only and retains its existing defaults.

Start the live worker in terminal 1. For `jev-default`, the API key stays in this worker process and is sent only in the authorized HTTP request. For a keyless local run, use `-classifier-id laya-local -task-list ticket-routing-laya` in both terminals instead. Public command examples use Bash:

```bash
cd recipes/ticket-routing/go
export CLASSIFIER_AI_KEY='paste-your-personal-key-here'
go run . -mode worker -classifier-id jev-default -task-list ticket-routing-jev
```

For the first controlled live test, start three tickets at concurrency one in terminal 2:

```bash
cd recipes/ticket-routing/go
go run . -mode live-batch -classifier-id jev-default -count 3 -concurrency 1 -batch-sla 1s -task-list ticket-routing-jev -confirm-live
```

Dataset selection is sequential by default, preserving file order. Because the fixture is grouped by provisional department label, use `-sample balanced` for a small representative live run. Balanced selection deterministically takes the first unused fixture for billing, technical, account, and content, in that order, then repeats that department order if more tickets are requested. The provisional labels are used only to choose fixture records; they are not included in the workflow input, sent to the classifier, used as routing instructions, or treated as validated ground truth.

For one fixture from each provisional department group and an ordered classification report:

```bash
cd recipes/ticket-routing/go
go run . -mode live-batch -classifier-id jev-default -count 4 -concurrency 1 -batch-sla 1s -task-list ticket-routing-jev -sample balanced -show-classifications -confirm-live
```

`-show-classifications` prints completed tickets in dataset submission order with the classified department, priority, complexity, their confidences, `priority-uncertain`, `complexity-uncertain`, model, business outcome, and classifier request/response latency. Technical failures show only the ticket ID and failure status. It never prints API keys or full ticket messages. Without the flag, the existing live summary is unchanged.

The optional `-input-token-price-per-million` flag accepts a user-supplied input-token price for an illustrative successful-response estimate. For example, append `-input-token-price-per-million 0.042` only after checking the price you intend to use. No price is built into the sample.

The live report keeps three measurements distinct:

- **Classifier HTTP inference latency** measures the successful HTTP attempt inside the classification Activity, including response receipt and parsing.
- **Per-ticket end-to-end client wait** runs from client submission through terminal parent Workflow completion.
- **Business SLA duration** is the Child Workflow acknowledgment timer.

The report aggregates provider-reported input and output tokens and counts successful responses whose complete usage information is missing. Cost calculations cover reported input tokens from successful recorded responses only and exclude Cadence infrastructure. A failed or retried Activity may have consumed additional API usage, so completed workflows do not establish the number of billed attempts and the estimate may be lower than actual billed usage. The provisional expected labels in the synthetic fixture are not used to claim classification accuracy.

### Final local live Jev run

Kevin's final local functional run used 10 synthetic tickets with `-sample balanced` at concurrency 2. It is historical evidence from [cadence-ai-samples-poc pull request 2](https://github.com/Bueller87/cadence-ai-samples-poc/pull/2) and was not repeated here. No acknowledgment Signals were sent, so every routed ticket completed with the expected business outcome of `SLA_TIMEOUT`.

| Field | Observed value |
|---|---:|
| Completed executions | 10 |
| Technical failures | 0 |
| `SLA_TIMEOUT` outcomes | 10 |
| Batch duration | 6.38 s |
| Average end-to-end client wait | 1.275 s |
| Jev request/response latency | min 102 ms, avg 168 ms, max 269 ms |
| Input tokens | 6,707 |
| Output tokens | 1,280 |
| Successful responses missing complete token usage | 0 |
| Reported model | `jev-1.13.0` |

This is a small local functional test, not a production throughput benchmark or a validated classification-accuracy benchmark. The fixture's expected labels remain provisional, and a reported model confidence is not proof that a classification is correct.

## Build and test

From `recipes/ticket-routing/go`:

```bash
gofmt -w .
go build -o /dev/null .
go test ./...
```

`go build ./...` also succeeds. In this directory that command writes a binary named `go`, which is gitignored. Prefer `-o /dev/null` or another explicit output path.

Tests cover:

- CWC responses, acknowledgment commands, timer cancellation, SLA results, and the signal/deadline race.
- Parent and Child Workflows, all four routes, `UNROUTABLE` results, acknowledgment, timeout, and invalid or duplicate Signals.
- Mock classification, dataset validation, repeated fixtures, bounded concurrency, invalid batch configuration, aggregation, and failures.
- Classifier catalog selection and validation, keyless Laya requests, Jev requests, response parsing, token usage, missing keys, HTTP errors, network failures, and malformed responses.

Automated tests never contact Cadence or TypeSafe.

## Copying the recipe

Start with `go/workflow.go`, `go/main.go`, and `go/catalog.go`. `workflow.go` contains the Workflow, Activities, typed data, mock rules, and small System One HTTP client; `catalog.go` loads `classifiers.yaml`; `main.go` selects and registers the Activity implementation. No shared repository package or third-party AI client library is required.

## Non-goals

Automatic acknowledgment simulation and classification-accuracy benchmarking are non-goals. The recipe also does not include reassignment, skill matching, workload balancing, multi-level escalation, or production performance claims. Provisional labels in [testdata/tickets.jsonl](testdata/tickets.jsonl) are not ground truth.
