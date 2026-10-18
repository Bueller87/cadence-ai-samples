# Contributing

Start a contribution with a specific problem or use case, not a library feature.

## First-time contributor path

Your fork and the canonical repository have different roles:

- `origin` is your contributor fork. Create and push implementation branches
  there.
- `upstream` is `cadence-workflow/cadence-ai-samples`. Issues, approvals, and
  pull-request targets live there.

Run `git remote -v` before starting. If the canonical repository is missing, add
it with:

```bash
git remote add upstream https://github.com/cadence-workflow/cadence-ai-samples.git
```

An open Issue's lifecycle label tells you what you may do:

- `status:needs-triage` means proposed work. It is not claimable, so do not
  self-assign or implement it.
- `status:agent-ready` means a maintainer approved the work. It is claimable only
  when it has no assignee, claim comment, or active linked implementation PR.
- `status:in-progress` means the work is already claimed. Do not start a
  competing implementation.

Assignment records ownership of approved work, not interest or priority. Only a
human maintainer may promote a proposal to `status:agent-ready`.

### Find and plan work

1. Find an open upstream `status:agent-ready` Issue.
2. Check its assignee, comments, and linked PRs. Do not take work owned by
   someone else.
3. Follow [Plan before building](WORKFLOW.md#plan-before-building). Review the
   assumptions, dependencies, scope, non-goals, validation, and decisions that
   need maintainer authority before claiming or editing.
4. Recheck the Issue immediately before claiming it.

If no claimable Issue exists, do not self-assign a `status:needs-triage` Issue.
Outside contributors should use the repository
[Issue forms](https://github.com/cadence-workflow/cadence-ai-samples/issues/new/choose)
to submit the problem, scope, non-goals, acceptance criteria, validation
approach, and spec path. The form applies only its configured initial labels,
including `status:needs-triage`. Maintainer triage applies any missing
`source:agent`, type, area, or other classification labels. Missing
classification labels never make a proposal claimable.

Maintainers and contributors with upstream label permission may instead use the
[labeled CLI proposal path](agent-context/README.md#creating-a-proposal). An
agent without that permission prepares the proposal for human form submission
or requests maintainer assistance; it must not run the labeled command. Then
wait for a maintainer to add `status:agent-ready`.

### Claim approved work

If you can update the upstream Issue, comment your intent with your GitHub
username and proposed branch, replace `status:agent-ready` with
`status:in-progress`, and then create an `issue-N-short-slug` branch on your
fork. The claim comment and `status:in-progress` together record your ownership.

If you cannot change upstream labels or assignment, post this comment:

> I would like to claim this approved Issue. Proposed branch:
> `issue-N-short-slug`. I cannot change upstream labels or assignment.
> Maintainer, please record me as the owner and change `status:agent-ready` to
> `status:in-progress`.

Do not create the branch or begin implementation until the upstream Issue has
`status:in-progress` and identifies you as the owner through either assignment
or a maintainer comment confirming the claim.

### Complete and hand off the work

1. Work on the Issue branch, then post and verify the local-completion evidence
   required by [AGENTS.md](AGENTS.md) before calling it ready for review or commit.
2. Follow the contributor's commit policy. Every commit requires DCO sign-off
   with lowercase `git commit -s`, regardless of who creates it.
3. After the signed commit is pushed, open a draft PR against
   `cadence-workflow/cadence-ai-samples:main`.
4. Link the Issue and include actual test commands/results, untested behavior,
   and known limitations.
5. Wait for repository-owner approval. Never self-approve. A coding agent may
   execute a merge only after explicit repository-owner direction and after
   verifying required approval and checks; without authorization, it reports the
   remaining gate and stops.

Keep claimed work `status:in-progress` until merge closes the Issue. If
abandoning it, comment the reason and current state, replace
`status:in-progress` with `status:agent-ready`, and ask before deleting the
branch. Branch names identify the Issue, not the coding tool, because work may
move between agents.

## DCO and licensing

The required lowercase `git commit -s` sign-off certifies that the contributor
has the right to submit the contribution under the Developer Certificate of
Origin (DCO). The repository's [Apache License 2.0](LICENSE) governs use,
modification, and distribution of the repository.

## Coding-agent contributions

Agent-assisted changes follow the same Issue, branch, PR, review, and validation
requirements as human-written changes. Coding agents must begin with
[AGENTS.md](AGENTS.md) and the
[repository context map](agent-context/README.md). The
[contribution workflow cheat sheet](WORKFLOW.md) maps short intent phrases to
these gates. Agents may create upstream `status:needs-triage` proposals but may
not approve priority or architecture, approve PRs, or authorize merges. Agents
without upstream label permission prepare an Issue-form submission or ask a
maintainer to file it.

## Recipe contributions

Before implementation:

1. Describe the proposed use case and why durable workflow orchestration helps.
2. Run `./scripts/new-recipe.sh your-recipe-name --language python --classifier none --agent none`
   (or another implemented profile from `./scripts/new-recipe.sh --help`), then
   tailor its `SPEC.md`. To add another implementation profile to an existing
   recipe without changing recipe-owned root files, use
   `./scripts/add-recipe-implementation.sh --help`.
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
