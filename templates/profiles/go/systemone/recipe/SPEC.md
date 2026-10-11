# __RECIPE_TITLE__ implementation specification

## Purpose

Provide an independently runnable starting point for a business process that
needs one durable SystemOne classification decision. The scaffold classifies a
synthetic request as `routine` or `priority`; recipe authors replace that
starter taxonomy with their own bounded decision.

## Goals

- Keep mock and live classification behind one typed Activity contract.
- Run offline without credentials, Cadence, or provider access.
- Select supported live classifiers from `classifiers.yaml` without accepting
  unsafe endpoints or unknown providers.
- Make repeated demonstrations safe through explicit Workflow ID reuse
  semantics.

## Non-goals

- Agent-framework integration, free-form generation, or Java/Python support.
- Automatic provider discovery or fallback between classifiers.
- Shared runtime imports from another recipe.

## Repository implementation profile

Generated from the `go/systemone` profile defined by the
[recipe template system specification](https://github.com/cadence-workflow/cadence-ai-samples/blob/main/templates/SPEC-Recipe-Templates.md).

- **Cadence language:** Go
- **Classifier integration:** SystemOne
- **Agent integration:** none
- **Implementation directory:** `go/systemone`
- **Mock behavior:** `mock` classifies locally by synthetic keywords and makes
  no network calls.
- **Live behavior:** `live-demo` and a live-configured Worker require
  `-confirm-live`; the Worker executes one SystemOne HTTP request per Activity
  attempt.
- **Runtime catalog use:** `jev-default` and `laya-local` are selected from
  `classifiers.yaml`; only the `typesafe` and `laya` provider contracts are
  implemented.
- **Workflow ID reuse:** starters use the stable
  `__RECIPE_SLUG__-demo` ID with `ALLOW_DUPLICATE`. A new Run may start after
  the previous Run closes, while an open duplicate is rejected.
- **Profile deviations:** none.

## Runtime and dependencies

- Go 1.23
- `go.uber.org/cadence` for the Worker, client, Workflow, Activity, and offline
  test environment
- `go.uber.org/yarpc` for the Cadence frontend connection
- `gopkg.in/yaml.v3` for catalog parsing
- `github.com/stretchr/testify` for tests

## Workflow and Activity contract

`__RECIPE_CLASS__Workflow` receives `RecipeInput{text}`. It schedules
`ClassifyText` with bounded retries and returns the original input plus a
`Classification{label, confidence, probabilities, model}`. Workflow code does
not read environment variables, parse catalogs, use wall-clock time, or make
network requests.

The mock Activity maps urgent, blocked, outage, or security text to `priority`;
other non-empty text maps to `routine`. The live Activity sends one SystemOne
choice question with the same labels and validates the selected choice,
confidence, probabilities, and response model before returning.

## Configuration and validation

- `-domain`: defaults to `cadence-ai-samples`.
- `-task-list`: defaults to `__RECIPE_SLUG__` and must match between Worker and
  starter.
- `-workflow-id`: defaults to `__RECIPE_SLUG__-demo`.
- `-classifier-id`: defaults to `mock`; live IDs must exist in
  `classifiers.yaml`.
- `-catalog-dir`: defaults to the generated checkout's repository root.
- `-confirm-live`: mandatory for every process configured with a live
  classifier.
- `CLASSIFIER_AI_KEY`: mandatory only for a TypeSafe Worker. It is read from the
  environment and is never sent to Workflow history.

The selected `classifiers.yaml` entry is the sole source of provider, model,
and endpoint values. The Worker passes its endpoint unchanged to the HTTP
request transport.
TypeSafe endpoints require HTTPS because the integration sends an API key.
Laya endpoints require `localhost`, `127.0.0.1`, or `::1`. All endpoints reject
embedded credentials, query strings, fragments, unsupported schemes, and
missing hosts.

## Failure handling

Empty input, invalid configuration, authentication failures, non-retryable HTTP
client errors, and malformed responses use nonretryable Cadence error reasons.
Network failures, rate limits, provider overload, and server errors remain
retryable within the Activity's two-minute schedule-to-close limit. The
Workflow returns only after a validated classification is recorded or the
Activity policy is exhausted.

## Offline acceptance tests

- The Workflow records and returns a mocked Activity result.
- The mock classifier handles both labels and rejects empty input.
- An in-process HTTP server verifies the SystemOne request and response
  contract without calling a provider.
- Temporary catalogs cover selection, unknown IDs, providers, endpoint safety,
  and TypeSafe key requirements.
- A safe catalog endpoint change reaches the in-process HTTP transport without
  a live provider call.
- Mode tests reject classifier/mode mismatches and missing live confirmation.
- Start options use the stable ID, matching task list, and
  `ALLOW_DUPLICATE` rather than terminating an open Run.

## Definition of done

- The generated recipe is independently copyable and imports no recipe source.
- README setup, mock, live, credential, and reuse instructions match the CLI.
- All external work remains in the classification Activity.
- `go test ./...` passes without credentials, provider calls, or a Cadence
  server.
