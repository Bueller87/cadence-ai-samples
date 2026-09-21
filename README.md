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

Browse recipe directories by problem or use case. The first recipe, [StreamWave ticket routing](recipes/ticket-routing/), demonstrates typed AI-assisted classification with durable Cadence routing. Each recipe README explains how to run it, what to expect, and how it handles failures.

## Contributing

Contributions are welcome. Start with a concrete real-world problem, write an implementation specification, and keep the resulting recipe focused and portable. See [CONTRIBUTING.md](CONTRIBUTING.md) and the reusable files in [`templates/recipe`](templates/recipe/).
