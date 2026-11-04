# Set up your laptop

Download the dependencies before the session so you can spend the time working
with the model and Cadence. The selected recipe's README is authoritative for
versions, services and commands. Prepare only what your task needs.

[Clone your fork](../../CONTRIBUTING.md#first-time-contributor-path) before
following the commands below.

<a id="optional-explore-the-samples-in-your-browser"></a>

## Explore the samples in your browser

The [Samples Explorer](../../tools/explorer/README.md) lets you browse recipes,
prepare dependencies, generate commands and open the running Workflow in
Cadence Web. You can use it during ContribFest or try the samples afterward.

Browsing needs Python 3.10+ with venv support. The first launch installs PyYAML
and needs internet access. You do not need Docker, Cadence, a model or provider
credentials just to browse. Each recipe has its own requirements to run.

From the repository root:

```bash
./scripts/explore.sh
```

The launcher opens `http://127.0.0.1:8765/`. If that port is in use, launch with
`./scripts/explore.sh --port 0` to choose an available port. Stop the Explorer
with Ctrl+C.

1. **Choose an example.** Select the recipe, language and framework. Choose
   mock mode for scripted responses or live mode to call the selected model.
   Start with Incident Triage, Python, mock to explore without downloading a
   model. Incident Triage has no framework selector. Preparing this implementation needs Python 3.12 or
   3.13; browsing the Explorer itself needs only Python 3.10+.
2. **Prepare the recipe.** Install the language runtime named in its README,
   then click **Prepare recipe dependencies**. This runs the recipe's
   `./run.sh setup`; the same command is available to copy and run yourself.
3. **Check services.** Start the services the recipe needs, then click
   **Refresh and check services**. In local live mode,
   **Warm up selected local services** sends a small request to the selected
   Ollama or Laya service. It does not start services or download models.
4. **Start the Worker and Workflow.** Copy the generated commands into
   separate terminals. On macOS, **Open in Terminal** pastes the command into
   a new Terminal window. Review it and press Enter to run it. If macOS blocks
   the paste, use Cmd+V or copy the command manually.
5. **Inspect your Run.** Click **Resolve latest run**, then **Open history**
   or **Open queries**. Queries need a running Worker.
   Resolve again after Continue-As-New to follow the new Run.
6. **Try a Signal.** Where the recipe provides controls, enter the requested
   fields, select **Send …**, then **Confirm …** when prompted.
   Acceptance means Cadence received the request; check a Query or history
   to see what the Workflow did. For Ticket Routing, use the
   [Acknowledge Ticket control in Cadence Web](../../recipes/ticket-routing/README.md#cadence-web-cwc).

On a shared Cadence server, use a task list and Workflow ID unique to you in
both generated commands. For recipes with a
generated Workflow ID, the Explorer's **Resolve** and **Signal** controls follow
that ID; changing a copied command does not retarget them. For your own Workflow
ID, use Cadence Web or the recipe CLI. Recipes that ask you to enter an exact
Workflow ID can follow the ID you enter.

The [Explorer documentation](../../tools/explorer/README.md) covers service
checks, preparation, local warm-up and Run controls. Its checks are advice:
a reachable service does not prove that your domain or Worker is ready. You
can also run every exercise directly from the terminal and Cadence Web.

### Troubleshoot local connections

| Symptom | Next step |
| --- | --- |
| The launcher asks for Python 3.10+ | Install Python 3.10+ with venv support, then reopen your terminal and retry. |
| Cadence or Cadence Web is unreachable | If you use Docker, start it and check `docker ps` for the containers and published ports. Follow the recipe's setup instructions, then refresh the checks. |
| Cadence is reachable but nothing runs | Check the domain and Worker task list. The connection check does not verify that the Worker is ready. |
| Ollama warm-up fails | Check `ollama list` for the selected model. Download a missing model with its documented `ollama pull` command. Warm-up cannot download it for you. |
| A Query times out | Start the matching Worker and check the domain and Workflow ID. |

## Prepare the live Incident Triage exercise

The opening Incident Triage exercise needs Python 3.12
or 3.13, local Cadence in domain `cadence-ai-samples` (see the
[Cadence requirements](../../recipes/ticket-routing/README.md#requirements)),
and [Ollama](https://ollama.com/download) with `qwen2.5:7b`. The model is a
4.7 GB download and used 5.1 GB of memory when loaded in our test:

```bash
ollama pull qwen2.5:7b
```

Tasks marked **Python only** in the [shortlist](shortlist.md) can be worked on
without Ollama. A coding agent is optional and is separate from the model used
by the running example.

## Get the code and check setup

Fork and clone [cadence-ai-samples](https://github.com/cadence-workflow/cadence-ai-samples)
using the [contributor instructions](../../CONTRIBUTING.md#first-time-contributor-path).
From your checkout's root:

```bash
cd recipes/incident-triage/python/bare
./run.sh setup
.venv/bin/python -m unittest discover -s tests -v
```

The tests use fixtures. Passing them checks the code setup; it does not mean
you have called a real model. Keep installation and model download separate
from the time needed to run an already prepared example.

## Check that a real model answers

With Cadence and Ollama running, from the same recipe directory:

```bash
.venv/bin/python exercises/retry_policy_live/run_exercise.py --scenario baseline
```

The run is working when you see:

- `MODEL RESPONSE` lines with the lookups the model chose, each after a
  `SPEND` line with the input and output tokens the model reported;
- the Workflow ID, Run ID and a Cadence Web link you can open;
- a draft report waiting for review. Type `reject` to finish.

`SETUP FAILED` names the missing piece. A model error prints
`MODEL CALL FAILED` and the run ends `FAILED`; there is no scripted answer.
Keep that output and show a helper.

`--model-id` picks another entry from the repository's
[`models.yaml`](../../models.yaml). `llama3.2-local` is smaller but often
drafts the wrong cause. Paid providers need `MODEL_AI_KEY`.

## The introductory retry exercise

Read [Activity retries](https://cadenceworkflow.io/docs/python-client/retries/),
then follow the [exercise README](../../recipes/incident-triage/python/bare/exercises/retry_policy_live/README.md).
You edit one file to make a temporary 429 recover and a permanent 404 stop,
then see the repeated-call guard and how `revise` and `guidance` differ.

## If setup or internet access fails

Tell a helper which command failed and keep its output. You can pair on a
working laptop if useful. Not every laptop can run the model.

A source archive does not contain installed Python packages, container images
or model weights. Download them before travel. Before relying on disconnected
work, try the whole selected task with networking disabled and localhost still
available. Claim upstream work while online; after reconnecting, confirm
ownership before pushing. A repeatable walkthrough needs no Issue claim.

Save commands, IDs, model receipts, changed files and the next step. Stop only
services and Workers you started. Use the [task list](shortlist.md) to choose
your next contribution.
