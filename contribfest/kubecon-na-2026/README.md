# Build Durable AI Agent Examples

Make an agent recover from a failed call, wait safely for a person, or keep
completed work after its Worker stops. Start with an example you can run,
then contribute one improvement you can see and explain.

Join Diana Zawadzki, Shijie Sheng and Cadence maintainers at KubeCon +
CloudNativeCon North America 2026.

- **When:** Wednesday, November 11, 2:20 to 3:40 PM MST
- **Where:** Salt Palace, Level 2, room 255 D, Salt Lake City

## New to Cadence? Start here

1. [Set up your checkout](setup.md#first-time-here-start-with-your-selected-task).
2. Run a prepared agent using the Explorer below. Choose **Incident Triage**,
   **Python**, **mock** for scripted responses and synthetic logs. This needs
   local Cadence and Python 3.12 or 3.13, but no model download or API key.
   Follow the [recipe runbook](../../recipes/incident-triage/README.md) for commands.
3. Open the Run in Cadence Web. Find the evidence the agent collected and the
   draft waiting for human input. You now have a running example to build on.

A **Workflow** coordinates the job. **Activities** perform calls such as
asking a model or reading logs. Cadence records their completed results so
Workflow state can be reconstructed after a Worker restarts. A **Signal**
supplies new input, such as a review decision; your code decides how to use it.

## Choose your contribution

Choose the experience you want in the [task selection page](shortlist.md):

- **Run and explore:** extend a prepared demonstration and inspect its result.
- **Add a capability:** give an agent retries, a review deadline or clearer status.
- **Understand recovery deeply:** protect bookings, replay older histories or
  choose where to resume an investigation.
- **Strengthen an example:** make its tests trustworthy or make it easier to copy.

Each issue starts with the outcome, the contribution and the concept you will
learn. It gives one focused experiment, exact checks and relevant Cadence
resources. Check its difficulty and required services before downloading anything.

Have your own idea? [Propose a workflow](shortlist.md#bring-your-own-workflow)
such as a research assistant, coding agent or travel planner. Pick one useful
result and one failure to handle. Use public or synthetic data.

## Try the Samples Explorer

From the repository root:

```bash
./scripts/explore.sh
```

![Choose Incident Triage and check local Cadence connections in the Samples Explorer](images/explorer-start.png)

*The service checks tell you what still needs to start. Browsing examples does
not require those services.*

The Explorer reads your local code, including your edits. Select an example,
prepare its dependencies and copy the generated Worker and Workflow commands
into separate terminals. Start the services yourself. Use **Resolve latest run**
and **Open history** to inspect the execution in Cadence Web.

Browsing needs Python 3.10+ with venv support; first launch downloads a dependency.
Running a recipe has its own requirements. See the
[Explorer walkthrough](setup.md#explore-the-samples-in-your-browser) for service
checks, review controls and troubleshooting. If browser controls fail, use the
recipe's documented terminal or Cadence Web path and ask a helper.

## What you'll do

**1. See a failure, then change one thing (about 20 minutes).** In the shared
[retry-policy exercise](../../recipes/incident-triage/python/bare/exercises/retry_policy_live/README.md),
an on-call agent investigates a fictional checkout outage using a real local model.
One temporary HTTP 429 stops its log lookup before reasoning begins. Add a
bounded Activity retry policy, rerun it and see the lookup recover while the
completed graph lookup runs only once. Then check that a permanent 404 stops
after one attempt. Cadence performs the retries; your policy decides which
failures to retry and when to stop.

Prepare the model and dependencies before the session. If your laptop cannot
run it, pair with a helper or use the prepared mock example above and choose
a task with fewer dependencies. Scripted responses teach orchestration without
claiming to test model quality.

**2. Make a contribution you can demonstrate.** Claim a prepared task, or get
your own recipe proposal approved. Run its starting check, make the focused
change and compare the result. For a test task, show what incorrect behavior
the new test catches. You do not need to finish an entire agent to contribute.

**3. Share the result.** Show the output, state or history that demonstrates
what changed. Explain where it would help in your own application. Leave a
draft PR or a clear next step on the issue so someone can continue.

<a id="get-ready"></a>

## Get started

- [Prepare your laptop](setup.md).
- **Join `#cadence-contributors` on CNCF Slack.** [Sign up or sign in](https://slack.cncf.io/),
  then search for the channel. Ask for help during the session and continue afterward.
- [See the session outline](agenda.md).

Trying the introductory exercise needs no issue claim. For an upstream
contribution, post exact `/claim` and wait for the winning immutable comment
plus `status:in-progress` before creating the issue branch in your fork. Use
the [participant guide](../../CONTRIBFEST.md) for claims, manual fallback,
signed commits and review. Coding agents are optional; if you use one, start
with the repository's [agent instructions](../../AGENTS.md).

### Issue pool

The [live GitHub issue pool](https://github.com/cadence-workflow/cadence-ai-samples/issues?q=is%3Aissue%20is%3Aopen%20label%3Aevent%3Acontribfest)
is the official source for current availability, sorting and claims. The
selection page is a dated set of prepared ideas. Claim an existing task on its
issue; create a new Recipe proposal for your original workflow.
