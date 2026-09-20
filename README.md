# Cadence AI Samples

`cadence-ai-samples` is a community-oriented collection of practical AI solutions built with [Cadence](https://cadenceworkflow.io/) durable workflow orchestration. The project focuses on reliable, cost-efficient applications that solve real-world problems—not demonstrations organized around individual SDK features.

Cadence makes long-running AI workflows easier to operate by preserving workflow state and providing durable retries, timers, and failure recovery. External AI calls must execute in Cadence Activities, never directly in Workflow code; workflow logic coordinates them predictably even when processes restart or dependencies fail.

## Choosing the right tool

Recipes should use the simplest approach that fits the problem:

- **Deterministic code** for rules and transformations that can be expressed reliably in ordinary code.
- **Specialized decision models** for focused classification, scoring, ranking, or prediction tasks.
- **Generative models** when the application needs to create, extract, transform, or summarize flexible content.
- **AI agents** when a task genuinely benefits from iterative planning, tool use, and decisions across multiple steps.

## Recipe philosophy

Every recipe is intended to be self-contained, independently runnable, and easy to copy into an existing application. Recipes do not depend on one another or on a shared integration framework. A straightforward layout—often one workflow file for the workflow, Activities, and business logic, plus one worker entry point—is preferred when it keeps the example clear.

Recipes may use Go, Python, or an occasional minimal combination of both. AI providers and agent frameworks should be replaceable when practical, without adding abstraction layers that obscure the solution. This approach follows the simplicity of Cadence's redesigned [Go samples](https://github.com/cadence-workflow/cadence-samples/tree/master/new_samples).

## Finding recipes

Browse recipe directories by problem or use case. The first recipe, [StreamWave ticket routing](recipes/ticket-routing/), demonstrates typed AI-assisted classification with durable Cadence routing. Each recipe README describes its requirements, setup, execution, expected results, architecture, and reliability choices.

## Contributing

Contributions are welcome. Start with a concrete real-world problem, write an implementation specification, and keep the resulting recipe focused and portable. See [CONTRIBUTING.md](CONTRIBUTING.md) and the reusable files in [`templates/recipe`](templates/recipe/).
