# Auto Research implementation specification

## Problem statement

A useful research briefing can require several model roles and human edits over
time. Those steps must survive Worker restarts without repeating completed model
calls, simulated tool work, or publication. The result must remain reviewable
without claiming that synthetic source briefs are factual web research.

## Goals

- Plan one to four focused questions for a bounded synthetic topic.
- Run one specialist pass per question using simulated search and source-read
  Activities.
- Synthesize a bounded Markdown draft and expose it with its revision.
- Wait durably for revision-aware revise, expand, or approve Signals.
- Publish exactly one approved local report through an idempotent Activity.
- Keep representative mock and live modes on the same Workflow state machine.

## Non-goals

- Real search, crawling, retrieval credentials, or factual-source verification.
- Remote publication, email, object storage, databases, or CMS integration.
- Unbounded autonomous research or performance-driven parallelism.
- Classifier integration or a shared runtime with another recipe.
- Remote provider validation on the work Mac.

## Implementation profile

The recipe is generated from `python/openai-agents`:

- Cadence language: Python.
- Classifier: none.
- Agent integration: OpenAI Agents.
- Implementation: `python/openai-agents`.
- Runtime catalog: only a live Worker reads repository-root `models.yaml`.
- Live confirmation: `worker --mode live` requires `--confirm-live`.
- Workflow ID reuse: `ALLOW_DUPLICATE`; a closed ID is reusable, while an open
  duplicate is rejected.

The recipe imports neither template code nor another recipe.

## Workflow state machine

Both modes execute:

1. Validate the topic and mode.
2. Plan one to four questions.
3. For each question, schedule one simulated search, read two simulated source
   briefs, and run one specialist.
4. Synthesize the initial report at revision `0`.
5. Expose status and a Markdown report query, then wait for human review.
6. Apply an eligible revision or expansion and increment the revision only
   after the updated draft completes, or approve the current revision.
7. Publish the approved report once and return a portable receipt.

The Workflow owns topic, phase, question count, draft, revision, accepted edit
count, pending action, and publication receipt. Replay reads completed
Activities and Signals from history. There is no application review timer; the
Run is subject to its configured 24-hour execution timeout.

## Review contract

Signals are:

- `approve_report(expected_revision: int)`
- `revise_report(expected_revision: int, feedback: str)`
- `expand_report(expected_revision: int, topic: str)`

A Signal is eligible only while state is `WAITING_FOR_REVIEW`, its expected
revision equals the current revision, and no action has won that revision. The
first eligible action wins. Early, stale, duplicate, conflicting, empty,
malformed, or over-limit actions are deterministic no-ops and schedule no
Activity. At most three revise or expand actions are accepted. Approval remains
eligible at the edit limit and makes later Signals irrelevant.

## Role and Activity boundaries

The Workflow contains deterministic orchestration only.

- `auto-research.mock-plan`, `mock-specialist`, `mock-synthesis`, and
  `mock-edit` provide deterministic offline role output.
- `auto-research.openai-agents-agent` executes live planner, specialist,
  synthesizer, and editor roles with the Worker-selected model.
- `auto-research.search-sources` and `read-source` return synthetic fixtures and
  perform no network requests.
- `auto-research.log-progress` emits replay-safe operator progress.
- `auto-research.publish-report` performs the local filesystem side effect.

Live prompts receive only bounded Workflow data and synthetic briefs. Provider
clients, credentials, endpoint, model selection, and raw provider failures stay
inside Worker-owned model Activities.

## Publication and idempotency

`AUTO_RESEARCH_OUTPUT_DIR` is read only by the publication Activity. The
Workflow derives a deterministic publication ID from recorded Workflow ID, Run
ID, and final revision. The Activity creates a stable filename, writes through
a temporary file, and atomically replaces the destination. A retry with the
same approved content returns the same receipt and does not create another
report.

The receipt contains publication ID, filename, revision, and SHA-256 content
hash. It never contains an absolute path. Generated reports and temporary files
must remain outside tracked source and be removed after validation.

## Data and limits

`RecipeInput` contains only text and `mock` or `live` mode. `WorkflowStatus`
contains phase, mode, topic, revision, edit count, question count, bounded draft,
and optional portable publication receipt. `RecipeResult` contains the approved
report and receipt.

Limits:

- topic: 500 characters;
- initial questions: one to four;
- feedback: 1,000 characters;
- expansion topic: 300 characters;
- accepted revise/expand rounds: three;
- source brief: 2,000 characters;
- specialist summary: 5,000 characters; and
- report/query draft: 20,000 characters.

Provider, model, endpoint, credentials, output directory, and absolute paths are
excluded from Workflow input, queries, and result.

## Configuration and live inference

The Worker defaults to task list `auto-research-openai-agents` and local model
selection `llama3.2-local`. `models.yaml` is the only source of model, provider,
and provider-native endpoint. The adapter supports compatible Google, Ollama,
and OpenAI catalog entries without a recipe-owned model ID allowlist. Remote
credential-bearing endpoints require HTTPS; Ollama requires loopback.

Google and OpenAI use Worker-local `MODEL_AI_KEY`. Ollama requires no key. Local
acceptance validation is authorized only for `llama3.2-local`; remote live
validation is deferred.

## Failure and retry behavior

Model Activities have a three-minute schedule-to-close timeout and up to three
attempts with exponential backoff. Authentication, configuration, and schema
errors are non-retryable. Tool and publication Activities have bounded
30-second timeouts and retry policies. Provider errors are sanitized before
entering history. Invalid role output or exhausted Activity retries fail the
Workflow for operator inspection rather than inventing fallback evidence.

## Acceptance and validation

- Offline tests cover deterministic roles, bounds, all review no-op cases,
  first-valid-action-wins, edit limits, query safety, CLI payloads, catalog
  isolation, and atomic/idempotent publication.
- A local Cadence check demonstrates initial draft, revision, expansion,
  stale/duplicate rejection, approval, one publication, and Worker restart.
- History proves completed role, simulated tool, and publication Activities are
  not intentionally repeated.
- Explorer discovers the recipe, resolves the exact current Run, renders the
  report query, and sends all three typed controls through confirmation UI.
- The local Ollama check uses the complete production state machine and Activity
  registrations. A model capability failure is recorded, not bypassed.
- Generator and Explorer drift suites pass.
- No report, cache, virtual environment, secret, or validation artifact remains
  tracked or untracked.
