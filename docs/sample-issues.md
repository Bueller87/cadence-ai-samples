# Sample issues

These scores rank potential production risks from 1 to 100, with 100 representing
the highest risk. Some items are intentional omissions that keep the samples
focused and copyable.

## Recurring AI Watch

| Score | Flaw | Why it matters |
|---:|---|---|
| 98 | Starter and Worker configuration can silently disagree. | A Workflow may claim one classifier or model while a differently configured Worker executes another provider. |
| 95 | Transient failures can retry forever across Watch cycles. | A persistent outage can create unlimited traffic, cost, and noise without reaching a degraded or terminal state. |
| 92 | Provider calls lack idempotency protection. | Activity retries can repeat successful but unrecorded AI calls, causing duplicate work and charges. |
| 88 | There is no Worker versioning or Workflow upgrade strategy. | Deploying changed Workflow code while long-running executions remain open can cause replay failures or behavioral drift. |
| 84 | Results have no durable delivery or acknowledgment path. | Reports exist only in Workflow state, so no external system confirms they were stored, delivered, or reviewed. |

## StreamWave ticket routing

| Score | Flaw | Why it matters |
|---:|---|---|
| 97 | Worker configuration is not durably bound to a Workflow execution. | Changing or mixing Workers on one task list can silently switch the classifier, model, or endpoint used for retries. |
| 94 | `UNROUTABLE` and `SLA_TIMEOUT` complete without escalation or durable delivery. | Tickets can leave the system successfully while no employee, review queue, or downstream service is responsible for them. |
| 91 | Jev Activity calls lack idempotency protection. | A lost Activity completion can trigger another paid classification request and produce duplicate external work. |
| 89 | An acknowledgment proves matching identifiers, not employee identity or authorization. | Anyone permitted to Signal the Workflow can construct the deterministic payload and acknowledge the assignment. |
| 86 | Workflow and assignment IDs have weak lifecycle semantics. | Reusing ticket IDs can be blocked by Cadence reuse policy, while deterministic assignment IDs can let stale commands target a later execution. |
| 83 | The Go sample hard-codes Jev instead of using the shared inference catalogs. | Changing the live classifier, model, or endpoint requires Go code changes instead of selecting entries from `classifiers.yaml`. |
