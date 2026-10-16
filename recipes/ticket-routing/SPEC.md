# StreamWave ticket routing implementation specification

## Purpose

Demonstrate why Cadence remains useful after an AI classification decision:
durable department Child Workflows, fixed employee assignment, validated
acknowledgment Signals, SLA timers, replay, and Cadence Web Custom Workflow
Controls.

The sample is independently copyable and uses only synthetic StreamWave support
tickets.

## Repository implementation profile

- **Cadence language:** Go
- **Classifier integration:** SystemOne
- **Agent integration:** none
- **Implementation directory:** `go/systemone`
- **CLI contract:** positional `worker|start` with required `-mode mock|live`
- **Mock behavior:** deterministic local classification without catalogs,
  credentials, or provider calls
- **Live behavior:** a confirmed live Worker selects `jev-default` or
  `laya-local` from the repository classifier catalog
- **Workflow ID reuse:** stable `ticket-routing-demo` with
  `ALLOW_DUPLICATE`; an open duplicate remains rejected
- **Profile deviations:** a text-less start selects a bundled fixture, the
  Workflow input is a typed ticket instead of generic text, and the business
  workflow includes Child Workflows, Queries, Signals, and SLA timers

## Layout

```text
recipes/ticket-routing/
├── README.md
├── SPEC.md
├── PROMPT.md
├── explorer.yaml
├── testdata/
│   └── tickets.jsonl
└── go/
    └── systemone/
        ├── .env.example
        ├── go.mod
        ├── go.sum
        ├── main.go
        ├── main_test.go
        ├── catalog.go
        ├── workflow.go
        └── workflow_test.go
```

The implementation must not import another recipe or a shared AI integration
layer.

## Input selection

Each parent Workflow receives one `Ticket{ticket_id,message,acknowledgment_sla}`
as explicit input.

- When `-text` is omitted, the external starter selects one random record from
  the 40-ticket JSON Lines fixture and prints its ID and message.
- Selection may repeat across processes. No persistent or Workflow-local
  round-robin state is permitted.
- When `-text` is present, the starter derives a deterministic synthetic ID and
  uses the supplied text.
- File I/O and randomness remain in the starter. The Worker and Workflow do not
  choose or mutate input.
- Activity retries and replay retain the ticket recorded in Workflow history.

## Classification Activity

One `ClassifyTicket` Activity request contains three independent `choice`
questions:

| Question | Allowed values |
| --- | --- |
| department | `billing`, `technical`, `account`, `content` |
| priority | `low`, `normal`, `high`, `critical` |
| complexity | `tier1`, `tier2`, `tier3` |

The result records choices, probabilities, provider-specific confidence values,
model, and token usage when reported. Provider parsing stays inside Activity
code.

TypeSafe/Jev uses its `confidence` field as selected-answer confidence and keeps
the `0.65` department threshold. Laya uses `answer_confidence`, the selected
choice probability, and routes departments at `0.40` or higher. Laya's raw
`confidence` value is normalized distribution entropy; it is preserved
separately as `*_entropy_confidence` and never used or described as
selected-answer confidence. For Laya, `answer_confidence` is required to equal
the selected entry in `probabilities`. Missing, out-of-range, or inconsistent
confidence data is a nonretryable malformed-response error.

An unsupported choice or provider-specific department confidence below its
threshold returns `UNROUTABLE` without a Child Workflow. Low priority or
complexity selected-answer confidence sets informational uncertainty flags
without changing the chosen value.

Mock mode uses deterministic local rules. Live mode uses one catalog-selected
SystemOne request per Activity attempt. TypeSafe requires
`CLASSIFIER_AI_KEY`; Laya requires a loopback endpoint and no key.

## Workflow topology

```text
TicketIntakeWorkflow
  -> ClassifyTicket Activity
  -> UNROUTABLE, or one department Child Workflow
  -> fixed employee assignment
  -> acknowledgment Signal or durable SLA timer
  -> ACKNOWLEDGED or SLA_TIMEOUT
```

The four Child Workflows are Billing, Technical, Account, and Content. Their
fixed employee IDs are `SW-BILLING-101`, `SW-TECH-202`, `SW-ACCOUNT-303`, and
`SW-CONTENT-404`.

The caller-provided parent Workflow ID is stable. Child IDs append
`-child-<department>`. Assignment IDs combine ticket ID, department, and
employee ID.

## Queries, Signals, and CWC

The parent registers a read-only `assignment-status` Query before classification.
It returns:

- `CLASSIFICATION_PENDING` while the Activity is unresolved;
- `UNROUTABLE` and a reason when no Child starts; or
- `AWAITING_ACKNOWLEDGMENT` with Child Workflow ID, department, employee,
  assignment ID, `acknowledge-assignment` Signal name, and SLA.

After the Child completes, the same typed Query reports `ACKNOWLEDGED` or
`SLA_TIMEOUT` and whether the SLA was met.

The starter polls this Query at a fixed interval for a bounded period. It prints
the routed assignment before waiting for terminal completion. A polling timeout
does not create an unbounded loop.

The Child Workflow owns assignment state and validates
`AcknowledgmentSignal{ticket_id,employee_id,assignment_id}`. The parent
`ticket-assignment` Query returns Cadence formatted Markdown with an
**Acknowledge Ticket** action while an assignment is pending. The action sends
the complete typed Signal payload to the parent, which deterministically
forwards it to the current Child, where the same validation runs. The generic
Explorer resolves the parent and links to its Cadence Web Queries page; it does
not own a Ticket Routing Signal form.

The Child `ticket-assignment` Query provides an alternate direct CWC path with
the same action and payload. Parent and Child formatted Queries have no action
after completion. Invalid, duplicate, and late Signals are ignored. Timeout
wins if Signal and timer are ready in the same Workflow task.

## SLA policy

Interactive defaults are:

- critical: 2 minutes
- high: 3 minutes
- normal: 4 minutes
- low: 5 minutes

These are demonstration policy, not customer commitments. SLA configuration is
typed and internal; the CLI exposes no SLA flags. Tests inject short durations.
Workflow code uses Cadence timers and time APIs, never wall-clock sleeps.

## CLI and configuration

The only actions are:

```text
./run.sh worker -mode mock
./run.sh start -mode mock
./run.sh worker -mode live -classifier-id laya-local -confirm-live
./run.sh start -mode live
```

Shared flags are `-address`, `-domain`, and `-task-list`. Start-only flags are
`-workflow-id` and `-text`. Worker-only live flags are `-classifier-id`,
`-catalog-dir`, and `-confirm-live`. Starts never read catalogs, credentials, or
classifier configuration.

No `demo`, `start-ticket`, `acknowledge`, `batch`, `live-demo`, `live-batch`,
concurrency, SLA, metric, token-price, or benchmark CLI is allowed.

## Failure and replay behavior

Classifier network failures, rate limits, overload, and retryable server errors
remain retryable within bounded Activity policy. Authentication, validation,
other client failures, and malformed successful responses are nonretryable.

A failed Activity attempt may have reached the provider. Only a completed
Activity result recorded in history is replay-safe without another inference
request. Replay tests must prove that the recorded classification and selected
ticket are reused.

## Dataset

`testdata/tickets.jsonl` contains exactly 40 unique synthetic tickets with valid
provisional labels and ten records per department. Labels are test fixture
metadata, not independently validated ground truth. Runtime input contains only
ticket ID, message, and internal SLA configuration.

Ambiguous and cross-department fixtures are retained as usability limitations;
their messages must not be padded to steer a specific model. In one maintainer
Laya corpus run, the `0.40` policy routed 35 of 40 fixtures and 32 routed
choices matched provisional department labels. A subsequent validation run
routed 37 of 40 with 32 matches; it routed 27 at `0.50` and 14 at `0.65`.
Variation between runs is expected, and these counts are not accuracy claims.

## Acceptance tests

- CLI accepts only the profile action, mode, and flag matrix.
- Fixture selection and explicit `-text` override are offline and deterministic
  under injected selection.
- Starter output orders selected input, parent execution, routed assignment,
  terminal wait, and typed result.
- Parent Query covers pending, routed, and unroutable states.
- Every department route and fixed employee is tested.
- Valid, invalid, duplicate, late, and deadline-race Signals are tested.
- Parent forwarding and direct Child CWC use the same payload contract.
- Parent and Child CWC Markdown have active and terminal states.
- Transport tests verify three questions, catalog mutation, key handling,
  endpoint safety, provider-specific confidence semantics, retry
  classification, malformed responses, and missing usage.
- The complete 40-ticket corpus is exercised against local Laya and its routing
  and provisional-label match counts are reported.
- Replay does not invoke the recorded classifier Activity again.
- Go tests/build, generator/isolation tests, Explorer Python and DOM suites,
  local Cadence mock flows, and one local Laya flow pass.

## Non-goals

- Batch submission, throughput, latency aggregation, cost calculation, or
  classification-accuracy benchmarking.
- Reassignment, availability or skill matching, workload balancing,
  multi-level escalation, or ticket resolution.
- Guaranteed non-repetition across starter processes.
- Explorer runtime implementation changes.
- Java support in this Issue.
- Paid remote validation without separate authorization.
