# Cadence AI Samples Explorer — Product Specification

Status: agreed product direction, ready for implementation.
Owner: Kevin Burns. Decisions recorded October 4, 2026 (America/Los_Angeles).
Target: October 5, 2026 Cadence Developer Advocacy POC demo.

## Purpose

Make the first run approachable: clone the repository, notice the local explorer,
launch it with one script, and follow a short guided path. Reduce command mistakes,
missing setup steps, and cold-model timeouts. Give newcomers and Contribfest helpers
a useful view of problems that Cadence-Web alone does not expose.

Lead with developer empathy. Short instructions, clear choices, copyable commands,
and useful next actions take precedence over extensive explanation. This is a
focused sample companion, not a general machine-management tool.

## Demo boundary

Tomorrow's demo shows both existing scenarios: Classifier (Ticket Routing) and
Classifier + Agent + LLM (Recurring AI Watch). Kevin will demonstrate the interface
and idea; he will not execute Workflows during the meeting. Diagnostic results must
be real, not simulated green indicators. No full live inference matrix is required
for this POC. Preserve time for a Mac walkthrough and rehearsal.

## Delivery priorities — before the October 5 demo

Implement in this order. If time becomes tight, defer from the bottom, document the
omission, and keep the earlier working path reliable.

1. Reliable Mac launch and obvious root README entry point.
2. Both dynamic sample selection flows, simplified page, and classifier cleanup.
3. Correct Worker/start commands, recipe setup, terminal guidance, and controls.
4. Manual service checks with inline remediation instructions.
5. Explicit local inference warm-up.
6. Cadence-Web link to the correct domain.
7. Focused validation and a rehearsed walkthrough; reserve time throughout delivery.

## User experience — six numbered steps

### 1. Choose a combination

- Show a one-sentence description of each sample's scenario.
- Follow sample → language → framework → mode → model/classifier, as applicable.
- Keep applicable dropdowns visible even with one choice; auto-select that choice.
- Hide a concept only when it does not apply. When multiple execution modes exist,
  require an explicit selection rather than silently selecting live inference.
- The local checkout is the source of truth: recipe directories, actual supported
  CLI behavior, recipe-owned metadata when necessary, and configuration catalogs.
- No agents catalog and no invented `--agent-id` flag. Framework selection maps to
  the implementation directory.
- Refresh rereads checkout data. Preserve still-valid choices, clear invalid ones,
  and recompute dependent commands. Do not retain stale commands after a source error.
- Verify implemented classifiers in code. Retain implemented entries (expected
  Jev and Laya, subject to verification); remove unimplemented candidate entries
  from `classifiers.yaml` and capture them as future issue candidates.
- Keep `models.yaml` intact; Kevin considers it current. Report a discovered
  contradiction rather than silently deleting models.
- Offer combinations supported by the selected implementation, not a blind catalog
  cross-product. Do not infer support from the presence of a YAML entry alone.
- Do not use absence of historical live evidence alone to declare a supported
  combination broken. Existing evidence remains in repository validation/docs.
- An unrecognized or incomplete future recipe remains visible with a short,
  actionable explanation and README link. Never guess runnable commands.

### 2. Check services and prepare

- A manual Refresh button checks relevant services and rereads checkout data.
  No automatic probes on initial page load or selection changes.
- Check Cadence, Cadence-Web, selected local classifier/model services, and remote
  health endpoints where supported. Only check dependencies relevant to the mode.
- Show observed endpoints, timestamp, and specific state. Distinguish unchecked,
  checking, reachable, missing model, authentication required, unreachable,
  unavailable check, and stale results where the probe can establish them.
- Changing configuration or selections invalidates affected results. Never reuse
  a green result for a different endpoint or model.
- Each probe has a bounded timeout and useful failure text. A port accepting
  connections is not proof that inference, a domain, or a Worker is ready.
- Remote checks must be non-billable and avoid inference calls. If no suitable
  health endpoint exists, say that the check is unavailable.
- Checks are fallible advice. Results never block copying commands, opening links,
  or explicitly requested warm-up. Invalid configuration may prevent generating a
  command; a failed health check must not.
- Show short inline setup steps with copy buttons: services, recipe runtime,
  dependencies, environment, and required credential names. Users choose where to
  resume; do not assume whether software is installed or running.
- Users perform setup themselves. Do not start infrastructure, install recipe
  dependencies, download models, or change machine settings from this step.
- Derive instructions from repository documentation and source. Where executable
  facts require metadata, place minimal structured metadata with the recipe and
  validate it against the implementation; avoid brittle runtime prose scraping or
  a second hard-coded database in the explorer.
- Never display, copy, log, or return secret values to the browser. State required
  environment variable names and keep credentials in the local execution environment.

### 3. Warm up local inference

- One explicit button warms the selected locally hosted classifier/model components
  where applicable, with progress and per-component success/failure results.
- Use the smallest suitable request for loading/warming the component. Bound the
  operation and explain timeout/failure without marking it ready.
- Do not trigger billable cloud inference, even through a gateway. A local gateway
  address is not evidence that its inference target is local.
- No automatic model downloads, model installation, service startup, or warm-up.
- For remote-only or mock selections, show a concise explanation that local warm-up
  is not needed. Keep the numbered flow understandable.
- Health and warm-up actions use resolved configuration, not unrelated defaults.
  Warm-up verifies only what was actually exercised; it does not certify a Workflow.

### 4. Copy Worker command

- Label terminal context: “Terminal 1: Worker — leave running.”
- Include the implementation directory and required environment/runtime context;
  make one-time setup available before the command.
- Generate Linux/Mac shell commands accepted by the selected implementation.
- Keep IDs and flags explicit for learning. Resolve framework by directory.
- Use `cadence-ai-samples` as the domain. Share connection settings and task list
  consistently with the Workflow command and diagnostics.
- Quote paths/values correctly. Provide visible copy success/failure feedback.
- No Worker execution from the explorer.

### 5. Copy Workflow command

- Label “Terminal 2: Start Workflow.” The Worker must remain running in Terminal 1.
- Generate the selected implementation's actual start/demo/batch action as applicable.
  Do not force a Worker/start split onto a recipe whose CLI uses a different shape.
- Keep domain, task list, Workflow ID, mode, and relevant selections consistent.
- Keep supported status/check-now/stop controls compact and discoverable here.
  Copy each separately; copying start must never also stop the demo.
- No Workflow execution from the explorer.

### 6. Open Cadence-Web

- Provide a link to the configured Cadence-Web instance and correct domain.
- Keep the link available regardless of advisory health-check results.
- Do not claim a Workflow has started merely because its command was generated.
- Opening a specific Workflow history is future work, not a demo requirement.

## Page simplification

Remove these three standalone cards:

1. Resolved combination.
2. Execution evidence.
3. How this fits the teaching model.

Selections and commands communicate the combination. Keep necessary diagnostics
inside the relevant numbered step. Keep execution evidence in repository docs/tests
and longer teaching explanations in READMEs and the presentation.

## Implementation guardrails

- Continue the existing `tools/explorer/` implementation and `scripts/explore.sh`.
  Prefer the smallest useful change; no framework migration or hosted replacement.
- Serve on loopback only. No public deployment and no dependence on the old hosted
  explorer or obsolete POC branch for runtime data.
- Preserve recipe independence and existing behavior. No broad recipe refactor.
- Keep health checks read-only and warm-up narrowly bounded to the selected local
  inference components. The local web server must not become an arbitrary shell
  executor. Protect action endpoints against unintended cross-origin invocation.
- Keep frontend output free of secrets and sanitize diagnostic errors.
- Cursor may investigate feasibility and choose implementation details. Surface
  a blocker with a concrete fallback rather than expanding scope.

## Acceptance and validation

- Fresh/repeated Mac launcher runs work; README clearly states explorer prerequisites
  separately from recipe prerequisites. Failure gives a useful next step.
- Both samples have correct selection flows; applicable single-choice dropdowns stay
  visible. Mock mode avoids irrelevant inference checks/warm-up.
- Checkout/catalog changes appear on Refresh; invalid selections clear commands.
- Verify classifier implementation before removing candidates; preserve model catalog.
- Generated commands match both Python framework CLIs and the Go action shape.
  Paths with spaces work, settings agree, and controls copy separately.
- Probe tests cover success, refusal, timeout, malformed responses, unsupported health
  check, and stale results. Failures never disable otherwise valid copy/link actions.
- Local warm-up has bounded progress/failure behavior. Tests demonstrate remote
  selections cannot invoke billable inference or download models.
- Cadence-Web link reaches the configured domain; no generated command implies execution.
- Visually inspect on the Mac browser: both scenarios, copy feedback, error rendering,
  numbered order, concise text, and removal of all three cards.
- Rehearse healthy and missing-service views. Document unverified behavior honestly.

---

## HARD CUT LINE — October 5, 2026 POC demo

Everything below is backlog only. Do not implement before October 6, 2026
(America/Los_Angeles). Reaching that date does not authorize automatic scope
expansion; Kevin must prioritize the next work. Preserve these ideas without
building scaffolding for them during the POC.

## Future backlog — proposed priority, for review after the demo

1. **Contribfest reliability:** deeper Cadence domain/Worker registration/task-list
   diagnostics and a downloadable diagnostic summary with secrets removed. Keep
   “services reachable” distinct from “Worker available.”
2. **Recipe growth without drift:** follow the future
   [recipe template system specification](../../templates/SPEC-Recipe-Templates.md)
   and strengthen contributor guidance and CI checks aligning generated profiles,
   catalogs, commands, setup docs, and supported combinations.
   - The current Explorer remains a focused POC whose source readers are tailored
     to the existing recipes.
   - Future generalization should consume the validated generated-recipe contract,
     not infer commands from arbitrary repository shapes.
   - A nonconforming recipe remains visible with its README and an actionable
     unsupported explanation.
   - Add minimal recipe-owned metadata only for facts that layout and validated
     CLI conventions cannot express safely. Do not build a duplicate command
     catalog or generalized authoring platform by default.
   - Explorer web-client portability is separate future work.
3. **Workflow-history deep link:** resolve Run ID using Workflow ID; handle missing
   executions, multiple runs, and Continue-As-New explicitly.
4. **Windows experience:** Linux/Mac and Windows command tabs in the same explorer,
   plus a Windows launcher; preserve the same selection flow.
5. **Terminal handoff:** open a new terminal with the Worker/Workflow command ready
   for the user to review and press Enter. No automatic execution.
6. **Optional infrastructure startup:** explicit start actions only after handling
   existing installations, running services, ports, permissions, and partial states.
7. **Unimplemented classifier opportunities:** add implementations/tests before
   restoring these removed catalog candidates:
   - `kev-local` (`kev`, `kev-latest`): no provider implementation in either recipe.
   - `von-local` (`von`, `von-1.2.0`): no provider implementation in either recipe.
   - `reflex-local` (`reflex`, `Qwen/Qwen3.5-4B`): no provider implementation in
     either recipe.

Milestones motivating later prioritization: Seattle tech talk in approximately
2–3 weeks and November Contribfest, where contributors have about 60 minutes.
