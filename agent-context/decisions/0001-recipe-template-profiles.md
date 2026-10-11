# ADR 0001: Explicit recipe template profiles

- Status: Accepted
- Date: 2026-10-10

## Context

`scripts/new-recipe.sh` accepted only a slug and copied one implicit starter
from `templates/recipe/`. That starter combined Python, the Jev classifier, and
Google ADK without saying so. The
[recipe template system specification](../../templates/SPEC-Recipe-Templates.md)
defines three independent dimensions (language, classifier integration, agent
integration) and ten valid combinations. Generated recipes must stay
independently runnable and copyable.

## Decision

- The generator requires `--language`, `--classifier`, and `--agent`. There is
  no default profile and no slug-only compatibility mode.
- One `case` matrix in `scripts/new-recipe.sh` maps each valid combination to a
  profile ID. Help output and validation both read it. SystemOne-plus-agent
  profiles are named `python/google-adk-systemone` and
  `python/openai-agents-systemone`.
- Availability is an explicit `implemented_profiles` list. A valid but
  unimplemented profile fails without writing.
- Each profile is a complete source tree under
  `templates/profiles/<language>/<profile-name>/`, with `recipe/` copied to
  `recipes/<slug>/` and `next-steps.txt` printed afterward. Profiles share no
  files and no runtime library; duplication between profiles is intentional.
- Generation assembles the recipe in a hidden staging directory under
  `recipes/`, rejects unresolved placeholders, and renames it into place.
- The generator stays a Bash script compatible with macOS Bash 3.2, so
  generation needs no language runtime beyond what the selected profile uses.
- `templates/recipe/` is removed. Its generic specification and prompt
  templates move into each profile. Its Python plus Jev plus Google ADK code is
  not carried forward as a profile; Phase 2 builds `python/google-adk` from
  validated recipe patterns instead.
- `python/bare` generates its implementation in `recipes/<slug>/python/bare/`
  and starts demo Workflows with `ALLOW_DUPLICATE` Workflow ID reuse.

## Alternatives considered

- **Keep slug-only usage defaulting to Python.** Rejected because it preserves
  an undocumented default the specification forbids.
- **Shared base template plus per-dimension overlays.** Rejected because
  overlays couple profiles and make a generated recipe harder to read as one
  unit. Full-profile copies keep each scaffold reviewable on its own.
- **Python generator script.** Rejected for Phase 1 because it adds a Python
  requirement for Go and Java contributors. The Bash matrix is small and is
  tested through the real script.
- **Derive availability from template directory existence.** Rejected because a
  partial template directory would become available silently. Tests instead
  check that template directories and `implemented_profiles` match.

## Consequences

- Every new profile adds a full template tree, an `implemented_profiles` entry,
  and generator and Explorer test coverage.
- Fixes common to several profiles must be applied to each profile by hand.
- Contributors must type three options. Help output lists every valid
  combination to compensate.
- Implementation directory names for Go, Java, and SystemOne profiles are
  decided when those profiles are delivered.

## References

- [Issue #9](https://github.com/cadence-workflow/cadence-ai-samples/issues/9)
- [Recipe template system specification](../../templates/SPEC-Recipe-Templates.md)
- [Template profiles README](../../templates/profiles/README.md)
