# Contributing

Start a contribution with a specific problem or use case, not a library feature.

## Contribution workflow

1. Open an upstream Issue describing the problem, scope, non-goals, acceptance
   criteria, and offline validation path.
2. Wait for a maintainer to add `status:agent-ready` before implementation.
3. Before taking an Issue, check for an assignee, claim comment, or active linked
   PR. Comment your intent so another contributor does not duplicate it.
4. Work on a branch in your fork and open a draft PR against
   `cadence-workflow/cadence-ai-samples:main`.
5. Link the Issue and include actual test commands/results, untested behavior, and
   known limitations.
6. Sign every commit for DCO with `git commit -s`.
7. Wait for repository-owner approval. Do not approve or merge your own PR.

Anyone may propose work. Unapproved ideas use `status:needs-triage`; only a human
maintainer promotes them to `status:agent-ready`.

## Coding-agent contributions

Agent-assisted changes follow the same Issue, branch, PR, review, and validation
requirements as human-written changes. Coding agents must begin with
[AGENTS.md](AGENTS.md) and the
[repository context map](agent-context/README.md). Agents may create upstream
`status:needs-triage` proposals but may not approve priority, architecture, PRs,
or merges.

## Recipe contributions

Before implementation:

1. Describe the proposed use case and why durable workflow orchestration helps.
2. Run `./scripts/new-recipe.sh your-recipe-name` to create the Python starter,
   then tailor its `SPEC.md`.
3. Define how the recipe can be exercised with mock or synthetic data without paid AI API access.

When implementing a recipe:

- Keep it independently runnable and free of dependencies on other recipes.
- Keep workflow and Activity code easy to copy into another project.
- Include a README that explains how to run the recipe, what it does, and what users should expect.
- Include mock or synthetic test data that contains no real customer information or proprietary data.
- Keep credentials, secrets, and environment-specific configuration out of the repository.
- Do not use employer-owned code, internal documentation, proprietary configurations, credentials, or other non-public assets.
- Avoid unnecessary abstractions, shared integration layers, and premature refactoring.

Modularization is optional. A workflow, its Activities, and related business logic may remain together in one file when that is the clearest presentation.
