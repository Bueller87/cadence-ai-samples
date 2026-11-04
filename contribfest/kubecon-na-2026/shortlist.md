# Find something to work on

Start with the [retry-policy exercise](../../recipes/incident-triage/python/bare/exercises/retry_policy_live/README.md).
For your contribution, [bring your own workflow](#bring-your-own-workflow), or
take a ContribFest Issue below if you didn't bring one.

Comment on the Issue you want to take. A helper will record your claim and
update its label and assignment. You do not need label or assignment permissions.

## ContribFest Issues

Find all issues using [this filter](https://github.com/cadence-workflow/cadence-ai-samples/issues?q=is%3Aissue+state%3Aopen+label%3Aevent%3Acontribfest+no%3Aassignee)

| Issue | What you'll do | Cadence idea | Level | Needs |
| --- | --- | --- | --- | --- |
| [#110](https://github.com/cadence-workflow/cadence-ai-samples/issues/110) | Prove `revise` reuses evidence and `guidance` investigates again | Signals, Queries, Workflow tests | low | Python only |
| [#112](https://github.com/cadence-workflow/cadence-ai-samples/issues/112) | Make the Incident Triage clean-copy check pass | Self-contained samples | low | Python only |
| [#121](https://github.com/cadence-workflow/cadence-ai-samples/issues/121) | Prove a lost reply doesn't save the conclusion twice | Idempotent writes | low | Python only |
| [#111](https://github.com/cadence-workflow/cadence-ai-samples/issues/111) | Show a model call recovering from a rate limit | Activity retry policy | mid | Cadence, Ollama, `qwen2.5:7b` |
| [#120](https://github.com/cadence-workflow/cadence-ai-samples/issues/120) | Let an unanswered approval expire | Durable timers | mid | Cadence, Ollama, `qwen2.5:7b` |
| [#109](https://github.com/cadence-workflow/cadence-ai-samples/issues/109) | Help a small local model name the root cause | Human review of agent output | mid | Cadence, Ollama, `llama3.2` and `qwen2.5:7b` |

Each Issue explains the problem, shows the command that reproduces it, names the
file to change and says how to check the fix. Check the Issue's current status
and claim instructions before starting.

To claim one:

1. Check it is open, unassigned and labeled `status:ready`, with no linked pull request.
2. Comment on the Issue with your GitHub username and your branch name, `issue-N-short-slug`.
3. Wait for the claim coordinator to assign you and change the label to `status:in-progress`.
4. Create the branch in your fork, commit with `git commit -s`, and open a pull request against `main`.

The full process is in [CONTRIBFEST.md](../../CONTRIBFEST.md#2-claim-one-issue).
The [ContribFest milestone](https://github.com/cadence-workflow/cadence-ai-samples/milestone/2)
has the current list.

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
