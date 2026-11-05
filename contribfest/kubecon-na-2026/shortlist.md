# Find something to work on

Start with the [retry-policy exercise](../../recipes/incident-triage/python/bare/exercises/retry_policy_live/README.md).
For your contribution, [bring your own workflow](#bring-your-own-workflow), or
take a ContribFest Issue below if you didn't bring one.

<a id="choose-a-contribfest-issue"></a>

## ContribFest Issues

Check the [approved recipe tasks](https://github.com/cadence-workflow/cadence-ai-samples/issues?q=is%3Aissue%20is%3Aopen%20milestone%3A%22KubeCon%20NA%202026%20ContribFest%2C%202026-11-11%22%20label%3Aevent%3Acontribfest%20label%3Astatus%3Aready%20no%3Aassignee%20label%3Aarea%3Arecipes%20label%3Aformat%3Ahuman-first) for work that fits your setup.
A task is available while it is open, unassigned and labeled `status:ready`,
with no existing claim or active implementation PR. If the pool is empty,
ask a facilitator about another approved task or explore a sample while you wait.

For alternatives such as Java profile CI, browse
[all approved event tasks](https://github.com/cadence-workflow/cadence-ai-samples/issues?q=is%3Aissue%20is%3Aopen%20milestone%3A%22KubeCon%20NA%202026%20ContribFest%2C%202026-11-11%22%20label%3Aevent%3Acontribfest%20label%3Astatus%3Aready%20no%3Aassignee).
Each Issue states its language, dependencies and checks. The same claim rules
apply to every task.

To request a task, comment on its Issue:

> I'd like to take this. GitHub: @yourname. Proposed branch:
> `issue-N-short-slug`.

A facilitator records your ownership and changes the label to
`status:in-progress`. You can then create the branch in your fork. You do not
need label or assignment permissions. Follow the
[claim guide](../../CONTRIBFEST.md#2-claim-one-issue) for sign-off and review.

### Recipe tasks

Browse the [full attendee issue pool in Get started](README.md#issue-pool).
It lists the prepared tasks for every recipe and links to each Issue's
requirements. Check the approved tasks above for current availability before
requesting a claim.

## Extend the exercise

Each scenario in the Incident Triage recipe's
[`run_exercise.py`](../../recipes/incident-triage/python/bare/exercises/retry_policy_live/run_exercise.py)
pairs a prepared failure with checks of the Workflow's behavior. To add one,
for example an approval that never arrives
([durable timers](https://cadenceworkflow.io/docs/concepts/timers/)) or a write
whose reply is lost
([Activity execution](https://cadenceworkflow.io/docs/concepts/activities/)), copy the `rate-limit` scenario, change the
failure, and write its checks. Open an Issue describing it before the pull
request.

## Bring your own workflow

Start from [#118](https://github.com/cadence-workflow/cadence-ai-samples/issues/118). Open your own Issue titled
`<Example> Make <your agent> durable`, so it can be claimed, reviewed and
continued by someone else.

Bring a small agent or script that's safe to share and makes at least one
model or tool call. Work through as many depths as you have time for:

1. **Wrap.** Start a Workflow that runs your agent in one Activity and returns
   its output. Run it and find it in Cadence Web.
2. **Break it.** Pick the call most likely to fail (a rate-limited API, a slow
   model, a flaky tool). Make it fail on purpose and watch what happens.
3. **Make it durable.** Move that call into its own Activity with a retry
   policy and a timeout. Read the retry guides for
   [Python](https://cadenceworkflow.io/docs/python-client/retries/) or
   [Go](https://cadenceworkflow.io/docs/go-client/retries/), then mark permanent
   errors as non-retryable.
4. **Steer (stretch).** Add a [Signal](https://cadenceworkflow.io/docs/python-client/signals/) so a person approves before the agent
   acts.

The [Incident Triage recipe](../../recipes/incident-triage/python/bare/workflow.py)
shows every step: model and tool calls as Activities, retry policies,
non-retryable errors and review Signals. Copy from it.

If you don't finish, write down the commands, what works and the next step,
and open an Issue so someone can continue.

## Explore and file what you find

[#119](https://github.com/cadence-workflow/cadence-ai-samples/issues/119): run Incident Triage in mock mode, stop the worker mid-run,
send Signals, run two at once. Open a `<Finding>` Issue for anything that
surprised you. No model needed.

**If you'd rather click than type (optional):** from the repository root, run
`./scripts/explore.sh`. Choose Incident Triage, python, and mock mode (this
recipe has no framework selector).

1. **Refresh and check services**, then copy the Worker and start commands into
   separate terminals and run them.
2. **Resolve latest run** to find your Run.
3. Follow the [recipe walkthrough](../../recipes/incident-triage/README.md) to
   send guidance, a revision and approval: enter the field, select **Send …**,
   then **Confirm …**.
4. **Open history** to see the recorded Activities and Signals in Cadence Web.

The Explorer is a helper for navigation and controls; the terminal path above
works on its own. More details are in [setup](setup.md#optional-explore-the-samples-in-your-browser).

## Leave a useful result

Follow the [finish or hand off instructions](../../CONTRIBFEST.md#4-finish-or-hand-off).
