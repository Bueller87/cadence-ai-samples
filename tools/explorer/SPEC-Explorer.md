# Cadence AI Samples Explorer — Product Specification

Status: October 5 POC scope implemented and validated; merged to `main` on
October 7, 2026 in `8446cf3`. Post-demo backlog is active.
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
- When the validated resolver provides one safe Workflow ID, show it and allow
  the user to resolve the current Run through Cadence-Web.
- When one safe Workflow ID cannot be derived, require the user to enter one
  exact Workflow ID before resolving a Run. Never choose an arbitrary parent,
  child, batch item, or historical execution.
- Build History and Queries links only from the exact domain, cluster, Workflow
  ID, and resolved or manually entered Run ID.
- Report missing, unreachable, stale, and Continue-As-New-related run changes
  without guessing. Keep domain navigation available after lookup failures.
- Do not claim a Workflow has started merely because its command was generated.
- After resolving one exact current Run, show only the Signal controls declared
  by the selected recipe's valid optional `explorer.yaml`.
- A manually entered historical Run may open History and Queries, but it never
  enables Signal sending.

### Recipe-owned Signal metadata

`explorer.yaml` is an optional Signal-only file at the recipe root. It is not a
general recipe manifest or command catalog. Version 1 has this shape:

```yaml
version: 1
signals:
  - id: stable-control-id
    name: exact_cadence_signal_name
    label: Human-readable label
    description: Short safe help text.
    intent: normal
    arguments:
      - name: request
        label: Request
        type: object
        required: true
        constraints: {}
        help: Short safe argument help.
        fields:
          - name: expected_revision
            label: Expected revision
            type: integer
            required: true
            constraints:
              minimum: 0
              maximum: 1000000
            help: Current revision number.
          - name: detail
            label: Detail
            type: string
            required: true
            constraints:
              min_length: 1
              max_length: 255
            help: Requested change.
```

`version` is required and must equal integer `1`; `signals` is optional. Unknown
fields are errors. Every Signal requires `id`, `name`, `label`, `description`,
`intent`, and `arguments`. Every argument requires `name`, `label`, `type`,
`required`, `constraints`, and `help`. An object argument also requires `fields`;
each field has the same required keys as a scalar argument.

The v1 bounds are:

- The UTF-8 file is at most 16 KiB, contains at most 16 Signals, and each Signal
  contains at most one top-level argument. An object contains 1–8 fields.
- Signal IDs are 1–64 lowercase letters, digits, or hyphens and start with a
  letter. Argument names use the same bound with underscores instead of
  hyphens. IDs, Signal names, and argument names are unique in their scope.
- Signal names are at most 128 characters. Labels are at most 80 characters;
  descriptions and argument help are at most 240 characters.
- Text is nonempty, has no leading or trailing whitespace, control characters,
  or angle brackets. Metadata cannot contain HTML.
- Intent is `normal`, `confirm`, or `destructive`. Both `confirm` and
  `destructive` require a separate inline confirmation action;
  `destructive` also receives danger styling.
- A top-level argument type is `string`, `integer`, `number`, `boolean`, or
  `object`. Object fields use only the four scalar types. String
  constraints allow only `min_length` and `max_length` from 0 through 4096.
  Integer and number constraints allow only `minimum` and `maximum` from
  -1,000,000,000,000 through 1,000,000,000,000. Boolean constraints are empty.
  Object constraints are empty. A minimum cannot exceed its maximum.
- Object field names are unique and follow argument-name bounds. Fields may be
  required or optional in any order. Arrays, arbitrary maps, nested objects,
  unions, binary values, and recursive schemas are unsupported.

The Explorer uses safe YAML loading and rejects unknown versions and fields,
duplicates, malformed YAML, unsupported types or constraints, excessive values,
and metadata files or symlinks that escape the checkout. A missing file or
missing `signals` section means no controls. Invalid metadata also means no
controls, but the recipe remains usable and receives a specific sanitized
metadata error.

The backend returns only sanitized control definitions plus a deterministic
SHA-256 revision of the complete validated metadata to the browser. A Signal
request contains that rendered revision, the stable control ID, an
argument-name/value object, and the resolved Run ID. It never contains a
browser-selected Signal name, intent, schema, or serialized payload.

Before contacting Cadence-Web, the backend reloads and validates the recipe
metadata and rejects a missing or mismatched rendered revision with an
instruction to refresh the Explorer. It validates the values and resolves the
current Run again. Immediately before the Signal POST, it reloads the metadata
and compares the revision again so a declaration change during Run resolution
also fails closed. It refuses to POST if either the metadata or execution
changed, disappeared, or cannot be resolved.

Cadence-Web accepts `signalInput` as a JSON-bearing string and forwards its
bytes to Cadence, but validates the string as exactly one JSON document first.
The Explorer therefore supports only no argument, one scalar argument, or one
bounded object argument. A payload-free Signal omits `signalInput`; a scalar is
encoded as its JSON value; and an object is encoded as one JSON object. For
example, `{expected_revision: 2, detail: "feedback"}` becomes
`{"expected_revision":2,"detail":"feedback"}`. Metadata with several top-level
arguments fails before controls render or Cadence-Web is contacted. The Explorer
does not join positional values, wrap arbitrary positional handlers in an array,
or accept raw JSON from users.

Argument values exist only in the in-memory form and same-origin POST body. They
are not placed in URLs, browser or server logs, persisted storage, durable
evidence, or sanitized error text. Cadence-Web acceptance remains distinct from
Workflow processing.

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
- All discovered recipes have correct selection flows; applicable single-choice dropdowns stay
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

## POC completion status

**Done (October 7, 2026, `8446cf3`, merged to `main`).**

The scope above this section is complete for the current two-recipe checkout and
the October 5 Mac target. The launcher, six-step flow, supported combinations,
commands, service checks, local warm-up, Cadence-Web domain link, focused tests,
and healthy/missing-service walkthrough were implemented and validated. Remaining
limitations and post-demo expansion are tracked below. This baseline merged to
`main` on October 7, 2026 in `8446cf3`.

Post-demo Terminal handoff, current-run deep links, the initial payload-free
Watch Signal controls, and repeatable Watch IDs merged through
[PR #2](https://github.com/cadence-workflow/cadence-ai-samples/pull/2) on
October 8, 2026 in `2693a81`. The recipe-owned metadata contract above
supersedes that Watch-specific Signal implementation while preserving its
delivery history here.

---

## HARD CUT LINE — October 5, 2026 POC demo

Everything below is backlog only. Do not implement before October 6, 2026
(America/Los_Angeles). Reaching that date does not authorize automatic scope
expansion; Kevin must prioritize the next work. Preserve these ideas without
building scaffolding for them during the POC.

## Future backlog — tracked in upstream Issues

The linked Issues are authoritative for remaining status, priority, and
ownership. This section preserves requirements and historical delivered scope.

1. **Contribfest reliability ([Issue #5](https://github.com/cadence-workflow/cadence-ai-samples/issues/5)):**
   deeper Cadence domain/Worker registration/task-list
   diagnostics and a downloadable diagnostic summary with secrets removed. Keep
   “services reachable” distinct from “Worker available.”
2. **Recipe growth without drift ([Issue #7](https://github.com/cadence-workflow/cadence-ai-samples/issues/7)):**
   follow the future
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
3. **Workflow-history deep link ([Issue #3](https://github.com/cadence-workflow/cadence-ai-samples/issues/3)):**
   exact-run navigation is generic whenever the validated resolver provides one
   safe Workflow ID. The Explorer resolves the current Run ID through the local
   Cadence-Web read-only API and provides exact History and Queries links plus a
   manual Run ID override. When an action can produce zero or several Workflows,
   the user must enter one exact Workflow ID before resolution. Resolve again
   after Continue-As-New; never guess a Workflow or Run.
4. **Signal control plane ([Issue #6](https://github.com/cadence-workflow/cadence-ai-samples/issues/6)):**
   optional recipe-root metadata defines bounded controls with zero arguments,
   one scalar, or one object for Recurring AI Watch and Human in the Loop.
   Controls appear only
   after latest-run resolution, revalidate the exact current Run, and report
   Cadence-Web acceptance without claiming Workflow processing. Queries remain
   in Cadence-Web through **Open queries**; arbitrary Signal names and raw JSON
   remain unsupported.
5. **Windows experience ([Issue #4](https://github.com/cadence-workflow/cadence-ai-samples/issues/4)):**
   Linux/Mac and Windows command tabs in the same Explorer,
   plus a Windows launcher; preserve the same selection flow.
6. **Terminal handoff ([Issue #4](https://github.com/cadence-workflow/cadence-ai-samples/issues/4)):**
   the delivered macOS slice opens Terminal.app with a
   re-resolved Worker/Workflow command ready for the user to review and press
   Enter. It never presses Enter automatically; copy remains the permission
   fallback. Other terminal applications and platforms remain future work.
7. **Recipe runtime bootstrap ([Issue #8](https://github.com/cadence-workflow/cadence-ai-samples/issues/8)):**
   make the first five minutes after clone reliable
   by detecting whether the selected implementation is prepared and offering one
   explicit, recipe-scoped setup action. For Python, create/reuse the implementation
   virtual environment and install its declared dependencies; support equivalent
   dependency preparation for other languages. Keep manual setup available, show
   progress and bounded failures, handle partial/repeated setup safely, and never
   turn the Explorer into an arbitrary package installer or shell executor. Worker
   and Workflow handoff must explain when preparation is still required.
8. **Secure live credential handoff ([Issue #10](https://github.com/cadence-workflow/cadence-ai-samples/issues/10)):**
   before opening a live Worker command,
   identify the required LLM and classifier credential names and let the user
   provide their values in a secure local context, such as a hidden Terminal
   prompt or an approved credential store. Never accept, display, persist, log,
   or return secret values through the browser, command preview, URL, Workflow
   input, or history. Keep mock and keyless-local paths free of credential prompts.
9. **Optional infrastructure startup ([Issue #12](https://github.com/cadence-workflow/cadence-ai-samples/issues/12)):**
   explicit start actions only after handling
   existing installations, running services, ports, permissions, and partial states.
10. **Unimplemented classifier opportunities ([Issue #11](https://github.com/cadence-workflow/cadence-ai-samples/issues/11)):**
   add implementations/tests before
   restoring these removed catalog candidates:
   - `kev-local` (`kev`, `kev-latest`): no provider implementation in either recipe.
   - `von-local` (`von`, `von-1.2.0`): no provider implementation in either recipe.
   - `reflex-local` (`reflex`, `Qwen/Qwen3.5-4B`): no provider implementation in
     either recipe.

Milestones motivating later prioritization: Seattle tech talk in approximately
2–3 weeks and November Contribfest, where contributors have about 60 minutes.
