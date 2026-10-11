# __RECIPE_TITLE__

## Architecture

This recipe uses the `java/bare` profile: Java 17, the Cadence Java client, no
classifier integration, and no agent framework:

`input → assess Activity → optional follow-up Activity → result`

Both Activities are synthetic, offline, and credential-free. The bare profile
never reads `models.yaml` or `classifiers.yaml`. Both `mock` and `live` are
scaffold lanes backed by those same synthetic Activities, so neither mode makes
provider calls.

## Requirements and setup

- Java 17 or newer. Gradle resolves a Java 17 toolchain when necessary.
- A Cadence frontend at `127.0.0.1:7933` with domain `cadence-ai-samples`.

From the repository root:

```bash
cd recipes/__RECIPE_SLUG__/java/bare
./gradlew test
```

The checked-in Gradle 8.14.3 wrapper, dependency locks, and pinned dependencies
make the build reproducible.

The CLI has exactly two positional actions, `worker` and `start`. Each requires
`--mode mock` or `--mode live`.

## Worker and start commands

Start the Worker:

```bash
./gradlew run --args="worker --mode mock"
```

Start the example Workflow in another terminal:

```bash
./gradlew run --args="start --mode mock --text 'Investigate this example request.'"
```

Both commands default to domain `cadence-ai-samples`, task list
`__RECIPE_SLUG__`, and Workflow ID `__RECIPE_SLUG__-demo`. Keep `--domain`,
`--task-list`, and `--mode` identical between them. You may use `--mode live`
to reserve a live scaffold lane, but it remains synthetic until you replace the
Activities with a provider integration.

## Workflow ID reuse

`start` explicitly uses `WorkflowIdReusePolicy.AllowDuplicate`. A new Run may
use the stable run ID after the previous Run closes. Cadence still rejects a
duplicate while a Run is open, and the command tells you to wait or pass a
different `--workflow-id`.

## Reliability and tests

Activities have bounded timeouts and three attempts with exponential backoff.
Invalid input is non-retryable. `./gradlew test` covers Workflow branching,
offline Activities, configuration, literal CLI flags, failures, and reuse
policy without a Cadence server or credentials.

All source and dependencies are contained under `java/bare`; the recipe imports
no other recipe and can be copied as a unit.
