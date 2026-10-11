# __RECIPE_TITLE__ implementation specification

## Problem statement

Define the business request that this recipe will process and the durable
outcome it must produce.

## Goals and non-goals

### Goals

- Replace the synthetic starter decision with one measurable business outcome.
- Keep orchestration deterministic and external work in Activities.
- Preserve an offline path for development and tests.

### Non-goals

- Prescribing an AI framework, classifier, or live provider.
- Sharing runtime source with another recipe.

## Business scenario

The starter accepts text, assesses whether it needs follow-up, and records a
synthetic follow-up when the text contains `investigate`. Replace this scenario
with the recipe's users, inputs, constraints, and expected outcome.

## Repository implementation profile

Generated from the `go/bare` profile defined by the
[recipe template system specification](https://github.com/cadence-workflow/cadence-ai-samples/blob/main/templates/SPEC-Recipe-Templates.md).

- **Cadence language:** Go
- **Classifier integration:** none
- **Agent integration:** none
- **Implementation directory:** `go/bare`
- **Mock and live behavior:** The starter Activities are synthetic and make no
  external calls. There is no live mode. If an external Activity is added, keep
  a synthetic implementation for offline tests and document the live
  confirmation boundary.
- **Runtime catalog use:** Not applicable. Update this field if the recipe adds
  model or classifier Activities that read repository catalogs.
- **Workflow ID reuse:** `start` uses the stable demo Workflow ID
  `__RECIPE_SLUG__-demo` with `client.WorkflowIDReusePolicyAllowDuplicate`. A
  new Run may start after the previous Run closes. A start while a Run is open
  is rejected and reported.
- **Profile deviations:** None.

## Language and dependencies

- **Language:** Go
- **Runtime version:** Go 1.23
- **Dependencies:** `go.uber.org/cadence` 1.3.1 for the client, Worker, Workflow,
  Activities, and offline test environment; `go.uber.org/yarpc` 1.55.0 for the
  Cadence service transport; `github.com/stretchr/testify` 1.9.0 for tests.

These are published versions already proven in this repository.

## Workflow architecture

`__RECIPE_CLASS__Workflow` executes the recipe-scoped assess Activity. It returns
the assessment immediately when no follow-up is required. Otherwise, it
executes the follow-up Activity and returns both values. The Workflow keeps no
mutable state outside Cadence history and performs no external calls directly.

## AI model or agent responsibilities

None. The bare profile prescribes no model, classifier, or agent framework.

## Activity boundaries

- `__RECIPE_SLUG__.assess` validates and assesses the input. It is the boundary
  to replace with a lookup, rules engine, model, or other nondeterministic work.
- `__RECIPE_SLUG__.follow-up` records a synthetic result. It is the boundary to
  replace with the recipe's idempotent side effect.

## Input and output schemas

### Workflow input

```json
{
  "text": "non-empty request text"
}
```

### Workflow output

```json
{
  "assessment": {
    "needs_follow_up": true,
    "reason": "keyword match"
  },
  "follow_up": "Follow-up recorded for: Investigate this example request."
}
```

`follow_up` is omitted when `needs_follow_up` is false.

## Configuration

The CLI accepts `-mode`, `-address`, `-domain`, `-task-list`, `-workflow-id`,
and `-text`. It defaults to Cadence at `127.0.0.1:7933`, domain
`cadence-ai-samples`, task list `__RECIPE_SLUG__`, and Workflow ID
`__RECIPE_SLUG__-demo`. No environment variables or secrets are required.

## Failure handling and retry behavior

Activities have a one-minute schedule-to-start timeout, 30-second
start-to-close timeout, and two-minute schedule-to-close timeout. They retry up
to three times with exponential backoff. Empty text raises the non-retriable
`InvalidRequestError`. Other Activity failures are returned by the Workflow.
Worker and start command setup failures exit with an error.

## Synthetic test data

Tests use invented routine and investigation requests. The fixtures cover both
Workflow branches, empty input, Activity failure, command validation, start
failure, reuse policy, and duplicate-open-run handling. No server, provider,
credentials, or paid access is required.

## Tests and acceptance criteria

- `go test ./...` passes with no Cadence server.
- Routine text skips follow-up, and investigation text records follow-up.
- Empty text is non-retriable, and Activity failures fail the Workflow.
- Worker and start commands share documented defaults and accept overrides.
- Start uses the stable Workflow ID with `AllowDuplicate`.
- A duplicate start while a Run is open reports a specific actionable error.

## Definition of done

- [ ] Replace the starter business scenario and complete recipe-specific goals.
- [ ] Keep the recipe independently runnable and copyable.
- [ ] Document any profile deviations.
- [ ] Keep setup and execution instructions current.
- [ ] Preserve explicit and tested Workflow ID reuse behavior.
- [ ] Test Workflow, Activity, configuration, command, and failure behavior.
- [ ] Keep synthetic fixtures for normal and edge cases.
- [ ] Include no credentials, proprietary data, or dependencies on other recipes.
