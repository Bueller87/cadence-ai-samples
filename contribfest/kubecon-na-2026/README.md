# Build Durable AI Agent Examples

Join Diana Zawadzki, Shijie Sheng and Cadence maintainers at KubeCon +
CloudNativeCon North America 2026.

- **When:** Wednesday, November 11, 2:20 to 3:40 PM MST
- **Where:** Salt Palace, Level 2, room 255 D, Salt Lake City

## Try the Samples Explorer

Start with the [Samples Explorer](../../tools/explorer/README.md) to choose
an agent recipe, prepare its dependencies and follow a Run in Cadence Web.
It reads your local code, including your edits, and gives you matching Worker
and Workflow commands.

![Choose Incident Triage and check local Cadence connections in the Samples Explorer](images/explorer-start.png)

*Browse first, then start the services your recipe needs. Here the checks show
that Cadence and Cadence Web are not running.*

From the repository root:

```bash
./scripts/explore.sh
```

You can browse with Python 3.10+ and venv support, before installing Cadence or
a model. The first launch downloads a Python dependency. See
[setup](setup.md#explore-the-samples-in-your-browser) for requirements.

Use it to:

- **Choose an example.** Select a recipe, language, framework and mock or live
  mode. The Explorer generates commands for that implementation.
- **Prepare and check your setup.** Install the recipe's dependencies with
  **Prepare recipe dependencies**, then use **Refresh and check services**.
  For a local live model, **Warm up selected local services** checks a small
  request before you start the Workflow.
- **Run your code.** Copy the Worker and Workflow commands into separate
  terminals. On macOS, **Open in Terminal** pastes a command for you to review
  and run.
- **Follow a Run.** Use **Resolve latest run**, then **Open history** or
  **Open queries** to inspect it in Cadence Web.
- **Send review input.** Recipes with Signal controls let you send guidance,
  request a revision or approve a result. Check the Run's state afterward to
  see how it handled your input.

For a first try, choose **Incident Triage**, **python**, **mock** (this recipe
has no framework selector) and follow
the [recipe runbook](../../recipes/incident-triage/README.md). Mock mode uses
scripted responses and needs no model or API key. Running the Workflow still
needs Cadence; the Run links need Cadence Web. The Explorer prepares recipe
dependencies, but you start the services, Worker and Workflow yourself.

## What you'll do

**Bring your own workflow and make it durable.** Bring an agent or script you
already run that makes at least one model or tool call. By the end, it runs
as a Cadence Workflow and survives the failure you're most worried about.

**1. Warm up together (about 20 minutes).** Incident Triage is an AI agent
investigating a fictional checkout outage with a real local model.

- **See it fail:** a log lookup gets an HTTP 429 rate limit, and with no retry
  policy the agent stops before it starts reasoning.
- **First win:** you add a [Cadence Activity retry policy](https://cadenceworkflow.io/docs/python-client/retries/), in one file. Run it
  again: the lookup waits, retries and the agent carries on. No retry loop in
  your code.
- **Handle a permanent failure:** in this exercise, a 404 is non-retryable, so retrying it only
  wastes time. Your policy must stop it after one attempt. Check both outcomes
  in Cadence Web.

Instructions:
[retry-policy exercise](../../recipes/incident-triage/python/bare/exercises/retry_policy_live/README.md).

**2. Make your own workflow durable (the rest of the session).**

1. Wrap: run your agent inside one [Activity](https://cadenceworkflow.io/docs/concepts/activities/), started by a [Workflow](https://cadenceworkflow.io/docs/concepts/workflows/).
   Every run, input, result and retry shows up in Cadence Web.
2. Split: move the model call and each tool call into their own Activities,
   so each one retries on its own and finished work isn't redone.
3. Steer (stretch): add a [Signal](https://cadenceworkflow.io/docs/python-client/signals/) so a person can approve or redirect.

Stop at any depth. Start from [#118](https://github.com/cadence-workflow/cadence-ai-samples/issues/118): open your own Issue for your
workflow, so someone can continue it if you don't finish. Steps are in
[Find something to work on](shortlist.md#bring-your-own-workflow).

**Want to explore first?** [#119](https://github.com/cadence-workflow/cadence-ai-samples/issues/119): break a running agent on purpose
(stop the worker, send a Signal mid-run) and file what surprised you.

**Looking for a task?** Browse the [full issue pool](#issue-pool) in Get started,
then check the [approved tasks and claim steps](shortlist.md#contribfest-issues).
A facilitator records your claim before you start.

A merged fix, a draft pull request or a written next step are all good
outcomes. You can work by hand or with a coding agent. If you use an agent,
start it with the repository's [agent instructions](../../AGENTS.md).

<a id="get-ready"></a>

## Get started

- [Set up your laptop](setup.md).
- **Join `#cadence-contributors` on CNCF Slack.** [Sign up or sign in](https://slack.cncf.io/),
  then search for `#cadence-contributors` and join the channel. Ask setup,
  task and review questions there, and stay in touch after the session.
- [Find something to work on](shortlist.md).
- [See the session outline](agenda.md).

Ask a maintainer if you get stuck or want help choosing a task. The
[participant guide](../../CONTRIBFEST.md) covers claiming Issues, sign-off and
review.

### Issue pool

This catalog covers the prepared attendee tasks. Open each Issue for its
runtime requirements, exact commands and success criteria. Readiness and
ownership live on GitHub: choose only an open, unassigned `status:ready` Issue
with no existing claim or active implementation PR. Ask a facilitator to
record your claim before creating a branch.

**On hold:** [#109](https://github.com/cadence-workflow/cadence-ai-samples/issues/109)
has not passed its model-output quotation checks. Choose another task while
its `status:ready` label is absent.

[#118](https://github.com/cadence-workflow/cadence-ai-samples/issues/118) and
[#119](https://github.com/cadence-workflow/cadence-ai-samples/issues/119) are
invitations to propose your own work or report a finding, so do not claim
those umbrella Issues. Organizer preparation is tracked separately in
[#92](https://github.com/cadence-workflow/cadence-ai-samples/issues/92).

| Recipe | Issue | What you will practice |
| --- | --- | --- |
| Incident Triage | [#109: Help the triage model identify the deepest observed error](https://github.com/cadence-workflow/cadence-ai-samples/issues/109) | On hold: model-output quotation checks have not passed |
| Incident Triage | [#110: Test that revision reuses evidence and guidance collects more](https://github.com/cadence-workflow/cadence-ai-samples/issues/110) | Test review Signals and evidence reuse |
| Incident Triage | [#111: Demonstrate recovery from a rate-limited model call](https://github.com/cadence-workflow/cadence-ai-samples/issues/111) | Recover a model Activity after a rate limit |
| Incident Triage | [#112: Fix the Incident Triage clean-copy check](https://github.com/cadence-workflow/cadence-ai-samples/issues/112) | Keep copied recipes independent of repository files |
| Incident Triage | [#120: Demonstrate expiration of an unanswered triage approval](https://github.com/cadence-workflow/cadence-ai-samples/issues/120) | Observe an unanswered approval timer expire |
| Incident Triage | [#121: Test that a lost reply does not save the conclusion twice](https://github.com/cadence-workflow/cadence-ai-samples/issues/121) | Test an idempotent write after a lost reply |
| Incident Triage | [#132: Check that triage claims quote collected evidence](https://github.com/cadence-workflow/cadence-ai-samples/issues/132) | Check claims against the evidence collected |
| Incident Triage | [#133: Print a summary of work repeated after restart](https://github.com/cadence-workflow/cadence-ai-samples/issues/133) | Compare repeated work after restart |
| Incident Triage | [#164: Rewind an agent investigation with Workflow Reset](https://github.com/cadence-workflow/cadence-ai-samples/issues/164) | Reset at a supported history boundary |
| Agent Handoffs | [#122: Stop retrying invalid booking input](https://github.com/cadence-workflow/cadence-ai-samples/issues/122) | Distinguish invalid input from temporary failure |
| Agent Handoffs | [#123: Prevent duplicate bookings after a lost reply](https://github.com/cadence-workflow/cadence-ai-samples/issues/123) | Deduplicate bookings after a lost reply |
| Agent Handoffs | [#124: Set an explicit limit on agent turns](https://github.com/cadence-workflow/cadence-ai-samples/issues/124) | Bound the number of agent turns |
| Agent Handoffs | [#125: Test recovery and exhaustion for booking retries](https://github.com/cadence-workflow/cadence-ai-samples/issues/125) | Test bounded retries and exhaustion |
| Agent Handoffs | [#126: Show the selected agent and booking receipts in status](https://github.com/cadence-workflow/cadence-ai-samples/issues/126) | Expose agent decisions and receipts through Queries |
| Human in the Loop | [#127: Retry temporary ride-booking failures](https://github.com/cadence-workflow/cadence-ai-samples/issues/127) | Retry a temporary booking failure |
| Human in the Loop | [#128: Expire unanswered ride approvals](https://github.com/cadence-workflow/cadence-ai-samples/issues/128) | Expire an unanswered approval |
| Human in the Loop | [#129: Prevent duplicate rides after a lost booking reply](https://github.com/cadence-workflow/cadence-ai-samples/issues/129) | Use a stable key to avoid duplicate rides |
| Human in the Loop | [#130: Limit repeated approval rounds](https://github.com/cadence-workflow/cadence-ai-samples/issues/130) | Bound repeated human approval rounds |
| Human in the Loop | [#131: Show whether a ride approval was applied](https://github.com/cadence-workflow/cadence-ai-samples/issues/131) | Show whether a review Signal took effect |
| Human in the Loop | [#165: Make the Human in the Loop tests run in a copied recipe](https://github.com/cadence-workflow/cadence-ai-samples/issues/165) | Keep recipe tests usable outside the full repository |
| Auto Research | [#134: Expire unanswered research reviews](https://github.com/cadence-workflow/cadence-ai-samples/issues/134) | Expire an unanswered research review |
| Auto Research | [#135: Stop retrying invalid research sources](https://github.com/cadence-workflow/cadence-ai-samples/issues/135) | Exclude invalid sources from Activity retries |
| Auto Research | [#136: Reject research plans that exceed the limits](https://github.com/cadence-workflow/cadence-ai-samples/issues/136) | Reject plans that exceed the agreed limits |
| Auto Research | [#137: Test early, invalid and duplicate research decisions](https://github.com/cadence-workflow/cadence-ai-samples/issues/137) | Test early, stale and duplicate review Signals |
| Ticket Routing | [#34: Align Ticket Routing tests with the declared Go 1.23 version](https://github.com/cadence-workflow/cadence-ai-samples/issues/34) | Keep example tests compatible with the supported toolchain |
| Ticket Routing | [#138: Stop retrying invalid mock tickets](https://github.com/cadence-workflow/cadence-ai-samples/issues/138) | Stop retrying invalid mock input |
| Ticket Routing | [#139: Add a low-confidence mock ticket example](https://github.com/cadence-workflow/cadence-ai-samples/issues/139) | Make uncertain routing visible in mock mode |
| Ticket Routing | [#140: Show the ticket acknowledgment deadline in parent status](https://github.com/cadence-workflow/cadence-ai-samples/issues/140) | Expose a child Workflow deadline in parent status |
| Ticket Routing | [#141: Replay ticket acknowledgment and timeout histories](https://github.com/cadence-workflow/cadence-ai-samples/issues/141) | Replay routed and timed-out Workflow histories |
| Recurring AI Watch | [#142: Skip a release after repeated processing failures](https://github.com/cadence-workflow/cadence-ai-samples/issues/142) | Limit repeated failures across watch cycles |
| Recurring AI Watch | [#143: Retry report generation without repeating classification](https://github.com/cadence-workflow/cadence-ai-samples/issues/143) | Retain completed classification across report retries |
| Recurring AI Watch | [#144: Preserve check-now requests across Workflow Runs](https://github.com/cadence-workflow/cadence-ai-samples/issues/144) | Carry pending requests through Continue-As-New |
| Recipe documentation | [#145: Add Cadence Web screenshots to the recipe guides](https://github.com/cadence-workflow/cadence-ai-samples/issues/145) | Connect runbook instructions to visible Run state |
| Java templates | [#71: Add Java 17 profile tests to pull-request CI](https://github.com/cadence-workflow/cadence-ai-samples/issues/71) | Generate and test copyable examples in CI |
| Your own contribution | [#118: Bring an agent and make one failure recoverable](https://github.com/cadence-workflow/cadence-ai-samples/issues/118) | Invitation: propose your own durable agent example |
| Your own contribution | [#119: Explore how an agent recovers from interruption](https://github.com/cadence-workflow/cadence-ai-samples/issues/119) | Invitation: explore recovery and report a finding |

Use the [approved tasks and claim steps](shortlist.md#contribfest-issues) to
check current availability. The table includes tasks awaiting verification
and tasks with an existing owner; inclusion alone does not make a task claimable.
