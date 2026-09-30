# Project history

This repository is the proof of concept behind
[cadence-workflow/cadence-ai-samples](https://github.com/cadence-workflow/cadence-ai-samples).
The official repository starts from a single signed commit, so the history of
how the samples came together stays here: 48 commits and 9 merged pull
requests, from September 20 to 29, 2026.

## The spark

Two things came together. TypeSafe released Jev, a decision model that returns
typed answers with probabilities. At the same time, we were preparing
Contribfest issues to bring new contributors into Cadence. See
[the-spark.md](1.%20the-spark.md).

The goal was to get started fast and let Cadence shine:

- Make the first five minutes about Cadence. Start from a template and pick a
  model from YAML instead of writing integration code.
- Not all AI inference needs to be an LLM. Classify first, at a fraction of a
  cent, and generate only when the classifier says it matters.
- Agentic AI workflows aren't just Python anymore, so the repository holds both
  Go and Python samples.

## Ticket routing in Go

The first day produced the scaffold and StreamWave ticket routing: live Jev
classification, durable acknowledgment with a Child Workflow, and a synthetic
ticket dataset.

An early batch run looked slow, about 1.43 Workflows per second. A controlled
experiment did not reproduce it. The local server completed about 31 to 35
Workflows per second at concurrency 25, and updating the server image with
`info` logging added about 12%.
[PR #1](https://github.com/Bueller87/cadence-ai-samples/pull/1) added bounded
mock batch runs, and
[PR #2](https://github.com/Bueller87/cadence-ai-samples/pull/2) added live Jev
batches. All 10 live Workflows completed, with an average Jev latency of 168 ms.

## Agent frameworks on the Python SDK

Before building a Python sample, we needed to know whether agent frameworks
run on the Cadence Python SDK.
[PR #3](https://github.com/Bueller87/cadence-ai-samples/pull/3) tested Google
ADK and OpenAI Agents with Gemini, OpenAI, and local Ollama, including replay
after a Worker restart.

It found that native OpenAI Activities failed to serialize on SDK v0.4.0, and
that upstream Cadence PR #176 fixed it. That is why the Python recipes pin the
SDK to that commit.

## Inference as Config

[PR #4](https://github.com/Bueller87/cadence-ai-samples/pull/4) added the root
YAML catalogs in 23 lines. The first Python sample built on them was over
engineered, 1,773 lines in a single checkpoint commit, and was trimmed before
review.

[PR #5](https://github.com/Bueller87/cadence-ai-samples/pull/5) added
Recurring AI Watch. It runs mock-first, lets Jev decide whether a release
matters, writes a Gemini report only when it does, and uses durable timers and
Continue-As-New. After that:

- [PR #6](https://github.com/Bueller87/cadence-ai-samples/pull/6) added four
  open-source classifier entries and made the instructions Bash-first.
- [PR #7](https://github.com/Bueller87/cadence-ai-samples/pull/7) proved all
  six framework and model combinations live.
- [PR #8](https://github.com/Bueller87/cadence-ai-samples/pull/8) added the
  `new-recipe.sh` template generator.
- [PR #9](https://github.com/Bueller87/cadence-ai-samples/pull/9) added local
  Laya, which made a fully local run possible with no API keys and no cost.

## Lessons before the move

After the [demo](2.%20demo.md), the Go sample started reading its classifier
from `classifiers.yaml` instead of hard-coding Jev.

The six-combination matrix proved the plumbing could switch agent frameworks,
but a real application picks one. Recurring AI Watch split into `google-adk/`
and `openai-agents/` folders, and `agents.yaml` was removed. Models and
classifiers stay in YAML because swapping them needs no code changes. See
[takeaways.md](3.%20takeaways.md).

Both folders passed local live runs with Laya and Llama 3.2. Then 67 files
moved to the official repository. The compatibility spike and these docs
stayed here.

## What comes next

- **More templates.** The generator creates one Python starter with Google ADK.
  The plan is five templates chosen by a flag: `python/bare`, `go/bare`,
  `python/google-adk`, `python/openai-agents`, and `go/systemone`.
- **More model providers, like Claude.** A YAML entry alone is not enough. Each
  framework folder needs a small provider branch in `inference.py` and a
  matching rule in `live.py`.
- **A key-free first run.** Open-weight models should be the Contribfest
  default. The recipes still default to `gemini-flash-lite` and `jev-default`.
- **More runnable classifiers.** `kev-local`, `von-local`, and `reflex-local`
  are catalog entries only.
- **An unpinned SDK.** Move off the PR #176 commit once the fix ships in a
  Python SDK release.
- **Open design problems.** Worker and Workflow config binding, retry budgets
  across Watch cycles, and idempotent provider calls are unsolved. See
  [unsolved.md](unsolved.md), [sample-issues.md](sample-issues.md), and the
  [Contribfest issue list](contrifest-issues.md).
