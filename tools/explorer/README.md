# Local Samples Explorer

Generate matching Linux/Bash commands from the recipe files in your checkout.
The explorer does not start Workers, Workflows, Cadence, or AI services.

From the repository root:

```bash
./scripts/explore.sh
```

The launcher creates an isolated `tools/explorer/.venv`, installs PyYAML on the
first run, and opens `http://127.0.0.1:8765/`. The explorer itself requires Python
3.10+ and venv support. The selected recipe retains its own runtime requirements
(currently Python 3.12+ for Recurring AI Watch, or Go 1.23 for ticket routing).
The first launcher run needs package-download access. Later launches reuse the
environment. No Java, Gradle, Node.js, frontend build, provider keys, or Cadence
server is needed to browse the explorer.

```bash
./scripts/explore.sh --no-browser       # print the URL, for a headless machine
./scripts/explore.sh --port 0           # choose an available loopback port
```

Stop with Ctrl+C. The service always binds to `127.0.0.1`; there is no public
hosting step. Use an SSH tunnel if you run the launcher on another machine.

## Selection flow

1. Choose a sample discovered under `recipes/`.
2. Choose its language.
3. Choose a framework directory when applicable. There is no agent catalog or
   agent-ID CLI flag.
4. Choose an execution mode/action explicitly when multiple choices exist.
5. Select the model and classifier IDs accepted by that implementation's CLI.

Applicable dropdowns stay visible even when they have only one choice, which is
selected automatically. A selector is hidden when the concept does not apply.
Generated profiles expose exactly `worker` and `start` with explicit `mock` and
`live` modes. Mock mode needs no catalog selection. Live model/classifier
selections and confirmation flags are Worker-only; generated start commands
never contain inference configuration.

The six numbered steps generate setup, Worker, and matching client commands. Commands
include this checkout's absolute implementation directory so they can be pasted
into either terminal without navigating by hand. Python commands use that
implementation's `.venv/bin/python`; run its one-time setup first. A dedicated
task list includes the sample, implementation, mode, and selected catalog IDs.
The Watch's Workflow ID is shared by start/status/check-now/stop. Copy its run
command and controls separately so pasting a command does not immediately start
and stop the Watch.

Ticket Routing uses the same generic Go command contract. Its start command
selects a synthetic ticket inside the external starter. After resolving the
parent Run, use the **Queries** link to open its `ticket-assignment` Custom
Workflow Control in Cadence Web and acknowledge the assignment.

## Keeping the checkout as the source of truth

- **Directories:** each refresh rediscovers samples, languages, and framework
  directories; new or incomplete recipes remain visible.
- **Catalogs:** `models.yaml` and `classifiers.yaml` are parsed on every refresh
  and command-resolution request. There is no generated catalog snapshot.
- **Python CLI:** a restricted AST reader examines literal `parser()` definitions
  and reads the recipe's selection guards. It never imports recipe modules or
  executes their code, and needs none of their agent/SDK dependencies.
- **Go CLI:** a restricted source reader recognizes the generated positional
  `worker|start -mode mock|live` contract. Ticket Routing and generated Go
  profiles use this same contract.
- **Java CLI:** a bounded source reader recognizes only the generated
  positional `RecipeApplication.Command` contract and fixed Gradle invocation.
  It rejects symlinked/out-of-checkout source paths before reading and does not
  generalize arbitrary Java trees, run Gradle, or load Java classes.
- **Evidence:** matching Workflow/run IDs come from each recipe's
  `MATRIX_RESULTS.md`, with the recorded model checked against the selected
  catalog model. These are historical records, not fresh validation of changed
  code or endpoints. “Implemented” and “recorded live evidence” are distinct.

The readers deliberately support the current recipe patterns, not arbitrary
Python or Go. Unrecognized interfaces or guard expressions show an explanation
and the recipe README; unknown arguments never produce copyable commands.
CLI shape changes may require a small reader update. No recipe-owned list of
IDs, frameworks, modes, or supported providers is copied into the explorer.

Refresh rereads the current local files, including uncommitted edits; it does not
fetch GitHub or pull a branch. Resolution rereads and revalidates selections too.
A removed ID, malformed catalog, or rejected selection clears the previous
commands rather than leaving a stale command ready to copy.

## Checks and local warm-up

**Refresh and check services** rereads the checkout and runs bounded, advisory
checks for Cadence, Cadence-Web, and inference dependencies relevant to the
selection. Checks never run automatically on page load or selection changes, and
their results never disable valid commands or links. A TCP connection does not
prove that a domain or Worker is ready.

For a live selection using Ollama or Laya, **Warm up selected local services**
sends the smallest configured request to those loopback services. Warm-up is
explicit, bounded, and reports each component separately. It never calls remote
inference, downloads models, or starts services. Follow the repository-root README
to install services or models before retrying.

Keys remain in the local Worker environment. The explorer does not read `.env`
files, accept credential values, or put keys in commands. Its HTTP endpoints are
uncached and loopback-only. Warm-up and Terminal handoff actions additionally
require same-origin JSON requests. Warm-up accepts only resolved loopback targets,
and Terminal handoff accepts only re-resolved Worker/start actions. File viewing
is limited to public recipe README/evidence/CLI files and the two root catalogs.

## macOS Terminal handoff

On macOS, **Open in Terminal** copies the selected Worker or Workflow command,
opens a separate Terminal.app window, and pastes the command at the prompt. It
does not press Enter or execute the command. Review it before running it.

macOS may ask the application that launched the explorer for Automation or
Accessibility permission. If automatic paste is denied or times out, Terminal
still opens and the command remains copied; press Cmd+V to paste it. Permission
can be changed under **System Settings → Privacy & Security → Automation** or
**Accessibility**. macOS may attribute the request to Cursor, Terminal, Python,
or `osascript`, depending on how the explorer was launched.

The action is limited to commands resolved from the current checkout. The browser
cannot send arbitrary command text, and setup/control commands remain copy-only.
Windows command tabs and terminal handoff are deferred. The Python server remains
independent of the Bash launcher so another entry point can reuse it later.

## Cadence-Web run links

When a selection resolves one safe Workflow ID, Step 6 shows it and can
explicitly resolve its current Run ID through the local Cadence-Web read-only
API. This includes Recurring AI Watch, Human in the Loop, and conforming
generated recipes. Resolution needs neither the Cadence CLI nor a Cadence SDK
dependency in the Explorer. It never runs automatically and does not imply that
a Workflow was started.

After resolution, **Open history** and **Open queries** target that exact run. The
Run ID remains editable so an older exact run can be pasted. Resolve again after
Continue-As-New to replace it with the current run; the Explorer identifies the
previous Run ID as stale when the current Run changes. A missing execution or
unavailable Cadence-Web instance leaves **Open domain** available.

When a selection does not expose one safe Workflow ID, Step 6 requires an exact
Workflow ID before it enables latest-run resolution. Copy the exact ID from the
recipe output; the Explorer does not choose an arbitrary Workflow. A manually
entered Workflow ID and Run ID can open exact History and Queries links without
first resolving the latest Run.

After a successful latest-run resolution, a recipe with valid optional
`explorer.yaml` Signal metadata shows its declared controls. Recurring AI Watch
provides payload-free **Check now** and confirmed **Stop watch** controls. Human
in the Loop provides confirmed approve and reject controls with one required
call-ID field. Editing the Workflow or Run ID hides the controls until the
latest run is resolved again.

Each Signal may declare no argument, one bounded scalar argument, or one
bounded object argument whose named fields are scalar controls. Cadence-Web
requires `signalInput` to contain exactly one JSON document, so metadata with
several top-level arguments is rejected before controls render. Object fields
are rendered as typed inputs; the Explorer does not expose a raw JSON editor.

The browser sends the rendered metadata revision, a stable control ID, and field
values. Before contacting Cadence-Web, the backend reloads the selected recipe's
declaration and rejects missing or changed revisions with an instruction to
refresh the Explorer. It validates the control and values, revalidates that the
displayed Run is still current, and checks the metadata revision again before
the Signal POST. Confirm and destructive controls require a separate
confirmation click. Button feedback means Cadence-Web accepted or rejected the
request; it does not prove the Workflow processed the Signal.

Queries continue to run in Cadence-Web through **Open queries**. The Explorer
does not accept arbitrary Signal names, raw JSON payloads, or browser-provided
schemas and serialization. Recipes without `explorer.yaml` remain usable and
show no Signal panel. Invalid metadata also suppresses the panel and reports a
bounded validation error without disabling the recipe's commands or links.

See the [Explorer specification](SPEC-Explorer.md#recipe-owned-signal-metadata)
for the exact v1 schema and limits. When copying a recipe, copy its
`explorer.yaml` only if the copied Workflow retains those exact Signal handlers.
Update the declaration with the handler contract, or remove it to disable
Explorer Signal controls.

## Validation

```bash
tools/explorer/.venv/bin/python -m unittest discover -s tools/explorer/tests -v
```

Tests use a temporary checkout and local stub services, without cloud AI,
Cadence, Gradle, or Terminal.app calls. They cover supported Watch selections,
generated commands against actual parsers, generic Go actions, changed
catalogs/CLI guards, new/incomplete recipes, both modes and actions for every
generated profile,
non-execution of recipe modules, quoting of paths with spaces, probe/warm-up
failures, Terminal handoff and fallback, action protection, HTTP refresh
behavior, source-file boundaries, strict Signal metadata, backend Signal
serialization, and exact-current-Run revalidation.

The optional DOM workflow check requires Node.js and jsdom 26.1.0, solely for
development; these are not explorer runtime dependencies. Install jsdom into a
temporary location and set `NODE_PATH` to its `node_modules` directory:

```bash
JSDOM_DIR="$(mktemp -d)"
npm_config_registry=https://registry.npmjs.org \
  npm install --prefix "$JSDOM_DIR" jsdom@26.1.0
NODE_PATH="$JSDOM_DIR/node_modules" node tools/explorer/tests/ui.cjs
rm -rf "$JSDOM_DIR"
```

With local Cadence and Cadence-Web running and the Human in the Loop Python
environment prepared, verify that Cadence-Web forwards one structured document
to a typed Python Signal handler and that a query observes both fields:

```bash
recipes/human-in-the-loop/python/openai-agents/.venv/bin/python \
  tools/explorer/tests/local_structured_signal_check.py
```
