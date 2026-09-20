# [Recipe name] implementation specification

## Problem statement

[State the problem to solve and why it matters.]

## Goals and non-goals

### Goals

- [Measurable goal]

### Non-goals

- [Explicitly excluded behavior or scope]

## Business scenario

[Describe the users, operating context, inputs, constraints, and desired business outcome.]

## Language and dependencies

- **Language:** [Go, Python, or a minimal hybrid]
- **Runtime version:** [Version]
- **Dependencies:** [Required packages and why each is needed]

## Workflow architecture

[Describe workflow responsibilities, sequence, durable state, timers, and decision points. Keep the design specific to this recipe.]

## AI model or agent responsibilities

[Describe which tasks, if any, require a specialized decision model, generative model, or agent. Explain why deterministic code is insufficient for those tasks. Do not assume a particular provider or framework.]

## Activity boundaries

[List Activities and their responsibilities. Identify external calls and side effects, along with any idempotency requirements. All nondeterministic operations, including AI model calls, must execute in Activities rather than Workflow code.]

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
- [ ] Setup and execution instructions are complete.
- [ ] Workflow and Activity behavior is tested.
- [ ] Synthetic fixtures cover normal and edge cases.
- [ ] Reliability and architectural decisions are documented.
- [ ] No credentials, proprietary data, or dependencies on other recipes are included.
- [ ] [Additional recipe-specific completion criterion]
