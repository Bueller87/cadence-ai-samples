# Cursor handoff — Cadence AI Samples Explorer

## Mission

Implement the agreed POC in `SPEC-Explorer.md` on the existing feature branch.
Make this a concise, useful first-run companion driven by the local repository.
Kevin is the Product Owner; you own investigation, implementation, and focused
validation. Work autonomously within the acceptance criteria and scope boundary.

Read `SPEC-Explorer.md` first. It is the authoritative product contract and includes
the exact six-step flow, delivery priorities, and future backlog. Do not require
access to this conversation to proceed.

## Repository and current baseline

- Upstream: https://github.com/cadence-workflow/cadence-ai-samples
- Fork: https://github.com/Bueller87/cadence-ai-samples
- Working branch: `feat/local-samples-explorer` on the fork.
- Draft upstream PR: https://github.com/cadence-workflow/cadence-ai-samples/pull/1
- Last user-confirmed pushed commit: `88471fe4c79873ecfe5fa7c8bf3ea7b2f6ddea78`.
  It adds the first explorer in 13 files. Verify the current branch; do not reset to
  this historical SHA or overwrite later user changes.
- Kevin will put this handoff and the spec on that branch before continuing in Cursor.
- Personal POC repository was renamed to `cadence-ai-samples-poc`. It and the earlier
  hosted explorer are historical references, not sources of current runtime truth.
- There is no agents catalog requirement in the accepted repo. Frameworks are
  implementation directories. Older chat examples with `--agent-id` are obsolete.

Existing relevant files (verify against current checkout):

```text
scripts/explore.sh
tools/explorer/README.md
tools/explorer/explorer.py
tools/explorer/source.py
tools/explorer/server.py
tools/explorer/requirements.txt
tools/explorer/static/index.html
tools/explorer/static/app.js
tools/explorer/static/style.css
tools/explorer/tests/test_explorer.py
tools/explorer/tests/ui.cjs
models.yaml
classifiers.yaml
recipes/ticket-routing/
recipes/recurring-ai-watch/python/
```

The existing architecture is a small Python localhost server with plain HTML/JS.
Continue it. Discover exact commands and options from current recipe source and
READMEs, not this list or an assumed uniform CLI.

## Deadline and demo intent

Today when planned: October 4, 2026, America/Los_Angeles.
Demo: October 5, 2026 Cadence Developer Advocacy meeting, on Kevin's Mac.
Show both Classifier and Classifier + Agent + LLM scenarios. Kevin will not run
Workflows in the meeting. This is an interactive idea walkthrough, not a full live
matrix demonstration. Preserve time for launch verification and visual rehearsal.

Follow the specification's ordered priorities. No work below its HARD CUT LINE
before October 6, 2026; even then, wait for Kevin's prioritization. Do not build
Windows support, terminal launching, automatic infrastructure startup, or history
lookup now. If a lower-priority item cannot fit, record the deferral and report it.

## Agreed behavior to preserve

1. Local checkout defines sample/language/framework/mode/catalog choices. Applicable
   single-choice dropdowns remain visible and auto-selected. Explicit mode selection
   when multiple choices exist. Refresh rereads files; invalid selections clear commands.
2. Manual Refresh runs relevant service checks. No automatic checks on selection or
   load. Results are advisory; never block valid commands or links based on health.
   Report precise observed state and bounded failures; invalidate stale results.
3. One explicit local warm-up button. No billable remote inference, downloads, or
   service startup. Non-billable remote health checks only where available; a local
   gateway can still route to cloud inference, so do not classify it by URL alone.
4. Copy Worker command with setup and Terminal 1 context.
5. Copy Workflow/start/demo action with Terminal 2 context and separate compact controls.
6. Open the configured Cadence-Web domain, normally `cadence-ai-samples`.

Remove the Resolved combination, Execution evidence, and Teaching model cards.
Keep concise diagnostics/setup inside the numbered flow. Users perform setup in
their terminals and resume from their machine's current state.

Verify Jev/Laya implementation and remove only unimplemented classifier catalog
candidates. Preserve removed entries/rationale as issue candidates in a small local
backlog note or the spec; do not create GitHub issues without Kevin's instruction.
Keep `models.yaml` intact unless Kevin approves a concrete correction. Do not
equate lack of historical execution evidence with lack of implementation.

## Start here

1. Read applicable `AGENTS.md` instructions, the spec, current explorer docs/source,
   recipe CLIs/READMEs, catalogs, and classifier factories.
2. Inspect `git status`, branch, and remotes before editing. Preserve local changes.
   If the checkout is on another branch or the target is ambiguous, clarify before
   switching or merging. No destructive reset, rebase, or force push.
3. Run the existing launcher on the Mac:

   ```bash
   ./scripts/explore.sh
   ```

4. Establish the baseline. The existing explorer was reported as tested, but those
   results are historical. Reproduce the relevant checks on this checkout.
5. Implement in priority order. Prefer minimal recipe-owned metadata only when source
   discovery cannot reliably express executable facts; validate it rather than create
   a parallel explorer catalog. Do not implement a general metadata platform now.
6. Keep brief progress updates focused on results, remaining uncertainty, and time
   needed for the Mac walkthrough. Surface a blocker with a practical fallback.

## Prior validation — reported, not rerun for these documents

The initial version reportedly passed 13 Python tests, HTTP/DOM integration checks,
and parser checks across 60 Watch selections / 120 commands. Kevin previewed it
successfully on Windows and said both existing sample flows looked good.
Visual automated browser QA was not completed because Chromium download failed.
The Linux/Mac launcher still needs a real Mac walkthrough. Inspect current tests;
catalog cleanup can legitimately change selection counts.

## Validation expectations

Find and use the current documented test commands. The baseline Python suite can
be invoked from the repository root after launcher dependency setup with:

```bash
tools/explorer/.venv/bin/python -m unittest discover -s tools/explorer/tests -p 'test_*.py'
```

Inspect `tools/explorer/README.md` and `tools/explorer/tests/ui.cjs` for current DOM
test invocation and test-only dependencies; do not add Node as a runtime prerequisite.

Add meaningful coverage for new behavior:

- Accurate supported choices and recipe CLI commands; model catalog preserved;
  unimplemented classifiers removed with issue candidates retained.
- Refresh after source edits, invalid selection clearing, path quoting, consistent
  IDs/endpoints/domain, and separately copied controls.
- Probes: timeout, refusal, authentication, malformed response, unavailable check,
  and state invalidation after selection change. No inference needed to check remote health.
- Warm-up: selected local targets only; explicit user action, bounded operation,
  progress/error result, no automatic download or cloud inference.
- Advisory checks do not block otherwise valid copy/link actions.
- Loopback binding, action endpoint protection, and no secrets in frontend/log output.

Use fixtures or local stub services for failures rather than requiring cloud keys
or paid inference. A focused real local warm-up check is useful if services already
exist and Kevin elects to test it. Do not run Workflows or start/stop secret-bearing
Workers without coordinating with Kevin. Never ask him to paste API keys.

Visually inspect on the actual Mac browser: both samples, mock/live differences,
single-choice selectors, loading/stale/failure states, setup instructions, copy
feedback, six-step order, and removal of all three cards. Do not spend the demo
window repeatedly retrying an unavailable automated browser download; use a direct
Mac walkthrough and report what was verified.

## Guardrails and delivery

- Modify the existing repo branch; no public hosting, unrelated service deployment,
  framework rewrite, broad recipe refactor, or Windows implementation.
- The explorer may probe and explicitly warm selected local inference. It may not
  become an arbitrary shell executor or execute Workers/Workflows.
- Do not expose credentials in URLs, command previews, diagnostics, or browser data.
- Do not assume a health response proves model readiness, Worker registration,
  or successful Workflow execution. Report only observed facts.
- Avoid adding unrelated features or performing a full live matrix for this demo.
- Kevin owns committing/pushing unless he instructs you otherwise. Keep the PR draft;
  do not merge, mark ready, or send external messages on your own.
- The project uses DCO sign-off: `git commit -s`. Lowercase `-s` adds the declaration;
  uppercase `-S` cryptographically signs and is not the same requirement. Follow
  current repository policies; do not introduce a certificate setup requirement.

Finish with a concise report: implemented priorities, commands/checks run and their
results, Mac visual verification, remaining limitations, and any explicit deferrals.
Give Kevin a short rehearsal path through both samples with one missing-service
example. Keep future ideas in the spec; do not implement them to fill spare time.
