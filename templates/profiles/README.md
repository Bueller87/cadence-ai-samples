# Recipe template profiles

Each directory here is one complete implementation profile used by
[`scripts/new-recipe.sh`](../../scripts/new-recipe.sh) and
[`scripts/add-recipe-implementation.sh`](../../scripts/add-recipe-implementation.sh).
The required behavior is defined in the
[recipe template system specification](../SPEC-Recipe-Templates.md).

## Layout

```text
templates/profiles/<language>/<profile-name>/
  recipe/          copied to recipes/<slug>/ with placeholders replaced
  next-steps.txt   printed after a successful generation
```

`recipe/` mirrors the generated recipe exactly: the recipe README, completed
`SPEC.md` profile fields, `PROMPT.md`, and the implementation directory with its
source, dependencies, and tests. Profiles do not share files. Duplicate code
between profiles when that keeps each generated recipe independently copyable.

Implemented profiles:

- `go/bare`: Go, no classifier, no agent. Generates
  `recipes/<slug>/go/bare/`.
- `go/systemone`: Go with a SystemOne classifier and no agent. Generates
  `recipes/<slug>/go/systemone/`.
- `java/bare`: Java, no classifier, no agent. Generates
  `recipes/<slug>/java/bare/`.
- `java/systemone`: Java with a SystemOne classifier and no agent. Generates
  `recipes/<slug>/java/systemone/`.
- `python/bare`: Python, no classifier, no agent. Generates
  `recipes/<slug>/python/bare/`.
- `python/systemone`: Python with a SystemOne classifier and no agent. Generates
  `recipes/<slug>/python/systemone/`.
- `python/google-adk`: Python with Google ADK and no classifier. Generates
  `recipes/<slug>/python/google-adk/`.
- `python/google-adk-systemone`: Python with Google ADK and SystemOne. Generates
  `recipes/<slug>/python/google-adk-systemone/`.
- `python/openai-agents`: Python with OpenAI Agents and no classifier. Generates
  `recipes/<slug>/python/openai-agents/`.
- `python/openai-agents-systemone`: Python with OpenAI Agents and SystemOne.
  Generates `recipes/<slug>/python/openai-agents-systemone/`.

## Generic command contract

Every profile has an executable implementation-local launcher. Prepare it once
with `./run.sh setup`; Worker and Workflow commands also prepare automatically
when dependency declarations change. Every profile exposes exactly `worker` and
`start`, each with an explicit `mock` or `live` mode:

```text
./run.sh worker --mode mock
./run.sh worker -mode mock
```

Python and Java use `--mode`; Go uses `-mode`. Replace `worker` with `start` for
the starter command. Inference selections,
catalog paths, credentials, and live-confirmation flags belong only to live
Workers. Start commands contain business input and Cadence routing values, never
model, classifier, catalog, credential, or confirmation configuration.

Generic profiles do not add scenario actions or modes such as `demo`,
`live-demo`, `batch`, acknowledgment, or benchmark. Specialized recipes may
define those under their own specifications; they do not change this profile
contract.

## Placeholders

The generator replaces these markers in every file under `recipe/` and in
`next-steps.txt`:

| Marker | Example for `invoice-review` |
| --- | --- |
| `__RECIPE_SLUG__` | `invoice-review` |
| `__RECIPE_TITLE__` | `Invoice Review` |
| `__RECIPE_CLASS__` | `InvoiceReview` |

Any other `__UPPER_CASE__` marker left in a file or path stops generation before
anything is written. Local artifacts such as `.venv`, `.gradle`, `build`,
`__pycache__`, and `*.egg-info` are never copied by `new-recipe.sh`.
`add-recipe-implementation.sh` rejects those artifacts in profile source or
staged output instead of stripping them, including empty artifact directories.

## Add one implementation to an existing recipe

Use `add-recipe-implementation.sh` when a recipe already exists and you need
another profile directory under `recipes/<slug>/`.

- The existing recipe root must contain `README.md`, `SPEC.md`, and `PROMPT.md`.
- The selected profile template path, its `recipe/` directory, the
  implementation source directory, and relevant ancestors must be normal
  directories, not symlinks. The resolved implementation source must remain
  inside `templates/profiles/<profile>/`.
- `scripts/recipe_implementation_validate.py` checks profile source and staged
  output for generated artifacts, symlinks, and cross-recipe imports before the
  command moves anything into the recipe.
- Failures remove staging output and any newly created empty parent directories
  under the recipe, leaving sibling implementations and recipe-root files
  unchanged.

## Adding a profile

1. Confirm an approved Issue covers the profile.
2. Add `templates/profiles/<language>/<profile-name>/` with `recipe/` and
   `next-steps.txt`, using the profile ID from the generator matrix.
3. Add the profile ID to `implemented_profiles` in `scripts/new-recipe.sh` and
   `scripts/add-recipe-implementation.sh`.
4. Update `IMPLEMENTED` and the expected generation output in
   `scripts/tests/test_new_recipe.py` and
   `scripts/tests/test_add_recipe_implementation.py`, and add Explorer discovery
   coverage.
5. Validate as described below.

## Validation

From the repository root:

```bash
python3 -m venv scripts/.venv
scripts/.venv/bin/python -m pip install -r scripts/requirements-test.txt
scripts/.venv/bin/python -m unittest discover -s scripts/tests -v
tools/explorer/.venv/bin/python -m unittest discover -s tools/explorer/tests -v
```

Then generate the profile into a scratch recipe, run its documented offline
tests, and delete the scratch recipe:

```bash
./scripts/new-recipe.sh scratch-check --language python --classifier none --agent none
# Follow the printed next steps, then:
rm -rf recipes/scratch-check
```

To add another profile to an existing scratch recipe without touching its root
documents, use:

```bash
./scripts/add-recipe-implementation.sh scratch-check \
  --language python --classifier none --agent google-adk
```

These checks keep the profile fields, README commands, setup steps, catalog
names, credential environment-variable names, generated CLI, and Explorer
resolution aligned. Profiles with live inference must also retain an offline
endpoint-mutation test proving that a changed safe catalog endpoint reaches the
provider transport.

The implemented profiles need no recipe metadata layer. Their validated layout
and literal CLI contracts provide the facts the Explorer needs. A generated
recipe may add optional recipe-root `explorer.yaml` only when it has explicit
Cadence Signal handlers that need Explorer controls. The file is Signal-only;
it must follow the strict schema in the
[Explorer specification](../../tools/explorer/SPEC-Explorer.md#recipe-owned-signal-metadata)
and must not duplicate commands, Workflow IDs, providers, endpoints, or
credentials.

When copying a recipe with `explorer.yaml`, retain it only if the copied
Workflow keeps the exact declared Signal names, positional argument order, and
safety behavior. Modify and test the declaration with any handler change, or
remove the file to make the copied recipe show no Explorer Signal panel.
