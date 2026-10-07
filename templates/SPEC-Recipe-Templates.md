# Recipe template system specification

Status: future design for prioritization. This document records the intended
template model; it does not authorize implementation, sample ports, or Explorer
generalization.

## Purpose

Generate a repository-native starting point for a Cadence recipe from a small set
of composable choices. Generated recipes remain independently runnable and easy
to copy into another application. Code duplication between recipes and template
profiles is intentional when it preserves that independence.

Ported samples start from a generated recipe and move the source sample's business
intent into this repository's structure and conventions. They do not preserve an
upstream directory layout or command interface merely because it already exists.

## Vocabulary

- **Implementation profile:** the selected language, classifier integration, and
  agent integration that determine the generated scaffold.
- **Classifier integration:** an optional specialized decision-model boundary.
  The initial integration is SystemOne, with concrete providers selected through
  the repository classifier catalog.
- **Agent integration:** an optional agent framework integrated with Cadence.
  Google ADK and OpenAI Agents currently depend on Cadence Python SDK support.
- **Bare:** no prescribed agent framework. Bare does not mean that a recipe can
  never call AI; direct model Activities may still be added when the business
  problem needs them.

Runtime model and provider selection is recipe configuration through catalogs. It
is not a template-generation dimension.

## Composable dimensions

### Cadence language

Initial design languages:

- Go
- Java
- Python

The model must admit another language when Cadence has a suitable client and the
repository adds and validates a template implementation for it.

### Classifier integration

- `none`
- `systemone`

SystemOne is not an agent framework. It is a language-independent classifier
integration implemented at an Activity boundary, so it can be paired with any
supported Cadence client language.

### Agent integration

- `none`
- `google-adk`
- `openai-agents`

Until another Cadence SDK supplies an equivalent integration, Google ADK and
OpenAI Agents are valid only with Python. Classifier and agent choices are
independent: a Python recipe may use SystemOne before invoking an agent.

## Valid permutations

| Language | Classifier | Agent | Resulting profile |
| --- | --- | --- | --- |
| Go | none | none | `go/bare` |
| Go | SystemOne | none | `go/systemone` |
| Java | none | none | `java/bare` |
| Java | SystemOne | none | `java/systemone` |
| Python | none | none | `python/bare` |
| Python | SystemOne | none | `python/systemone` |
| Python | none | Google ADK | `python/google-adk` |
| Python | SystemOne | Google ADK | `python/google-adk` with SystemOne |
| Python | none | OpenAI Agents | `python/openai-agents` |
| Python | SystemOne | OpenAI Agents | `python/openai-agents` with SystemOne |

The generator must reject an agent integration for Go, Java, or another language
without a supported Cadence agent integration. It must also reject unknown
languages or integrations rather than generating an approximate scaffold.

## Conceptual generator interface

The future generator should accept orthogonal choices rather than one growing
list of combined template names. A representative interface is:

```text
./scripts/new-recipe.sh <recipe-slug> \
  --language go|java|python \
  --classifier none|systemone \
  --agent none|google-adk|openai-agents
```

The exact argument order, defaults, compatibility behavior, and help text belong
to implementation planning. Generation must remain deterministic and must fail
before writing a partial recipe when a combination is invalid.

## Generated recipe contract

Every implemented profile must generate a recipe that:

1. Has a repository-root recipe README and completed implementation specification.
2. Keeps each language and agent implementation in a predictable implementation
   directory without depending on another recipe.
3. Documents runtime versions, setup, dependencies, and required environment
   variable names without recording secret values.
4. Uses `cadence-ai-samples` as the default domain and keeps domain, task list,
   Workflow ID, and implementation selections consistent between Worker and
   Workflow commands.
5. Provides a clear Worker action and Workflow start/demo action. Optional status,
   signal, query, or stop controls are explicit and copied or run separately.
6. Keeps nondeterministic calls, including classifiers, models, agents, and other
   external services, inside Activities.
7. Provides an offline mock or synthetic core path that does not require paid AI
   access.
8. Validates supported catalog combinations and rejects unsupported combinations
   without guessing.
9. Includes focused tests for Workflow behavior, Activities, configuration,
   failure handling, and command parsing.
10. Remains independently runnable and copyable even when that duplicates code
    from another generated profile.

The contract describes behavior, not one universal source layout or shared
runtime library. Profile implementations may use language-appropriate command
syntax while exposing the same concepts.

## Explorer relationship

The Local Samples Explorer is a consumer of the generated recipe contract, not
the owner of it. Future Explorer work should prefer validated template
conventions over inference about arbitrary source trees.

Minimal recipe-owned metadata may describe facts that cannot be derived safely,
such as a scenario summary, optional controls, supported combinations, or whether
a Workflow action is intentionally available only through Cadence-Web. Metadata
must not become a second command catalog or contain credentials.

Recipes that do not follow an implemented profile should remain visible with
their README and a specific unsupported explanation. The Explorer must not guess
commands for them.

## Phased implementation

### First scaffold families

Prioritize these families when template implementation is approved:

1. `python/bare`
2. `go/bare`
3. `python/google-adk`
4. `python/openai-agents`
5. `go/systemone`

Classifier and agent dimensions remain composable even if every valid combination
is not delivered in the first phase.

### Follow-on profiles

After the first families are validated:

1. Complete Python/SystemOne-only and SystemOne-plus-agent compositions.
2. Add `java/bare` and `java/systemone`.
3. Add other Cadence client languages only with a maintained template,
   contributor guidance, and validation.
4. Add another agent integration only when a Cadence SDK integration and
   independently runnable sample support it.

## Initial OpenAI sample ports

The upstream
[Cadence OpenAI samples](https://github.com/cadence-workflow/cadence-samples/tree/master/python_sdk_samples/openai_samples)
are candidates for later ports:

| Upstream sample | Intended recipe | Language | Classifier | Agent |
| --- | --- | --- | --- | --- |
| Agent Handoffs | `agent-handoffs` | Python | none initially | OpenAI Agents |
| Auto Research | `auto-research` | Python | none initially | OpenAI Agents |
| Human in the Loop | `human-in-the-loop` | Python | none initially | OpenAI Agents |

Each port begins with the Python/OpenAI Agents scaffold. The port preserves the
sample's business intent and durable behavior while adopting this repository's
domain, command contract, tests, documentation, and independent recipe layout.
SystemOne is added only when the scenario has a real specialized classification
decision; it is not added merely to exercise a permutation.

## Validation expectations

Future implementation must test:

- Every advertised combination and every rejected combination.
- Generation into a clean destination and refusal to overwrite an existing one.
- Placeholder replacement and absence of unresolved template markers.
- Generated setup, Worker, Workflow, and test commands.
- Mock execution without credentials or paid provider calls.
- Catalog and environment-variable documentation matching generated source.
- Explorer discovery and command resolution for each delivered profile.
- Independence: a generated recipe does not import source from another recipe.

## Non-goals

This specification does not currently authorize:

- Implementing or expanding the generator.
- Porting the three OpenAI samples.
- Adding Java or another language to the repository.
- Generalizing the Explorer's current source readers.
- Building a general manifest or recipe-authoring platform.
- Making the Explorer web client portable or embedding it in copied recipes.
- Removing intentional duplication through a shared recipe runtime framework.
