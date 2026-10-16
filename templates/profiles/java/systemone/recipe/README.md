# __RECIPE_TITLE__

## Architecture

This `java/systemone` recipe keeps classification in one Cadence Activity:

`request → classify-text Activity → recorded Classification → result`

The default `mock` classifier is offline and credential-free. A live-configured
Worker reads only `classifiers.yaml`; its selected entry is the sole source of
the provider, model, and endpoint.

## Requirements and setup

- Java 17 or newer. Gradle resolves a Java 17 toolchain when necessary.
- A Cadence frontend at `127.0.0.1:7933`, domain `cadence-ai-samples`.

```bash
cd recipes/__RECIPE_SLUG__/java/systemone
./run.sh setup
./gradlew test
```

The checked-in Gradle 8.14.3 wrapper, dependency locks, and pinned dependencies
make the build reproducible.

The CLI has exactly two positional actions, `worker` and `start`. Each requires
`--mode mock` or `--mode live`.

## Offline mock

Start the Worker:

```bash
./run.sh worker --mode mock
```

Start the Workflow in another terminal:

```bash
./run.sh start --mode mock --text 'Investigate this urgent example request.'
```

The mock path does not read a catalog, `.env.example`, or any credential.

## Live classifiers

Only `typesafe` and `laya` catalog providers are implemented. Remote TypeSafe
endpoints require HTTPS. Laya endpoints must use `localhost`, `127.0.0.1`, or
`::1`. Every endpoint rejects credentials, query strings, fragments, missing
hosts, and unsupported schemes. No provider host is hardcoded.

For local Laya, use the same dedicated task list and classifier ID:

```bash
./run.sh worker --mode live --classifier-id laya-local --task-list __RECIPE_SLUG__-laya --confirm-live
./run.sh start --mode live --task-list __RECIPE_SLUG__-laya --workflow-id __RECIPE_SLUG__-laya-demo
```

For TypeSafe, export the key only in the Worker shell:

```bash
export CLASSIFIER_AI_KEY='your-personal-key'
./run.sh worker --mode live --classifier-id jev-default --task-list __RECIPE_SLUG__-jev --confirm-live
```

The start command needs no key, classifier ID, or catalog:

```bash
./run.sh start --mode live --task-list __RECIPE_SLUG__-jev --workflow-id __RECIPE_SLUG__-jev-demo
```

The live Worker's default `--catalog-dir ../../../..` points to the repository
root. Copy `classifiers.yaml` or override that directory after copying the recipe.
`--confirm-live` is required only for `worker --mode live` and is checked before
catalog, credential, or HTTP client setup. The Worker alone reads the catalog
and credential and constructs the classifier HTTP client. A mock Worker does
none of those things. Both start modes submit business text only and do not read
or validate a catalog, inspect credentials, or construct classifier HTTP
configuration. Endpoint, provider, model, catalog path, classifier ID, and
credential never enter Workflow input or history.

## Workflow ID reuse and tests

Start commands explicitly use `WorkflowIdReusePolicy.AllowDuplicate`: a closed
Run may be followed by another with the stable `__RECIPE_SLUG__-demo` ID, while
an open duplicate remains an error. `./gradlew test` covers the Workflow, mock
path, catalogs, endpoint safety, credentials, literal CLI flags, a mutated
endpoint reaching a local HTTP stub, start-command isolation from Worker
configuration, and an in-memory duplicate-open rejection without live calls.

## Manual setup recovery

If the launcher cannot prepare the implementation, verify Java and the checked-in Gradle wrapper, then run:

```bash
./gradlew --no-daemon classes
```
