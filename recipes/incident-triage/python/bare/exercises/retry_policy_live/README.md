# Incident Triage: investigate, then fix one retry policy

An AI agent investigates a synthetic checkout failure with a real model. You
watch it work in Cadence Web, then make one change: give the gateway log
lookup a retry policy so a temporary error recovers and a permanent one stops.

The incident, logs and API failures are synthetic. The model calls are real.

## Prepare before the session

You need Python 3.12 or 3.13, Docker, and Ollama.

1. Start local Cadence and register domain `cadence-ai-samples` using the
   [Cadence requirements](../../../../../ticket-routing/README.md#requirements).
   Cadence Web is at <http://localhost:8088>.
2. Install [Ollama](https://ollama.com/download), then pull the recommended
   model. It is a 4.7 GB download and used 5.1 GB of memory when loaded in our
   test.

   ```shell
   ollama pull qwen2.5:7b
   ```

3. From `recipes/incident-triage/python/bare`:

   ```shell
   ./run.sh setup
   .venv/bin/python -m unittest discover -s tests
   ```

Do these steps while you are online. Model weights, Python packages and
container images are not in the repository.

## 1. Watch the agent investigate

```shell
.venv/bin/python exercises/retry_policy_live/run_exercise.py --scenario baseline
```

The runner prints the selected model, then the Workflow ID, Run ID and a
Cadence Web link for this run. Open the link. Each model call prints a `SPEND`
line with the input and output tokens the model reported, then the action it
chose. When the agent drafts a report, the runner prints it and waits for
review. Type `reject` to finish without saving a report.

If something is not set up, the runner stops with `SETUP FAILED` and the fix.
A model error during planning or revision prints `MODEL CALL FAILED` for
every attempt, and the run ends `FAILED` in Cadence. There is no scripted
fallback.

## 2. Fix the retry policy

**Predict the result:** the dependency graph lookup succeeds, then the gateway
log lookup fails once. Which call should run again, and which completed result
should the investigation keep?

```shell
.venv/bin/python exercises/retry_policy_live/run_exercise.py --scenario rate-limit
```

The gateway log lookup gets one HTTP 429. The starter policy has no retries,
so the run stops at `gateway_logs_unavailable` before the agent plans, with
zero model calls. Find the failed Activity in Cadence Web.

Edit only [`exercise_options.py`](exercise_options.py). Give `SCAN_ACTIVITY` a
bounded `retry_policy`:

- The 429 says `Retry-After: 1`, so wait at least one second first.
- Use a finite number of attempts.
- Do not retry errors another attempt cannot fix. See `NonRetryableHttpError`
  in [`activities.py`](../../activities.py) and `QUICK_ACTIVITY` in
  [`workflow.py`](../../workflow.py).

Reference: [Cadence Activity retries](https://cadenceworkflow.io/docs/concepts/activities#retries).

Verify both outcomes:

```shell
.venv/bin/python exercises/retry_policy_live/run_exercise.py --scenario rate-limit
.venv/bin/python exercises/retry_policy_live/run_exercise.py --scenario missing-resource
```

`rate-limit` passes when the gateway attempts are `(0, 429)` then `(1, 200)`,
the dependency graph ran once, and the model reached review. `missing-resource`
passes when a 404 stops after `(0, 404)` with no planning. Three 404 attempts
mean the permanent error is being retried.

**Explain your change:** compare the two outcomes in history. A retry policy
can recover a temporary failure; it cannot repair an invalid request. Use this
pattern when your own agent calls a fallible API, without repeating unrelated
completed work.

## 3. See the repeated-call guard

```shell
.venv/bin/python exercises/retry_policy_live/run_exercise.py --scenario repeat-guard
```

Normally the agent never repeats a lookup: with Ollama, the request schema only
offers services nobody has checked yet. This scenario deliberately bypasses
that to show the Workflow's own guardrail. The model chooses the first lookup,
then the runner injects two copies of it, marked `INJECTED`. With the default
limit of 2, the second identical call runs and the third is blocked. The
Workflow then asks the model for a draft from the evidence it has. The guard is
in `run_agent` in [`workflow.py`](../../workflow.py).

## 4. Review through the evidence

```shell
.venv/bin/python exercises/retry_policy_live/run_exercise.py --scenario review
```

The runner sends two Signals and prints what each one changed:

- `revise` rewrites the draft from evidence the agent already collected. No
  new lookups run.
- `guidance` restarts the investigation. The agent makes new lookups, drafts
  again, and the earlier revision request is applied to the new draft.

The Workflow code defines both. In [`workflow.py`](../../workflow.py), the
`AWAITING_GUIDANCE` loop calls the revise Activity with the existing hops.
Guidance clears `calls` and `hops` and runs `run_agent` again.

## What to take into your own agent

Choose one model or tool call that can fail. Give it an Activity boundary,
define which failures deserve another attempt and decide when to stop. Then
cause that failure and inspect the attempts. If the call writes externally,
make repeated writes safe in the application too.

For human review, distinguish asking for a better explanation from asking for
more evidence. In this exercise, `revise` and `guidance` make that distinction
visible in lookup counts. Cadence delivers the Signals; the Workflow defines
their meaning.

## Resources

- [Activities](https://cadenceworkflow.io/docs/concepts/activities/): identify
  the boundary between coordinating work and making an external call.
- [Python retry policies](https://cadenceworkflow.io/docs/python-client/retries/):
  compare bounded attempts with errors another attempt cannot fix.
- [Signals](https://cadenceworkflow.io/docs/python-client/signals/): connect
  human input to the Workflow's existing review states.

## Choose a model

`--model-id` selects an entry in the repository-root
[`models.yaml`](../../../../../../models.yaml). The default is `qwen2.5-local`.

| `--model-id` | Needs | Notes |
| --- | --- | --- |
| `qwen2.5-local` | Ollama, `qwen2.5:7b` | Recommended |
| `llama3.2-local` | Ollama, `llama3.2:latest` | Smaller and faster; usually names the symptom, not the root cause |
| `openai-nano` | `MODEL_AI_KEY` | Paid API; not checked with this runner |
| `gemini-flash-lite` | `MODEL_AI_KEY` | Paid API; not checked with this runner |

## Options

| Option | Default |
| --- | --- |
| `--scenario` | `baseline`; also `rate-limit`, `missing-resource`, `repeat-guard`, `review`, `all` |
| `--options` | `exercise_options.py` |
| `--cleanup` | `prompt` asks before rejecting; `reject` does not ask |
| `--evidence` | a new temporary directory |
| `--max-steps` | the recipe's budget (6 planning steps) |
| `--target`, `--domain`, `--web-url`, `--cluster` | `localhost:7833`, `cadence-ai-samples`, `http://localhost:8088`, `cluster0` |

Each scenario writes `result.json`, the model decisions, token receipts, tool
attempts and the Cadence history to the evidence directory. Token counts are
what the provider reported. A missing count stays unavailable.
