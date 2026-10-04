# Cadence AI Samples

`cadence-ai-samples` is a collection of AI workflow examples built with [Cadence](https://cadenceworkflow.io/) durable workflow orchestration. The examples address specific problems instead of demonstrating individual library features.

Cadence preserves workflow state and provides durable retries, timers, and failure recovery for long-running AI workflows. External AI calls must run in Cadence Activities, never directly in Workflow code. Workflow logic produces the same decisions during replay, even after a process restart or dependency failure.

## Choosing the right tool

Recipes should use the simplest approach that fits the problem:

- **Deterministic code** for rules and transformations that can be expressed reliably in ordinary code.
- **Specialized decision models** for focused classification, scoring, ranking, or prediction tasks.
- **Generative models** when the application needs to create, extract, transform, or summarize flexible content.
- **AI agents** when a task needs several planning steps or tool calls.



## Recipe philosophy

Every recipe runs on its own and is easy to copy into an existing application. Recipes do not depend on one another or on a shared integration framework. Prefer a simple layout when it keeps the example clear. This often means one file for the workflow, Activities, and business logic, plus one worker entry point.

Recipes may use Go, Python, or an occasional minimal combination of both. AI providers and agent frameworks should be replaceable when practical, without adding abstraction layers that obscure the solution. This approach follows the simplicity of Cadence's redesigned [Go samples](https://github.com/cadence-workflow/cadence-samples/tree/master/new_samples).

## Finding recipes

All samples use the Cadence domain `cadence-ai-samples`.

Browse recipe directories by problem or use case. The first recipe, [StreamWave ticket routing](recipes/ticket-routing/), demonstrates typed AI-assisted classification with durable Cadence routing. Each recipe README explains how to run it, what to expect, and how it handles failures.

### Local Samples Explorer

Run `./scripts/explore.sh` to open a localhost explorer of this checkout. Choose a
sample, language, framework when applicable, mode, and catalog IDs to get
matching Linux Worker/client commands. Refresh reads the current recipe files
and catalogs, including local edits. Recorded live evidence and local-service
health-check/warm-up commands appear where applicable.

The launcher needs Python 3.10+ with venv support and installs its one small
dependency on first use. It previews commands; run them in your terminal.
See [explorer usage and validation](tools/explorer/README.md).

## Local AI services

Some recipes can run without API keys by using [Laya](https://github.com/NandhaKishorM/laya) for local classification and [Ollama](https://ollama.com/) for local models. The [Recurring AI Watch](recipes/recurring-ai-watch/) recipe selects them with `--classifier-id laya-local` and `--model-id llama3.2-local`.

### Install Laya

Clone Laya and start its HTTP server with Docker Compose:

```bash
git clone https://github.com/NandhaKishorM/laya.git
cd laya
LAYA_PORT=8008 docker compose -f compose.yaml -f compose.http.yaml up -d --build laya-serve
curl -s http://localhost:8008/health
```

Port 8008 matches `laya-local` in `[classifiers.yaml](classifiers.yaml)`. Laya defaults to port 8000, which the local Cadence server already publishes. Laya downloads its model weights on the first classification request and stores them in a named Docker volume for reuse. The first download can take several minutes. You can set `HF_TOKEN` to get higher Hugging Face download rate limits.

### Install Ollama and llama3.2

On macOS, install Ollama with Homebrew, start its service, and download the model:

```bash
brew install ollama
brew services start ollama
ollama pull llama3.2
ollama run llama3.2 "Say ok."
```

Ollama listens on `localhost:11434`, which matches `llama3.2-local` in `[models.yaml](models.yaml)`. Run `ollama ps` to see whether the model is loaded.

### Warm up the local services

AI Activities in Recurring AI Watch have a 30-second execution budget. A cold Laya or Ollama request can take longer, so warm up both services before starting a Workflow.

1. Check that Laya is ready:
  ```bash
   curl -s http://localhost:8008/health
  ```
2. Send a Laya classification request. The 10-minute client timeout allows an initial model download to finish:
  ```bash
   curl -sS --max-time 600 -X POST http://localhost:8008/v1/systemone \
     -H 'Content-Type: application/json' \
     -d '{"state":"Version: 0.0.1\nRelease notes: warmup","model":"convaiinnovations/laya","questions":{"relevant":{"type":"choice","instructions":"Is this a warmup request?","criteria":{"yes":"It is a warmup.","no":"It is not a warmup."}}}}'
  ```
   The response should contain `"answers"`. A second request should return quickly.
3. Load llama3.2 into Ollama and keep it loaded for 30 minutes:
  ```bash
   curl -sS --max-time 300 http://localhost:11434/api/generate \
     -d '{"model":"llama3.2:latest","prompt":"Say ok.","stream":false,"keep_alive":"30m"}'
  ```



## Contributing

Contributions are welcome. Start with a concrete real-world problem, write an implementation specification, and keep the resulting recipe focused and portable. See [CONTRIBUTING.md](CONTRIBUTING.md) and the reusable files in `[templates/recipe](templates/recipe/)`.

Create a Python recipe starter with `./scripts/new-recipe.sh your-recipe-name`.
