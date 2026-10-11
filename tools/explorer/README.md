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
environment. No Node.js, frontend build, provider keys, or Cadence server is
needed to browse the explorer.

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
Python mock runs still show model/classifier IDs because the current CLI resolves
those catalogs even in mock mode. Go mock actions show the built-in `mock`
classifier and have no generative model or agent-framework selector.

The six numbered steps generate setup, Worker, and matching client commands. Commands
include this checkout's absolute implementation directory so they can be pasted
into either terminal without navigating by hand. Python commands use that
implementation's `.venv/bin/python`; run its one-time setup first. A dedicated
task list includes the sample, implementation, mode, and selected catalog IDs.
The Watch's Workflow ID is shared by start/status/check-now/stop. Copy its run
command and controls separately so pasting a command does not immediately start
and stop the Watch.

Go live batches require an explicit count and concurrency. Their bounds are read
from the recipe's validation code. The acknowledgment action requires IDs from
an existing ticket, so it directs you to the README instead of inventing them.

## Keeping the checkout as the source of truth

- **Directories:** each refresh rediscovers samples, languages, and framework
  directories; new or incomplete recipes remain visible.
- **Catalogs:** `models.yaml` and `classifiers.yaml` are parsed on every refresh
  and command-resolution request. There is no generated catalog snapshot.
- **Python CLI:** a restricted AST reader examines literal `parser()` definitions
  and reads the recipe's selection guards. It never imports recipe modules or
  executes their code, and needs none of their agent/SDK dependencies.
- **Go CLI:** a restricted source reader examines literal `flag.*` declarations,
  the mode switch, classifier mode binding, and selection/batch guards. It does
  not build or execute the Go sample.
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

For Recurring AI Watch, Step 6 shows the generated Workflow ID and can explicitly
resolve its current Run ID through the local Cadence-Web read-only API. This needs
neither the Cadence CLI nor a Cadence SDK dependency in the Explorer. Resolution
never runs automatically and does not imply that a Workflow was started.

After resolution, **Open history** and **Open queries** target that exact run. The
Run ID remains editable so an older exact run can be pasted. Resolve again after
Continue-As-New to replace it with the current run. A missing execution or
unavailable Cadence-Web instance leaves **Open domain** available.

Ticket Routing direct-run links are deferred because its current actions can
create several Workflow IDs. Supporting it requires an explicit Workflow
selection rather than guessing.

After a successful latest-run resolution, Recurring AI Watch also shows
payload-free **Send check-now** and **Send stop-watch** controls. The Explorer
revalidates that the displayed run is still current, maps only those two
allowlisted actions, and proxies the Signal through Cadence-Web. Button feedback
means Cadence-Web accepted or rejected the request; it does not prove the
Workflow processed the Signal. Editing the Run ID disables the controls until the
latest run is resolved again. The copyable CLI controls remain available.

Queries continue to run in Cadence-Web through **Open queries**. Generic Signal
discovery and payload forms are future work; the Explorer does not accept
arbitrary Signal names, raw JSON payloads, or browser-provided serialization.

## Validation

```bash
tools/explorer/.venv/bin/python -m unittest discover -s tools/explorer/tests -v
```

Tests use a temporary checkout and local stub services, without cloud AI,
Cadence, or Terminal.app calls. They cover all 24 supported Watch selections,
120 generated commands against both actual argparse parsers, Go actions and
live-batch bounds, changed catalogs/CLI guards, new/incomplete recipes, a
freshly generated `python/bare` recipe,
non-execution of recipe modules, quoting of paths with spaces, probe/warm-up
failures, Terminal handoff and fallback, action protection, HTTP refresh
behavior, and source-file boundaries.

The optional DOM workflow check requires Node.js and jsdom 26.1.0, solely for
development; these are not explorer runtime dependencies. Install jsdom into a
temporary location and set `NODE_PATH` to its `node_modules` directory:

```bash
node tools/explorer/tests/ui.cjs
```
