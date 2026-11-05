# Choose what to make happen

**Start with an agent you can run, then choose one thing to improve.**
Bring your own workflow or build on a prepared example below.

The [live GitHub issue pool](https://github.com/cadence-workflow/cadence-ai-samples/issues?q=is%3Aissue%20is%3Aopen%20label%3Aevent%3Acontribfest)
is the **official source** for sorting, availability and claims. These tables
are a November 5 snapshot of prepared contributions. They do not limit your ideas.

## Get a first result

New to Cadence? [Run a prepared agent](README.md#new-to-cadence-start-here)
before choosing an implementation task. Start with scripted mock responses if
you do not have a model installed. Running a Workflow still needs Cadence.

Want to build something of your own? [Propose your workflow](#bring-your-own-workflow).
Choose a useful result and one failure it should recover from.

<a id="choose-a-contribfest-issue"></a>

## ContribFest Issues

1. Open your selected issue in the live pool. Check that it is open,
   `status:ready`, unassigned and has no earlier claim or active implementation PR.
2. Comment: **“I'd like to take this. GitHub: @yourname. Branch: `issue-N-short-slug`.”**
3. Wait for a facilitator to record ownership and replace `status:ready` with `status:in-progress`.
   Then create the branch in your fork and follow the issue's instructions.

You do not need label permissions. If a task is taken, choose another or propose
your own. Trying the shared introductory exercise needs no issue claim.

<a id="choose-your-level"></a>

## Choose your level

**Beginner:** a focused edit with a clear result. **Intermediate:** connect
behavior across steps. **Advanced:** reason about persistent state or history
across executions. These describe the change, not your ability. Coding agents
are optional at every level. Check setup separately from difficulty.

### Run and explore

Extend a prepared demonstration and see the agent respond. Existing commands are starting points; each issue names the scenario or output you will add.

| What you will make possible | Your contribution | Level | Setup |
| --- | --- | --- | --- |
| [Watch triage recover from a model rate limit · #111](https://github.com/cadence-workflow/cadence-ai-samples/issues/111) | Exercise | Intermediate | Python + Cadence + model |
| [Watch unanswered triage approval expire · #120](https://github.com/cadence-workflow/cadence-ai-samples/issues/120) | Exercise | Intermediate | Python + Cadence + model |
| [See which investigation calls a restart avoids repeating · #133](https://github.com/cadence-workflow/cadence-ai-samples/issues/133) | Output | Beginner | Python + Cadence |
| [Try a support ticket the agent is too uncertain to route · #139](https://github.com/cadence-workflow/cadence-ai-samples/issues/139) | Mock example | Beginner | Go 1.24+ |

### Add a capability

Give an agent a useful behavior: clearer progress, bounded work, safer review or sensible retries. Each issue explains what Cadence provides and what your application must decide.

| What you will make possible | Your contribution | Level | Setup |
| --- | --- | --- | --- |
| [Stop invalid trip bookings after one attempt · #122](https://github.com/cadence-workflow/cadence-ai-samples/issues/122) | Feature | Intermediate | Python + Cadence |
| [Bound trip-agent turns and retain completed bookings · #124](https://github.com/cadence-workflow/cadence-ai-samples/issues/124) | Feature | Intermediate | Python |
| [See the trip specialist and completed bookings · #126](https://github.com/cadence-workflow/cadence-ai-samples/issues/126) | Feature | Beginner | Python |
| [Recover an approved ride from a temporary failure · #127](https://github.com/cadence-workflow/cadence-ai-samples/issues/127) | Feature | Intermediate | Python + Cadence |
| [Expire ride proposals that nobody approves · #128](https://github.com/cadence-workflow/cadence-ai-samples/issues/128) | Feature | Intermediate | Python + Cadence |
| [Stop repeated ride-approval requests at a clear limit · #130](https://github.com/cadence-workflow/cadence-ai-samples/issues/130) | Feature | Intermediate | Python |
| [Explain why a ride approval did or did not apply · #131](https://github.com/cadence-workflow/cadence-ai-samples/issues/131) | Feature | Intermediate | Python |
| [Flag report quotations that the logs do not support · #132](https://github.com/cadence-workflow/cadence-ai-samples/issues/132) | Feature | Advanced | Python |
| [Expire research drafts without publishing them · #134](https://github.com/cadence-workflow/cadence-ai-samples/issues/134) | Feature | Intermediate | Python + Cadence |
| [Stop research from retrying an invalid source · #135](https://github.com/cadence-workflow/cadence-ai-samples/issues/135) | Feature | Intermediate | Python + Cadence |
| [Keep research plans within an explicit scope · #136](https://github.com/cadence-workflow/cadence-ai-samples/issues/136) | Feature | Beginner | Python |
| [Stop invalid support tickets after one attempt · #138](https://github.com/cadence-workflow/cadence-ai-samples/issues/138) | Feature | Intermediate | Go 1.24+ |

### Understand recovery deeply

Explore what must survive a failure and what may run again. These tasks are useful when you need reliable bookings, safe code upgrades or agents that run across many cycles.

| What you will make possible | Your contribution | Level | Setup |
| --- | --- | --- | --- |
| [Recover trip receipts without duplicate bookings · #123](https://github.com/cadence-workflow/cadence-ai-samples/issues/123) | Feature | Advanced | Python + SQLite |
| [Prove booking recovery and retry exhaustion · #125](https://github.com/cadence-workflow/cadence-ai-samples/issues/125) | Tests | Intermediate | Python + Cadence |
| [Recover one ride receipt without booking another ride · #129](https://github.com/cadence-workflow/cadence-ai-samples/issues/129) | Feature | Advanced | Python + SQLite |
| [See a support ticket's real acknowledgment deadline · #140](https://github.com/cadence-workflow/cadence-ai-samples/issues/140) | Feature | Advanced | Go 1.24+ + Cadence |
| [Keep acknowledged and expired ticket histories replayable · #141](https://github.com/cadence-workflow/cadence-ai-samples/issues/141) | Tests | Advanced | Go 1.24+ + Cadence |
| [Keep a release watcher moving past a failing update · #142](https://github.com/cadence-workflow/cadence-ai-samples/issues/142) | Feature | Advanced | Python |
| [Correct a draft without repeating earlier evidence calls · #164](https://github.com/cadence-workflow/cadence-ai-samples/issues/164) | Exercise | Advanced | Python + Cadence |

### Strengthen an example

Make an example easier to reuse or prove that its guarantees hold. These contributions improve tests, portability or CI; they do not all add new agent behavior.

| What you will make possible | Your contribution | Level | Setup |
| --- | --- | --- | --- |
| [Run the ticket example's tests on Go 1.23 · #34](https://github.com/cadence-workflow/cadence-ai-samples/issues/34) | Reusable tests | Beginner | Go 1.23 + newer |
| [Catch broken generated Java examples · #71](https://github.com/cadence-workflow/cadence-ai-samples/issues/71) | CI | Intermediate | Java 17 + Gradle |
| [Prove revisions reuse evidence and guidance gathers more · #110](https://github.com/cadence-workflow/cadence-ai-samples/issues/110) | Tests | Intermediate | Python |
| [Take the triage example into your own project · #112](https://github.com/cadence-workflow/cadence-ai-samples/issues/112) | Reusable tests | Beginner | Python |
| [Prove a lost reply cannot save a report twice · #121](https://github.com/cadence-workflow/cadence-ai-samples/issues/121) | Tests | Beginner | Python |
| [Prove repeated approvals publish a report only once · #137](https://github.com/cadence-workflow/cadence-ai-samples/issues/137) | Tests | Intermediate | Python |
| [Take the ride-approval example into your own project · #165](https://github.com/cadence-workflow/cadence-ai-samples/issues/165) | Reusable tests | Beginner | Python |

**Setup notes:** Python tasks use 3.12 or 3.13. “Python” alone means the core
checks need no Cadence service or model. Install dependencies first; keep
localhost available for test fixtures. Each issue gives the full requirements.
Other Go tasks use 1.24+ while #34's compatibility fix remains open. #109 is
held while its quotation-quality checks fail; existing claims remain reserved.

### Recipe tasks

In the live pool above, filter with `purpose:run-and-explore`,
`purpose:add-capability`, `purpose:recovery` or `purpose:strengthen-example`.
Add `difficulty:beginner`, `difficulty:intermediate` or `difficulty:advanced`
to narrow the change complexity. Combine these with a recipe label and
`status:ready`; then check claims before starting.

## Bring your own workflow

**Build something useful to you and show how it recovers from one failure.**
Try a research assistant that resumes after a Worker stops, a travel agent
that waits for approval, or a coding agent that keeps completed tool results.
Invent your own example; use public or synthetic data.

1. [Open a Recipe proposal](https://github.com/cadence-workflow/cadence-ai-samples/issues/new?template=recipe-proposal.yml).
   Start with the application: who uses your agent and what it produces.
   Describe one failure, what should survive it and how someone can see the
   recovery without paid model access. Give a small first scope and an intended
   `recipes/<your-slug>/SPEC.md` path; the form guides the remaining fields.
2. Mention ContribFest and link invitation #118 in your description. Ask a
   facilitator to review it. Wait for approval as `status:ready` and a recorded
   claim as `status:in-progress` before upstream implementation.
3. Build and test your example in your fork. Show its useful output, cause
   the selected failure and inspect the recovery. Record setup, Workflow/Run
   IDs and the results you actually observed. Keep progress and your
   draft PR on that issue so someone can continue if you stop.

Claim an existing issue when improving its task. Create a new proposal for
your own example. Do not duplicate a task or claim the umbrella invitation.

**A useful Cadence distinction:** a Workflow coordinates the job; Activities
perform calls such as asking a model or saving a report. Cadence records
completed Activity results. Put calls in separate Activities to give them
separate recovery points. Retrying a write can repeat its effect, so use a
stable request key when a repeated call must not save or book twice.

## Extend the exercise

Propose one new failure scenario using the Recipe form above. The
[retry exercise](../../recipes/incident-triage/python/bare/exercises/retry_policy_live/README.md)
is a starting point. Check the live pool first; a prepared task may already
cover your idea.

## Explore and file what you find

Run a sample in mock mode, stop your Worker, restart it and inspect what
survived. Use the [Explorer walkthrough](setup.md#optional-explore-the-samples-in-your-browser)
for controls and requirements. Record the steps and observations in a finding
through the repository's issue forms; #119 describes this exploration path.

## Leave a useful result

Show one observation that explains your contribution: a recovered draft, a
receipt reused, a deadline honored or a test that catches a deliberate bug.
Say what your change enables in an agent of your own.

[Hand off your contribution](../../CONTRIBFEST.md#4-finish-or-hand-off) with
changed files, commands, results and the next step. A focused draft PR or a
clear unfinished handoff helps the next contributor.
