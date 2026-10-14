# Recipe template system specification

Status: approved. Phase 1
([Issue #9](https://github.com/cadence-workflow/cadence-ai-samples/issues/9))
delivers the profile generator and the `python/bare` profile. Each remaining
profile, sample port, or Explorer change still requires its own approved Issue.
The template architecture is recorded in
[ADR 0001](../agent-context/decisions/0001-recipe-template-profiles.md).

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
| Python | SystemOne | Google ADK | `python/google-adk-systemone` |
| Python | none | OpenAI Agents | `python/openai-agents` |
| Python | SystemOne | OpenAI Agents | `python/openai-agents-systemone` |

The generator must reject an agent integration for Go, Java, or another language
without a supported Cadence agent integration. It must also reject unknown
languages or integrations rather than generating an approximate scaffold.

## Generator interface

The generator accepts orthogonal choices rather than one growing list of
combined template names:

```text
./scripts/new-recipe.sh <recipe-slug> \
  --language go|java|python \
  --classifier none|systemone \
  --agent none|google-adk|openai-agents
```

- All three options are required. There is no default profile, and slug-only
  usage fails with help that lists the valid combinations.
- Options may appear in any order as `--name value` or `--name=value`.
- One matrix in the generator defines the valid combinations. Help output and
  validation both read it.
- A valid profile that has no template in the checkout fails explicitly and
  names the implemented profiles.
- The generator writes nothing for missing, repeated, or unknown options;
  unknown values; combinations outside the matrix; unimplemented profiles;
  invalid slugs; or an existing destination, including a file or symlink.
- Generation is deterministic. The recipe is assembled in a hidden staging
  directory under `recipes/` and renamed into place only after every
  `__UPPER_CASE__` placeholder in file contents and paths is resolved.
- On success the generator prints `created recipes/<slug> from profile
  <profile>` followed by the profile's next steps.

Profile templates live under `templates/profiles/`; see its
[README](profiles/README.md) for the source layout and how to add a profile.

### Add one implementation to an existing recipe

When a recipe already exists, add another independently runnable profile
directory without changing recipe-owned root documents or sibling
implementations:

```text
./scripts/add-recipe-implementation.sh <existing-recipe-slug> \
  --language go|java|python \
  --classifier none|systemone \
  --agent none|google-adk|openai-agents
```

- The recipe root must already exist as a normal directory under `recipes/` with
  required root files `README.md`, `SPEC.md`, and `PROMPT.md`. Symlink recipe
  roots, paths outside `recipes/`, and missing or non-directory roots fail
  before writing.
- The command reuses the same profile matrix and placeholder rendering as
  `new-recipe.sh`, but copies only `templates/profiles/<profile>/recipe/<profile>/`
  into `recipes/<slug>/<profile>/`.
- The profile template path, its `recipe/` directory, the implementation source
  directory, and relevant ancestors must not be symlinks. The resolved
  implementation source must stay inside the selected profile template.
- `scripts/recipe_implementation_validate.py` rejects generated artifacts in
  profile source and staged output, including empty `.venv`, `venv`, `build`,
  `.gradle`, `__pycache__`, `.pytest_cache`, and `*.egg-info` directories.
  Add-implementation fails closed and does not strip artifacts.
- The validator also rejects symlinks and cross-recipe imports in Python, Go,
  and Java source before anything is moved into the recipe.
- Recipe-owned root files, metadata, and every existing implementation must
  remain byte-for-byte unchanged.
- The destination `recipes/<slug>/<profile>/` must not exist. Symlinks, files,
  or other collisions on the destination path fail before writing.
- Output is staged under a hidden directory inside the recipe root, validated,
  then moved atomically. Failures remove staging output, remove any newly created
  empty parent directories, and leave no partial destination.
- On success the command prints `added recipes/<slug>/<profile> from profile
  <profile>` followed by the profile's next steps.

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
11. Declares and tests Workflow ID reuse behavior. A stable demo Workflow ID
    intended for repeated runs must explicitly allow a new Run after the previous
    Run closes while still rejecting a duplicate start when a Run is open.

The contract describes behavior, not one universal source layout or shared
runtime library. Profile implementations may use language-appropriate command
syntax while exposing the same concepts.

### Generic command contract

Every generic implementation profile exposes exactly two actions:

- `worker --mode mock|live`
- `start --mode mock|live`

Languages may use their idiomatic subcommand and option syntax, but the action
names and mode values remain `worker`, `start`, `mock`, and `live`. Generic
profiles do not expose scenario-specific actions or modes such as `demo`,
`live-demo`, `batch`, acknowledgment, or benchmark.

Bare profiles accept both modes for interface consistency. Their starter
Activities remain synthetic in both modes until the generated recipe replaces
the live path with its own external Activity integration.

Specialized recipes may expose additional scenario-specific commands when their
own specification requires them. In particular, Ticket Routing remains a
specialized reference recipe; its command surface does not define the generic
template contract.

### Inference as configuration

For profiles with live inference, `models.yaml` is the source of model provider,
model name, and provider-native base endpoint values, and `classifiers.yaml` is
the equivalent source for classifier selections. The selected catalog entry is
the sole runtime source of those values. Worker-local adapters may derive a
transport-specific protocol route from that configured base, such as adding
`/v1beta/openai/` for Google's OpenAI-compatible API or `/v1/` for Ollama. They
do not define provider endpoint constants, require an exact host, or put
endpoints into Workflow input or history.

Endpoint validation remains fail closed without replacing configuration:
credential-bearing remote providers require HTTPS, local providers require a
loopback host, and URLs reject embedded credentials, query strings, fragments,
missing hosts, and unsupported schemes. Offline tests change a safe catalog
endpoint and verify that the changed base, plus any documented adapter-owned
protocol suffix, reaches the provider client or request transport without
making a live provider call.

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

[Issue #9](https://github.com/cadence-workflow/cadence-ai-samples/issues/9)
delivered the generator and `python/bare`, which generates its implementation
in `recipes/<slug>/python/bare/`. The remaining first families are tracked in
[Issue #25](https://github.com/cadence-workflow/cadence-ai-samples/issues/25):

1. `go/bare`
2. `python/google-adk`
3. `python/openai-agents`
4. `go/systemone`

Classifier and agent dimensions remain composable even if every valid combination
is not delivered in the first phase.

### Follow-on profiles

Follow-on work is tracked in
[Issue #13](https://github.com/cadence-workflow/cadence-ai-samples/issues/13).
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

Port status and ownership live in
[Issue #16](https://github.com/cadence-workflow/cadence-ai-samples/issues/16),
[Issue #15](https://github.com/cadence-workflow/cadence-ai-samples/issues/15),
and [Issue #14](https://github.com/cadence-workflow/cadence-ai-samples/issues/14),
respectively.

Each port begins with the Python/OpenAI Agents scaffold. The port preserves the
sample's business intent and durable behavior while adopting this repository's
domain, command contract, tests, documentation, and independent recipe layout.
SystemOne is added only when the scenario has a real specialized classification
decision; it is not added merely to exercise a permutation.

## Validation expectations

Generator behavior is tested in
[scripts/tests/test_new_recipe.py](../scripts/tests/test_new_recipe.py) and
[scripts/tests/test_add_recipe_implementation.py](../scripts/tests/test_add_recipe_implementation.py).
Each delivered profile must also pass its generated offline suite and the Explorer
discovery test. Implementation must test:

- Every advertised combination and every rejected combination.
- Generation into a clean destination and refusal to overwrite an existing one.
- Placeholder replacement and absence of unresolved template markers.
- Generated setup, Worker, Workflow, and test commands.
- Mock execution without credentials or paid provider calls.
- Catalog and environment-variable documentation matching generated source.
- Catalog endpoint changes reaching the selected provider transport without
  live calls or hardcoded-host overrides.
- Explorer discovery and command resolution for each delivered profile.
- Independence: a generated recipe does not import source from another recipe.

## Non-goals

This specification does not authorize work beyond approved Issues. In
particular, it does not authorize:

- Adding profiles beyond `python/bare` without their approved Issue.
- Porting the three OpenAI samples.
- Adding Java or another language to the repository.
- Generalizing the Explorer's current source readers.
- Building a general manifest or recipe-authoring platform.
- Making the Explorer web client portable or embedding it in copied recipes.
- Removing intentional duplication through a shared recipe runtime framework.
