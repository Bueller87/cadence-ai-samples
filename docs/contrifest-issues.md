# Contribfest sample issues

Each issue is a focused 60-minute contribution to either StreamWave ticket routing or Recurring AI Watch. The list prioritizes problems repeatedly raised in 2025 and 2026 guidance for production AI workflows: bounded retries and cost, idempotency, prompt and identity controls, evaluations, observability, and safe` Workflow evolution.

## Beginner

### 1. Add local inference readiness checks

**Sample:** Recurring AI Watch

**Task:** Add a CLI command that checks Cadence, Ollama, and Laya connectivity
without sending an inference request.

**Done when:**

- Each dependency reports ready, unavailable, or misconfigured.
- The command exits nonzero when a required dependency is unavailable.
- Tests mock every network check.



### 2. Show the selected inference configuration in status

**Sample:** Recurring AI Watch

**Task:** Include the agent framework, model provider, and model name in the
Workflow status Query and CLI output.

**Done when:**

- The values survive Continue-As-New.
- Mock and live status output remain clear.
- Query formatting and state-transfer tests cover the fields.



### 3. Stop logging raw ticket messages

**Sample:** StreamWave ticket routing

**Task:** Remove full ticket text from normal logs and retain only safe metadata
such as ticket ID and message length.

**Done when:**

- Demo and batch logs do not contain ticket message bodies.
- A regression test uses a secret-like marker and proves it is not logged.
- Debugging still has ticket and Workflow identifiers.



### 4. Reject oversized classifier input

**Sample:** StreamWave ticket routing

**Task:** Add a documented ticket-message size limit before mock or live
classification.

**Done when:**

- Oversized input fails as a nonretryable validation error.
- Boundary and over-limit tests cover both classifier paths.
- The error reports the limit without echoing ticket text.



### 5. Complete provider error classification

**Sample:** StreamWave ticket routing

**Task:** Classify authentication, throttling, timeout, and permanent HTTP
responses consistently in the Jev Activity.

**Done when:**

- `401` and `403` are nonretryable authentication failures.
- `408`, `429`, `529`, and `5xx` remain retryable.
- Table-driven tests cover every status class.



## Intermediate



### 6. Stop recurring retries after a cycle-level budget

**Sample:** Recurring AI Watch

**Task:** Track consecutive failed cycles and enter a durable `DEGRADED` state
after a configurable limit.

**Done when:**

- Successful checks reset the counter.
- Status reports the count and last safe error category.
- Tests prove a persistent outage stops generating provider calls.



### 7. Treat release notes as untrusted prompt data

**Sample:** Recurring AI Watch

**Task:** Delimit release-note content and instruct each model path not to follow
instructions found inside it.

**Done when:**

- Google ADK and OpenAI Agents use the same trust-boundary wording.
- A malicious fixture attempts to override the reporting task.
- Offline tests inspect the final model request without calling a provider.



### 8. Add an offline routing evaluation report

**Sample:** StreamWave ticket routing

**Task:** Compare classifier results with the provisional fixture labels and
report coverage, abstentions, and per-field agreement.

**Done when:**

- The command works with the deterministic mock and no credentials.
- `UNROUTABLE` is reported as abstention, not an incorrect department.
- Output states that fixture labels are provisional and makes no accuracy claim.



### 9. Escalate unroutable tickets to a review Workflow

**Sample:** StreamWave ticket routing

**Task:** Replace the terminal `UNROUTABLE` dead end with a small review Child
Workflow that waits for a routing Signal or expires.

**Done when:**

- Low-confidence tickets enter a visible review state.
- A validated Signal chooses one supported department.
- Tests cover review completion and review timeout.



### 10. Emit structured inference observations

**Sample:** Recurring AI Watch

**Task:** Record model, provider, outcome, latency, and retry count using stable
field names aligned with OpenTelemetry GenAI conventions where applicable.

**Done when:**

- Credentials and prompt bodies are excluded.
- Successful, retry-exhausted, and fatal outcomes are distinguishable.
- Tests assert the emitted field names and low-cardinality values.



## Advanced



### 11. Bind Workflow inference selection to the Worker

**Sample:** Recurring AI Watch

**Task:** Carry a deterministic configuration fingerprint in Workflow state and
make Activities reject a Worker whose configuration does not match.

**Done when:**

- The fingerprint covers agent, model, classifier, and endpoint identity but no secrets.
- It survives Continue-As-New.
- A mismatched Worker fails before contacting a provider.



### 12. Add stable operation IDs to provider Activities

**Sample:** Recurring AI Watch

**Task:** Derive a stable operation ID from Workflow ID, release identity, and
operation type for classifier and report calls.

**Done when:**

- Activity retries reuse the same operation ID.
- Different releases and operation types receive different IDs.
- Tests document how a provider or result store can use the ID for deduplication.



### 13. Load the Go classifier from the shared catalog

**Sample:** StreamWave ticket routing

**Task:** Replace the hard-coded Jev model and endpoint with validated selection
from `classifiers.yaml`.

**Done when:**

- The Worker accepts an explicit classifier ID and catalog path.
- Unsupported providers fail before the Worker starts.
- Secrets remain environment-only and mock mode remains the default.



### 14. Make acknowledgment tokens run-specific

**Sample:** StreamWave ticket routing

**Task:** Bind each acknowledgment to the current Child Workflow run so a stale
command cannot acknowledge a later assignment with reused business IDs.

**Done when:**

- The CLI and Cadence Web control use the same run-specific token.
- Wrong-run and replayed acknowledgments are ignored.
- Signal/deadline race behavior remains deterministic.



### 15. Add a versioned routing change with replay coverage

**Sample:** StreamWave ticket routing

**Task:** Introduce one small Workflow behavior change behind Cadence
`GetVersion` and verify old and new histories remain replay-compatible.

**Done when:**

- Existing executions retain old behavior.
- New executions use the changed behavior.
- A replay-focused test demonstrates both paths and documents safe removal.



## Selection basis

These issues are grounded in current public guidance:

- [OWASP Top 10 for Agentic Applications 2026](https://genai.owasp.org/download/52117)
emphasizes least agency, identity controls, memory integrity, and observability.
- [Cadence Activity guidance](https://cadenceworkflow.io/docs/go-client/execute-activity)
states that Activities may execute more than once and should be idempotent.
- [Cadence best practices](https://cadenceworkflow.io/faq/best-practices)
recommends unique transaction identifiers for at-least-once Activities.
- [Cadence Workflow versioning](https://cadenceworkflow.io/docs/go-client/workflow-versioning)
explains how incompatible deployments cause nondeterministic replay failures.
- [OpenTelemetry GenAI events](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-events.md)
defines interoperable inference and evaluation observations.
- [OWASP Agent Memory Guard](https://owasp.org/www-project-agent-memory-guard/)
reflects current concern about persistent untrusted context in agent systems.

