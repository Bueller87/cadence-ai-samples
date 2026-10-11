# __RECIPE_TITLE__ implementation specification

## Purpose

Provide one durable SystemOne classification decision with interchangeable
offline and live Activity implementations.

## Repository implementation profile

- **Profile:** `java/systemone`
- **Cadence language:** Java 17
- **Classifier integration:** SystemOne
- **Agent integration:** none
- **Implementation directory:** `java/systemone`
- **Mock behavior:** local keyword classification with no catalog, credential,
  server, or provider access
- **Live behavior:** only confirmed `worker --mode live` reads one supported
  `classifiers.yaml` selection and constructs the HTTP Activity
- **Workflow ID reuse:** stable `__RECIPE_SLUG__-demo` with
  `WorkflowIdReusePolicy.AllowDuplicate`; closed reuse is allowed and an open
  duplicate remains rejected
- **Profile deviations:** none

## Dependencies

Gradle 8.14.3 pins Cadence Java client 3.13.5, Jackson YAML 2.18.3, JUnit
5.11.4, and AssertJ 3.27.3. Dependency locking records the transitive graph.

## Workflow and Activity contract

`__RECIPE_CLASS__Workflow` receives only `RecipeInput{text}` and invokes
`__RECIPE_SLUG__.classify-text`. It returns the original text and typed label,
confidence, probabilities, and response model. Workflow code does not parse
catalogs, read environment variables, or perform network calls.

The mock maps urgent, blocked, outage, or security text to `priority` and other
non-empty text to `routine`. Live HTTP uses the selected catalog endpoint
unchanged and validates the choice, confidence, probabilities, and model.

## Worker-local configuration

The only positional actions are `worker` and `start`; both require
`--mode mock|live`. Shared flags are `--address`, `--domain`, and `--task-list`.
`--workflow-id` and `--text` configure starts. `--classifier-id` and
`--catalog-dir` configure only a live Worker. The boolean `--confirm-live` guard
is valid and required only for `worker --mode live`, and it is checked before
catalog, credential, or HTTP client setup. `CLASSIFIER_AI_KEY` is read only by
a confirmed live TypeSafe Worker.

The selected `classifiers.yaml` entry is the sole source of provider, model,
and endpoint. TypeSafe requires HTTPS; Laya requires loopback. URLs reject
credentials, query, fragment, unsupported schemes, and missing hosts. No exact
provider host is required or defined in source.

Both start modes avoid catalog and environment readers and do not construct
classifier HTTP configuration. Their Workflow input contains business text
only; classifier selection and transport configuration remain local to the
Worker. A mock Worker also avoids catalogs, credentials, and HTTP transport
construction.

## Failure and retry behavior

Configuration, authentication, invalid request, client HTTP, and malformed
response failures are non-retryable. Network failures, 429, 529, and server
errors retry within the two-minute Activity schedule-to-close timeout.

## Offline acceptance criteria

- `./gradlew test` passes without Cadence, credentials, or provider access.
- Mock setup invokes neither the catalog loader nor environment reader.
- Mock Worker and start setup do not construct a classifier HTTP client.
- Both start modes invoke neither catalog nor environment readers.
- Unconfirmed live Workers fail before catalog, credential, or client setup.
- Temporary catalogs test IDs, providers, credentials, and endpoint safety.
- A changed safe catalog endpoint reaches an in-process HTTP stub.
- Workflow input exposes only text, never Worker configuration.
- Start options explicitly use `AllowDuplicate`, and the in-memory service
  rejects a duplicate while the first Run remains open.
- The tree has no provider endpoint constants or cross-recipe imports.
