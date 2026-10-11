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

- `python/bare`: Python, no classifier, no agent. Generates
  `recipes/<slug>/python/bare/`.

## Placeholders

The generator replaces these markers in every file under `recipe/` and in
`next-steps.txt`:

| Marker | Example for `invoice-review` |
| --- | --- |
| `__RECIPE_SLUG__` | `invoice-review` |
| `__RECIPE_TITLE__` | `Invoice Review` |
| `__RECIPE_CLASS__` | `InvoiceReview` |

Any other `__UPPER_CASE__` marker left in a file or path stops generation before
anything is written. Local artifacts such as `.venv`, `__pycache__`, and
`*.egg-info` are never copied.

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
