# Cadence AI Samples

`cadence-ai-samples` is a collection of independently runnable AI workflow examples built with [Cadence](https://cadenceworkflow.io/) durable workflow orchestration.

## Explore the samples

```bash
./scripts/explore.sh
```

The Local Samples Explorer opens the current checkout on a loopback address. It
needs Python 3.10+ with venv support. On first launch, it creates
`tools/explorer/.venv` and downloads the pinned PyYAML dependency; later launches
reuse that environment. The first launch therefore needs package-download
access.

Browsing does not require Cadence, Cadence Web, a recipe language runtime,
recipe dependencies, provider credentials, Laya, or Ollama. The launcher and
generated commands use Bash. macOS supports the optional Terminal handoff;
native Windows command tabs and Terminal handoff remain deferred.

Use this short path in the Explorer:

1. Select a recipe and implementation.
2. Select mock mode when available.
3. Use **Refresh and check services** for advisory checks.
4. After installing the selected language runtime, use
   **Prepare recipe dependencies**.
5. Copy the Worker and Workflow commands, or use **Open in Terminal** on macOS.

Preparation invokes only the selected implementation's checked-in
`run.sh setup`. The Explorer does not install language runtimes or start
Cadence, Cadence Web, AI services, Workers, or Workflows. See
[Explorer usage and validation](tools/explorer/README.md) for the complete
selection, preparation, warm-up, and run-link behavior.

## Run a recipe

The flagship [Ticket Routing](recipes/ticket-routing/README.md) recipe provides
a visual mock path with synthetic tickets and no provider key. Its runbook
contains the commands for starting Cadence, preparing the implementation, and
running the Worker and Workflow.

Browse the current recipe runbooks directly:

- [Ticket Routing](recipes/ticket-routing/README.md), the flagship mock path
- [Human in the Loop](recipes/human-in-the-loop/README.md)
- [Recurring AI Watch](recipes/recurring-ai-watch/README.md)
- [Agent Handoffs](recipes/agent-handoffs/README.md)
- [Auto Research](recipes/auto-research/README.md)

Running a selected recipe has additional requirements:

- Go implementations currently declare Go 1.23.
- Supported Python recipe profiles use Python 3.12 or 3.13, not Python 3.14.
  The selected implementation's `pyproject.toml` is authoritative.
- Java profiles use a Java 17 toolchain and their checked-in Gradle wrapper.
- The baseline pull-request workflow uses Python 3.12.

Recipes use the `cadence-ai-samples` domain and need a reachable local Cadence
server. Cadence endpoint protocols and ports differ by SDK, so follow the
selected recipe README instead of assuming one frontend port. Cadence Web is
optional for browsing, but required for walkthroughs that use Queries, Signals,
exact-run links, or Custom Workflow Controls.

<a id="local-ai-services"></a>

## Optional local AI services

A recipe may need neither, one, or both local AI services:

- [Laya](https://github.com/NandhaKishorM/laya) provides local classification
  when an implementation selects
  [`laya-local`](classifiers.yaml).
- [Ollama](https://ollama.com/) provides local generative inference when an
  implementation selects an Ollama entry such as
  [`llama3.2-local`](models.yaml).

The selected recipe README and Explorer checks are authoritative. Provider keys
are needed only for an explicitly selected remote live path.

### Install Laya

Clone Laya and start its HTTP server with Docker Compose:

```bash
git clone https://github.com/NandhaKishorM/laya.git
cd laya
LAYA_PORT=8008 docker compose -f compose.yaml -f compose.http.yaml up -d --build laya-serve
curl -s http://localhost:8008/health
```

Port 8008 matches `laya-local` in [classifiers.yaml](classifiers.yaml). Laya
downloads its model weights on the first classification request and stores them
for reuse.

### Install Ollama and llama3.2

On macOS, install Ollama with Homebrew, start its service, and download the model:

```bash
brew install ollama
brew services start ollama
ollama pull llama3.2
ollama run llama3.2 "Say ok."
```

Ollama listens on `localhost:11434`, which matches `llama3.2-local` in
[models.yaml](models.yaml). After installation, the Explorer can explicitly
warm selected local services without starting or downloading them.

## Why Cadence for AI workflows

Cadence preserves workflow state and provides durable retries, timers, and
failure recovery for long-running AI workflows. External AI calls run in
Cadence Activities, never directly in Workflow code. Workflow logic produces
the same decisions during replay, even after a process restart or dependency
failure.

## Choosing the right tool

Recipes use the simplest approach that fits the problem:

- **Deterministic code** for reliable rules and transformations.
- **Specialized decision models** for focused classification, scoring, ranking,
  or prediction.
- **Generative models** for creating, extracting, transforming, or summarizing
  flexible content.
- **AI agents** for tasks that need several planning steps or tool calls.

## Recipe philosophy

Every recipe runs on its own and is easy to copy into an existing application.
Recipes do not depend on one another or on a shared integration framework.
Providers and agent frameworks should be replaceable when practical without
adding abstraction layers that obscure the solution. This follows the
simplicity of Cadence's redesigned
[Go samples](https://github.com/cadence-workflow/cadence-samples/tree/master/new_samples).

## Contributing

Contributions start with a concrete problem or use case. Follow the
[first-time contributor path](CONTRIBUTING.md#first-time-contributor-path),
[plan before building](WORKFLOW.md#plan-before-building), and run the
[offline pull-request checks](CONTRIBUTING.md#offline-pull-request-checks).
Coding agents start with the [coding-agent contract](AGENTS.md).

## License

This repository is licensed under the [Apache License 2.0](LICENSE).
