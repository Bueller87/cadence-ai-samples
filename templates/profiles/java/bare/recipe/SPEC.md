# __RECIPE_TITLE__ implementation specification

## Purpose

Replace the synthetic text assessment with one durable business outcome while
keeping orchestration deterministic and external work in Activities.

## Repository implementation profile

- **Profile:** `java/bare`
- **Cadence language:** Java 17
- **Classifier integration:** none
- **Agent integration:** none
- **Implementation directory:** `java/bare`
- **Mock/live behavior:** both modes use synthetic offline Activities; the live
  mode is a scaffold lane and makes no provider calls
- **Runtime catalogs:** none; this profile never reads repository catalogs
- **Workflow ID reuse:** stable `__RECIPE_SLUG__-demo` with
  `WorkflowIdReusePolicy.AllowDuplicate`; closed Runs may be followed by a new
  Run, while an open duplicate is rejected
- **Profile deviations:** none

## Dependencies

Gradle 8.14.3 pins Cadence Java client 3.13.5, JUnit 5.11.4, and AssertJ 3.27.3.
Dependency locking records the complete resolved graph.

## Workflow and Activities

`__RECIPE_CLASS__Workflow` calls `__RECIPE_SLUG__.assess`, returns immediately
for routine input, and otherwise calls `__RECIPE_SLUG__.follow-up`. Workflow
code performs no network, filesystem, environment, random, or wall-clock work.

Input contains only `text`. Output contains an assessment and optional
follow-up. Empty text raises non-retryable `InvalidRequestException`; other
Activity failures use bounded retries.

## Configuration

The only positional actions are `worker` and `start`; both require
`--mode mock|live`. Additional flags are `--address`, `--domain`, `--task-list`,
`--workflow-id`, and `--text`. Defaults are Cadence at `127.0.0.1:7933`, domain
`cadence-ai-samples`, task list `__RECIPE_SLUG__`, and Workflow ID
`__RECIPE_SLUG__-demo`. No credentials or environment variables are required.

## Acceptance criteria

- `./gradlew test` passes offline.
- Routine and investigation inputs cover both Workflow branches.
- Configuration and command parsing reject invalid values.
- Start options explicitly use `AllowDuplicate`, never terminate an open Run.
- The implementation has no catalog access or cross-recipe imports.
- README setup, Worker, start, mock, and test commands stay current.
