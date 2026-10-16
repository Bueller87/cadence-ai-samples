# StreamWave ticket routing

This flagship recipe classifies one fictional StreamWave support ticket, starts
the matching department Child Workflow, assigns a fixed fictional employee, and
waits for acknowledgment or a durable service-level agreement (SLA) timer.

The Go/SystemOne implementation is independently runnable from
`go/systemone/`. It exposes the standard profile actions, `worker` and `start`,
with explicit `mock` and `live` modes.

## What the Workflow does

`TicketIntakeWorkflow` receives a typed ticket containing the selected ticket ID
and message. One classification Activity asks three independent choice
questions in one request:

- department: billing, technical, account, or content;
- priority: low, normal, high, or critical;
- complexity: tier1, tier2, or tier3.

A routable result starts the matching department Child Workflow. Billing,
Technical, Account, and Content assign `SW-BILLING-101`, `SW-TECH-202`,
`SW-ACCOUNT-303`, and `SW-CONTENT-404`, respectively. The Child Workflow waits
for a matching `acknowledge-assignment` Signal or its SLA timer and returns
`ACKNOWLEDGED` or `SLA_TIMEOUT`. Unsupported or low-confidence department
results return `UNROUTABLE` without starting a Child Workflow.

The parent exposes a read-only `assignment-status` Query. While classification
is running it reports `CLASSIFICATION_PENDING`. Once routed, it reports the
Child Workflow ID, employee ID, assignment ID, Signal name, and SLA. The starter
polls this Query for a bounded period, prints those values, and then waits for
the terminal result.

## Requirements

- Go 1.23 or newer.
- A local Cadence server with the `cadence-ai-samples` domain.
- Cadence Web v4.0.14 or newer for Custom Workflow Controls (CWC).
- Laya on `localhost:8008` only for the optional local live path.
- `CLASSIFIER_AI_KEY` only for an explicitly authorized TypeSafe run.

From a separate clone of
[cadence-workflow/cadence](https://github.com/cadence-workflow/cadence):

```bash
docker compose -f docker/docker-compose.yml up -d
cadence --domain cadence-ai-samples domain register
```

Cadence Web is normally available at <http://localhost:8088>.

## Run offline with the mock classifier

Use two terminals:

```bash
cd recipes/ticket-routing/go/systemone
go run . worker -mode mock
```

```bash
cd recipes/ticket-routing/go/systemone
go run . start -mode mock
```

The starter randomly selects one record from `testdata/tickets.jsonl`, prints
its ID and message, and records both as Workflow input. Selection may repeat
between processes. The Workflow and Worker never select or mutate the input.

For a reproducible custom request, use the profile-supported `-text` flag:

```bash
go run . start -mode mock \
  -workflow-id ticket-routing-custom-demo \
  -text "I was charged twice for my StreamWave subscription."
```

The default Workflow ID is `ticket-routing-demo`. Cadence allows reuse after a
closed Run but rejects a second open Run with the same ID. Pass a different
`-workflow-id` when another default Run is still open.

## Acknowledge an assignment

The starter prints the classified Child Workflow ID, fixed employee ID,
assignment ID, Signal name, and SLA before waiting for completion.

### Local Samples Explorer

Run `./scripts/explore.sh` from the repository root, select Ticket Routing and
mock mode, start the generated commands, and resolve the parent Run. Open the
resolved Run's **Queries** link in Cadence Web.

### Cadence Web CWC

Run the parent's `ticket-assignment` Query and click **Acknowledge Ticket**.
This primary path sends the complete typed `acknowledge-assignment` payload to
the parent, which forwards the same Signal and payload to the current Child.
The Child validates all identifiers.

As an alternate path, open the Child Workflow ID printed by the starter, run
its `ticket-assignment` Query, and click **Acknowledge Ticket**. The Child CWC
sends the same Signal and payload directly to that Child Workflow.

Both paths use:

```json
{
  "ticket_id": "the selected ticket ID",
  "employee_id": "the printed employee ID",
  "assignment_id": "the printed assignment ID"
}
```

Invalid, duplicate, and late Signals do not change the result. If the Signal
and timer are ready in the same Workflow task, timeout wins deterministically.

The interactive demo defaults are two minutes for critical, three for high,
four for normal, and five for low priority. They are demonstration policy, not
real customer SLAs. Tests inject short durations.

## Run with local Laya

Start and warm Laya as described in the
[repository README](../../README.md#local-ai-services), then use a dedicated
task list in both terminals:

```bash
cd recipes/ticket-routing/go/systemone
go run . worker -mode live \
  -classifier-id laya-local \
  -task-list ticket-routing-laya \
  -confirm-live
```

```bash
cd recipes/ticket-routing/go/systemone
go run . start -mode live -task-list ticket-routing-laya
```

The starter reads no classifier catalog or credentials. Live configuration
belongs only to the Worker.

Laya reports two different confidence values for each choice answer:

- `answer_confidence` is the selected choice's probability. Ticket Routing
  records it as the decision confidence and routes departments at `0.40` or
  higher.
- `confidence` is Laya's normalized distribution-entropy score. Ticket Routing
  preserves it separately as `*_entropy_confidence`; it does not use or label
  that score as selected-answer confidence.

The adapter requires `answer_confidence` to match the selected entry in
`probabilities` and fails closed on missing, out-of-range, or inconsistent
confidence data. TypeSafe/Jev keeps its existing `confidence` semantics and
`0.65` department threshold.

Local corpus results vary with model output. A maintainer run routed 35 of 40
fixtures at `0.40`, with 32 routed choices matching the provisional department
labels. A subsequent full validation run routed 37 of 40, again with 32 label
matches; the same run routed 27 at `0.50` and 14 at `0.65`. These figures
describe usability, not accuracy guarantees.

## Optional TypeSafe SystemOne

This path consumes external API usage and must be explicitly authorized:

```bash
cd recipes/ticket-routing/go/systemone
export CLASSIFIER_AI_KEY='your-personal-key'
go run . worker -mode live \
  -classifier-id jev-default \
  -task-list ticket-routing-jev \
  -confirm-live
```

```bash
go run . start -mode live -task-list ticket-routing-jev
unset CLASSIFIER_AI_KEY
```

The key is read only by the Worker. It is not printed, written to Workflow
history, or loaded from `.env.example`.

## Reliability and result interpretation

Classifier HTTP runs in a Cadence Activity with finite timeouts and bounded
retries. Network failures, rate limits, provider overload, and retryable server
errors may repeat an inference attempt. Authentication, request-schema, client,
and malformed-response errors are nonretryable. A completed Activity result is
recorded and reused during replay, so replay does not call the classifier again.

`ACKNOWLEDGED`, `SLA_TIMEOUT`, and `UNROUTABLE` are successful business results.
Provider or Workflow failures remain execution errors.

## Synthetic dataset

[`testdata/tickets.jsonl`](testdata/tickets.jsonl) contains 40 fictional tickets,
ten for each provisional department label. The labels support fixture checks;
they are not independently validated ground truth and are not used for routing.
Short or cross-department requests remain intentionally ambiguous, and local
Laya may choose another department or return `UNROUTABLE`. The messages are not
padded or rewritten to steer the model toward their provisional labels.
[`PROMPT.md`](PROMPT.md) describes how to create more synthetic-only records.

## Build and test

```bash
cd recipes/ticket-routing/go/systemone
gofmt -w .
go test ./...
go build ./...
```

Tests run offline and cover the dataset, starter selection and override,
classifier transport, all routes, confidence handling, parent assignment Query,
parent forwarding, Child Signal validation, parent and Child CWC, SLA races,
and replay.

## Copying the recipe

Copy `go/systemone/main.go`, `workflow.go`, and `catalog.go` with their module
dependencies. The implementation imports no other recipe.

## Non-goals

This sample does not implement batch execution, throughput or cost reporting,
classification benchmarking, reassignment, workload balancing, multi-level
escalation, or full ticket resolution.
