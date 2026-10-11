# Recipe template profiles

Each directory here is one complete implementation profile used by
[`scripts/new-recipe.sh`](../../scripts/new-recipe.sh). The required behavior is
defined in the
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

Every profile exposes exactly `worker` and `start`, each with an explicit
`mock` or `live` mode. The language-specific shapes are:

```text
python main.py worker --mode mock
go run . worker -mode mock
./gradlew run --args='worker --mode mock'
```

Replace `worker` with `start` for the starter command. Inference selections,
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
`__pycache__`, and `*.egg-info` are never copied.

## Adding a profile

1. Confirm an approved Issue covers the profile.
2. Add `templates/profiles/<language>/<profile-name>/` with `recipe/` and
   `next-steps.txt`, using the profile ID from the generator matrix.
3. Add the profile ID to `implemented_profiles` in `scripts/new-recipe.sh`.
4. Update `IMPLEMENTED` and the expected generation output in
   `scripts/tests/test_new_recipe.py`, and add Explorer discovery coverage.
5. Validate as described below.

## Validation

From the repository root:

```bash
python3 -m unittest discover -s scripts/tests -v
tools/explorer/.venv/bin/python -m unittest discover -s tools/explorer/tests -v
```

Then generate the profile into a scratch recipe, run its documented offline
tests, and delete the scratch recipe:

```bash
./scripts/new-recipe.sh scratch-check --language python --classifier none --agent none
# Follow the printed next steps, then:
rm -rf recipes/scratch-check
```

These checks keep the profile fields, README commands, setup steps, catalog
names, credential environment-variable names, generated CLI, and Explorer
resolution aligned. Profiles with live inference must also retain an offline
endpoint-mutation test proving that a changed safe catalog endpoint reaches the
provider transport.

The implemented profiles need no recipe metadata layer. Their validated layout
and literal CLI contracts provide the facts the Explorer needs. If a future
profile cannot express a required fact through those contracts, add metadata
only after a failing contract test demonstrates the gap.
