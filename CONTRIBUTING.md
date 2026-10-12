# Contributing

Start a contribution with a specific problem or use case, not a library feature.

## Contribution workflow

1. Open an upstream Issue describing the problem, scope, non-goals, acceptance
   criteria, and offline validation path.
2. Wait for a maintainer to add `status:agent-ready` before implementation.
3. Before taking an Issue, check for an assignee, claim comment, or active linked
   PR. Recheck immediately before claiming, comment your intent, replace
   `status:agent-ready` with `status:in-progress`, then create an
   `issue-N-short-slug` branch.
4. Work on that branch, then post and verify the local-completion evidence
   required by [AGENTS.md](AGENTS.md) before calling it ready for review or commit.
5. Follow the contributor's commit policy. Every commit requires DCO sign-off
   with lowercase `git commit -s`, regardless of who creates it.
6. After the signed commit is pushed, open a draft PR against
   `cadence-workflow/cadence-ai-samples:main`.
7. Link the Issue and include actual test commands/results, untested behavior, and
   known limitations.
8. Wait for repository-owner approval. Never self-approve. A coding agent may
   execute a merge only after explicit repository-owner direction and after
   verifying required approval and checks; without authorization, it reports the
   remaining gate and stops.

Anyone may propose work. Unapproved ideas use `status:needs-triage`; only a human
maintainer promotes them to `status:agent-ready`.

Keep claimed work `status:in-progress` until merge closes the Issue. If abandoning
it, comment the reason and current state, replace `status:in-progress` with
`status:agent-ready`, and ask before deleting the branch. Branch names identify
the Issue, not the coding tool, because work may move between agents.

## Coding-agent contributions

Agent-assisted changes follow the same Issue, branch, PR, review, and validation
requirements as human-written changes. Coding agents must begin with
[AGENTS.md](AGENTS.md) and the
[repository context map](agent-context/README.md). The
[contribution workflow cheat sheet](WORKFLOW.md) maps short intent phrases to
these gates. Agents may create upstream `status:needs-triage` proposals but may
not approve priority or architecture, approve PRs, or authorize merges.

## Recipe contributions

Before implementation:

1. Describe the proposed use case and why durable workflow orchestration helps.
2. Run `./scripts/new-recipe.sh your-recipe-name --language python --classifier none --agent none`
   (or another implemented profile from `./scripts/new-recipe.sh --help`), then
   tailor its `SPEC.md`.
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

When changing a generated profile or its contract, run the generator and
Explorer suites documented in
[`templates/profiles/README.md`](templates/profiles/README.md), plus the
profile's generated offline tests. Keep its `SPEC.md` fields, README and setup
commands, catalog names, credential environment-variable names, and actual CLI
in sync. Live inference changes also need an offline safe-endpoint mutation test
that reaches the selected transport without making a provider call.
