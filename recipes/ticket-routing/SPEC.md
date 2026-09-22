# High-volume support ticket router: implementation specification

**Repository:** `Bueller87/cadence-ai-samples`  
**Recipe path:** `recipes/ticket-routing/`  
**Status:** Implemented through Phase 4C; owner review pending. Automatic acknowledgment simulation and classification-accuracy benchmarking remain deferred.
**Language:** Go  
**Scenario:** StreamWave, a fictional streaming service

## 1. Problem and story

StreamWave receives free-form customer-support tickets at high volume. A specialized decision model interprets each message once. Ordinary code and Cadence then handle routing, retries, acknowledgments, and service-level agreement (SLA) timers. An optional Custom Workflow Control (CWC) lets a user acknowledge an assignment in Cadence Web.

The sample must be easy to **run and copy into another Go application**. Keep it focused on this ticket-routing use case instead of building a reusable AI framework or production customer-service platform.

## 2. Scope

### Required

- One parent workflow per ticket: `TicketIntakeWorkflow`.
- One Jev request per ticket containing **three independent typed questions**: Department, Priority, Complexity.
- Four department-specific Child Workflow routes: Billing, Technical, Account, Content. Thin wrappers around common local logic are acceptable if clearer than duplicating code.
- Assign one **fixed fictional employee ID per department** (no employee-selection algorithm).
- Child Workflow waits for a matching acknowledgment Signal or a durable, configurable SLA deadline. Acknowledgment completes with `ACKNOWLEDGED`; timeout completes with `SLA_TIMEOUT`.
- A simple CWC Markdown acknowledgment button and the CLI acknowledgment command drive the **same child-workflow Signal**. An automated signal sender for unattended runs remains deferred.
- Mock classifier by default; real Jev use must be explicitly opted into on the user's personal machine.
- Bundled synthetic text tickets; optional `PROMPT.md` for generating more; a bounded batch runner with clearly labeled metrics.
- Reliable Activity retry policy and deterministic workflow replay; unit tests including the signal/timeout race.

### Explicit non-goals

- No generative agent, multimodal extraction, human-in-the-loop (HITL) review queue, refund execution, full ticket resolution, skill matching, workload balancing, employee availability, reassignment, or multi-level escalation.
- No automatic rerouting of ambiguous tickets: record them as `UNROUTABLE`, without starting a Child Workflow.
- No real customer data, actual streaming-company integration, web dashboard, shared integration framework, or production-scale performance claims.
- No Go-versus-Python/four-way model benchmark in this first recipe.

## 3. Layout and copy-out rule

```text
recipes/ticket-routing/
├── README.md
├── SPEC.md
├── PROMPT.md
├── testdata/
│   └── tickets.jsonl
└── go/
    ├── go.mod
    ├── go.sum
    ├── .env.example
    ├── main.go
    ├── batch.go
    ├── workflow.go
    ├── workflow_test.go
    └── batch_test.go
```

Put workflow functions, Activities, small business structs, and small provider implementations in `workflow.go`; register workflows and Activities and implement the command-line interface (CLI) starter in `main.go`. `batch.go` contains the bounded batch runner and its reporting so `main.go` and `workflow.go` remain copyable. No cross-recipe imports, `integrations/`, or shared framework. `go build ./...` and `go test ./...` must work from `go/`. A developer should be able to copy the two Go source files (and their normal module dependencies) into an existing Cadence project, without importing this recipe's dataset or benchmark tooling.

Do not replace the scaffold or alter unrelated files. The spec lives at the recipe root, not in `go/`.

## 4. Inputs, outputs, and classification contract

**Input:** `Ticket{TicketID, Message}`. Each ID must be unique per batch; input is synthetic text only. Use a small fixture dataset with at least 30 diverse tickets, including ambiguous and incomplete messages. No account identifiers or real customer information.

**Classification:** exactly one TypeSafe System One API request (`POST /v1/systemone` with configured model; default alias `jev-latest`) per ticket attempt, with the following three **independent `choice` questions**:

| Question | Allowed values | Meaning |
|---|---|---|
| `department` | `billing`, `technical`, `account`, `content` | Primary team best placed to handle the issue. |
| `priority` | `low`, `normal`, `high`, `critical` | Operational urgency based on issue impact/time-sensitivity, **not simply sentiment**. |
| `complexity` | `tier1`, `tier2`, `tier3` | Estimated expertise needed to investigate. Informational only; not used for employee matching in this sample. |

Use the provider's documented `state`/`model`/`questions` request structure and `answers` schema; define clear option descriptions for the fictional streaming business. Parse the chosen option, per-question probability distribution and confidence, returned model ID, and `usage.input_tokens`/`usage.output_tokens` where available. Do **not** invent an output schema, assume `score` returns categorical values, or pretend a reported confidence is a quality guarantee. All token usage and cost claims must distinguish real API data from mock values.

Normalize to `RoutingDecision{Department, Priority, Complexity, DepartmentConfidence, PriorityConfidence, ComplexityConfidence, Model, InputTokens, OutputTokens, ...}`. Exact Go field names may vary; use explicit JSON tags as needed. Keep provider-specific response parsing inside the Activity/provider code, not workflow code.

**Low-confidence/error policy:** If the provider returns an invalid label, missing response field, or a classification with department confidence below a configurable, documented threshold (illustrative default `0.65`), mark `UNROUTABLE` with a reason and complete without a department Child Workflow. If priority/complexity are low-confidence, retain the chosen values but mark the result as uncertain; never silently treat these as verified facts. Nonretryable configuration/API failures should yield an explicit failed execution or structured error, not a fabricated classification.

**Result:** `TicketResult` records ticket ID, classification (if available), department/employee/child execution identifier (if applicable), status (`ACKNOWLEDGED`, `SLA_TIMEOUT`, `UNROUTABLE`), and relevant timings. Separate terminal business status from workflow-engine execution failure.

## 5. Cadence execution and activity boundaries

```text
One ticket → TicketIntakeWorkflow
  → ClassifyTicket Activity (mock or Jev)
      ├─ UNROUTABLE → record result; complete parent
      └─ valid department → selected department Child Workflow
            → fixed department employee ID
            → start durable acknowledgment deadline
            → wait for matching acknowledgment Signal OR deadline
                ├─ Signal first → ACKNOWLEDGED
                └─ deadline first → SLA_TIMEOUT
            → return child result to parent; parent completes
```

- `TicketIntakeWorkflow` starts and **waits for** its Child Workflow's terminal result. Use a deterministic, documented ID derivation so the CLI and any future simulator can locate the child without relying on random IDs or guessing. Define ID rules that prevent collisions between tickets.
- The department Child Workflow owns assignment state, the signal channel, and the SLA timer. Register a read-only query that exposes current status and, when awaiting acknowledgment, Markdown formatted for Cadence Web CWC.
- The acknowledgment signal payload must contain at least `ticket_id` and `assignment_id` (or equivalent deterministic assignment token). Accept **only** the current ticket/assignment, ignoring duplicates, invalid payloads, and late signals. A signal received after SLA expiry must never turn `SLA_TIMEOUT` into `ACKNOWLEDGED`. Define deterministic handling of the signal/deadline boundary and test it.
- The CWC button acknowledges an assignment and does not resolve the ticket; label it `Acknowledge Ticket`. Its `{% signal %}` action targets the **department Child Workflow** and sends the same payload as the CLI acknowledgment command and any future automated simulator. Render a plain completed-state view without an action button after acknowledgment or expiry. Use Cadence's formatted query response and the Markdown tag syntax documented for Custom Workflow Controls; do not return raw HTML or invent CWC APIs. Document the Cadence Web version needed for CWC and provide the CLI signal alternative when it is unavailable.
- Use short, configurable demonstration SLA durations (illustrative: critical 5s, high 10s, normal 20s, low 30s). Label these as **demo policy**, not real customer SLAs. Do not create a timer by `time.Sleep` in workflow code.
- **All external and nondeterministic operations belong in Activities or in the external starter/simulator, never in workflow logic.** In particular: Jev HTTP, clocks/wall time, random inputs, logs/metrics external side effects, file I/O. Use Cadence workflow APIs for time, timers, signals, Child Workflows, and query handlers. Completed Activity results must be reused on workflow replay; do not call the provider from a workflow or query handler.

## 6. Execution and demo interfaces

Prefer a **single Go executable** (`main.go`) with small documented modes and flags instead of multiple mini-applications. Exact CLI flag spelling is left to implementation, but the README must offer copy/paste commands for:

1. Starting the worker with mock classification.
2. Starting one synthetic ticket and viewing its parent and Child Workflow in Cadence Web.
3. Manually acknowledging the child via CLI Signal **or** the CWC button.
4. Demonstrating SLA expiry by deliberately sending no acknowledgment.
5. Running a bounded batch of synthetic tickets. The currently implemented batch sends no acknowledgment Signals, so routed tickets exercise `SLA_TIMEOUT`.
6. Opting into real Jev with a personal API key, **explicitly** choosing a small initial live call count; never default to real calls or silently retry a batch without a bound.

Batch orchestration must live **outside** individual workflow code, using the Cadence client. Bound submission and simultaneous open executions with configurable concurrency. Each workflow receives one ticket only: no giant workflow looping over thousands of tickets. Do not use a random generator in workflow logic. On an unsuccessful batch, report failures and pending workflows instead of claiming a successful completion. A future automated simulator must locate the correct child and assignment before sending Signals; it must use the same validated Signal path as the CLI and CWC rather than introducing a separate mechanism.

## 7. Jev and mock implementation

- Provide a minimal Jev HTTP client using Go standard `net/http` in the Activity implementation, with the endpoint/model/configuration read from worker startup configuration. Do not require a Go client library or a generic AI adapter package.
- Default `AI_PROVIDER=mock`. Mock responses should be deterministic based on fixture metadata or a documented rule. Simulated Jev data does not represent model accuracy, latency, token usage, or spend. Provide a way to create a controlled transient failure and a nonretryable failure for tests/demo without touching Jev.
- `AI_PROVIDER=jev` requires an explicitly provided `TYPESAFE_API_KEY` and opt-in live mode. Do not check secrets into Git or print them. Validate configuration early; never send real API requests from a work machine lacking approval.
- Use a finite Activity timeout and Cadence-configured retry policy with exponential backoff for network/transient errors, HTTP 429/529, and retryable HTTP 5xx; respect server retry guidance where feasible. Authentication errors, schema/validation errors, and malformed successful responses should fail nonretryably. Avoid stacking an unbounded provider retry loop on top of Cadence retries. Document that failed/timed-out Activities may repeat an inference request; only **completed, recorded** Activity results are replay-safe without a new model call.

## 8. Dataset and optional generation prompt

Commit `testdata/tickets.jsonl` with at least 30 realistic fictional streaming-service customer tickets. Include ticket ID, message, and optional `expected_department`/`expected_priority`/`expected_complexity` for **test fixtures only**. Include examples for all four departments, urgency levels, typos, multi-intent ambiguity, and missing context. Do not present synthetic expected labels as independently validated ground truth.

`PROMPT.md` should instruct a coding assistant to generate a larger, schema-compatible JSONL corpus using synthetic-only inputs, controlled categories/distributions, varied writing styles, and explicit ambiguous cases. It must avoid real customer data and explain that labels should be reviewed before use in quality comparisons. The recipe must run without generating new data or using a paid model.

## 9. Metrics and benchmarks

For mock and live runs separately, report: started/completed/failed/pending workflows; `ACKNOWLEDGED`/`SLA_TIMEOUT`/`UNROUTABLE`; classification count; optional real provider input/output token totals; measured classification and end-to-end elapsed time; and Activity retry count when retries are recorded. Show the median and 95th percentile only when enough measured samples exist; document the clock and method. Cost calculation is **optional and opt-in**. Use a configurable published price and real provider usage, and label illustrative output as unmeasured.

Do not claim a fixed throughput, 10× cost advantage, or Uber/Netflix production-scale performance based on a laptop run. A laptop batch of 10 to 100 tickets is a functional demonstration; larger runs are optional after verifying the local Cadence cluster, Jev rate limits, and costs.

## 10. Tests and acceptance criteria

- `go test ./...` and `go build ./...` succeed within this recipe; no AI keys/network needed for unit tests.
- Workflow tests cover all four department branches, the `UNROUTABLE` path, normal acknowledgment, SLA expiry, invalid/wrong-assignment signal, duplicate/late signal, and a controlled signal/deadline race.
- Jev transport tests use `httptest` to validate exact request/response parsing and transient versus nonretryable HTTP behavior; no live provider requests in CI.
- Failure/restart demo or test proves a recorded classification Activity result is not re-inferred on workflow replay. Do not claim exactly-once external Activity execution.
- The CWC query produces the documented formatted Markdown envelope and a working acknowledgment button on a supported Cadence Web version. An ordinary CLI Signal must also work.
- Default mock mode can run one ticket and a bounded synthetic batch end to end, with no external AI key; timeout and acknowledgment paths are observable.
- README contains Bash-first setup and run commands with brief PowerShell equivalents, local Cadence prerequisites, mode/configuration instructions, safety notes for live API usage, optional CWC instructions, results interpretation, and a **copy-out** guide pointing to `workflow.go` and `main.go`.
- No unapproved/non-public employer code or data; only independently authored code, synthetic inputs, and public APIs/docs.

## 11. Implementation workflow for Codex

**Do not begin coding until the repository owner approves this SPEC.md.** On approval, inspect the existing scaffold and official Cadence Go/CWC and TypeSafe API docs. Use the actual APIs and verified signatures. Implement the smallest vertical slice in this order: mock single ticket → Child Workflow + acknowledgment/timeout → CLI signal and CWC → tests → synthetic batch → live Jev transport → documentation. Keep functionality and files minimal. Before changing scope or introducing extra packages, stop and explain the trade-off. Do not commit/push until the owner reviews the implementation.

## References

- [Cadence redesigned Go samples](https://github.com/cadence-workflow/cadence-samples/tree/master/new_samples)
- [Cadence Custom Workflow Controls documentation](https://cadenceworkflow.io/docs/concepts/workflow-queries-formatted-data)
- [Cadence CWC Go sample](https://github.com/cadence-workflow/cadence-samples/tree/master/new_samples/query)
- [TypeSafe Jev API reference](https://docs.typesafe.ai/api)
- [TypeSafe Quick Start](https://docs.typesafe.ai/introduction/quickstart)
