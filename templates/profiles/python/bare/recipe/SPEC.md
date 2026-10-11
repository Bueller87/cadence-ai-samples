# __RECIPE_TITLE__ implementation specification

## Problem statement

[State the problem to solve and why it matters.]

## Goals and non-goals

### Goals

- [Measurable goal]

### Non-goals

- [Explicitly excluded behavior or scope]

## Business scenario

[Describe the users, operating context, inputs, constraints, and desired business outcome.]

## Repository implementation profile

Generated from the `python/bare` profile defined by the
[recipe template system specification](https://github.com/cadence-workflow/cadence-ai-samples/blob/main/templates/SPEC-Recipe-Templates.md).

- **Cadence language:** Python
- **Classifier integration:** none
- **Agent integration:** none
- **Implementation directory:** `python/bare`
- **Mock and live behavior:** The starter Activities are synthetic and make no
  external calls. There is no live mode. [If you add an external Activity, keep
  a synthetic implementation for offline tests and describe any live
  confirmation boundary here.]
- **Runtime catalog use:** Not applicable. [Update if the recipe adds model or
  classifier Activities that read `models.yaml` or `classifiers.yaml`.]
- **Workflow ID reuse:** `start` uses the stable demo Workflow ID
  `__RECIPE_SLUG__-demo` with `ALLOW_DUPLICATE`. A new Run may start after the
  previous Run closes; a start while a Run is open is rejected and reported.
- **Profile deviations:** None. [Record intentional differences from the
  profile contract and why they are needed.]

## Language and dependencies

- **Language:** Python
- **Runtime version:** Python 3.12 or 3.13
- **Dependencies:** `cadence-python-client` 0.4.1 (published release) for the
  Worker, client, and offline test environment; `pydantic` for the data
  converter.
  [Add recipe-specific dependencies and why each is needed.]

## Workflow architecture

[Describe workflow responsibilities, sequence, durable state, timers, and decision points. Keep the design specific to this recipe.]

## AI model or agent responsibilities

[Describe which tasks, if any, require a specialized decision model, generative model, or agent. Say what ordinary code cannot decide reliably. The bare profile prescribes no framework; write "None" if the recipe needs no AI.]

## Activity boundaries

[List Activities and their responsibilities. Identify external calls and side effects, along with any idempotency requirements. All nondeterministic operations, including AI model calls, must run in Activities, never in workflow code.]

## Input and output schemas

### Workflow input

```json
{
  "placeholder": "[type and meaning]"
}
```

### Workflow output

```json
{
  "placeholder": "[type and meaning]"
}
```

[Add Activity schemas where they clarify important boundaries.]

## Configuration

[List environment variables, configuration files, defaults, validation rules, and secret-handling expectations.]

## Failure handling and retry behavior

[Specify timeouts, retry policies, exponential backoff, non-retryable errors, compensation or recovery, and behavior after workflow or worker restarts.]

## Synthetic test data

[Describe safe mock or synthetic fixtures, how they are generated, and the cases they cover. No paid AI access should be required for the core test path.]

## Tests and acceptance criteria

- [Test case and expected result]
- [Observable acceptance criterion]
- [Failure or recovery scenario]

## Definition of done

- [ ] The recipe is independently runnable.
- [ ] The selected implementation profile and any deviations are documented.
- [ ] Setup and execution instructions are complete.
- [ ] Workflow ID reuse behavior is explicit, documented, and tested.
- [ ] Workflow and Activity behavior is tested.
- [ ] Synthetic fixtures cover normal and edge cases.
- [ ] Reliability and architectural decisions are documented.
- [ ] No credentials, proprietary data, or dependencies on other recipes are included.
- [ ] [Additional recipe-specific completion criterion]
