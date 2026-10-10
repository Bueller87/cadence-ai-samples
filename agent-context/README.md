# Repository context map

This is the durable map for humans and coding agents. It points to authoritative
sources; it is not a duplicate backlog or session summary. Start with
[AGENTS.md](../AGENTS.md).

## Authority and reading order

1. Inspect the checkout, branch, remotes, and working tree.
2. Read the assigned GitHub Issue and linked PR.
3. Read the linked feature or recipe specification.
4. Read the matching README/runbook.
5. Inspect only the relevant code and tests.
6. Read evidence documents only when evaluating a verification claim.

Authority by concern:

- **Intended behavior and acceptance:** applicable `SPEC*.md`.
- **Work status, priority, and ownership:** upstream GitHub Issues and PRs.
- **Approved design rationale:** numbered ADRs under
  [decisions/](decisions/).
- **Runtime behavior:** source, catalogs, and tests in the checkout.
- **Run instructions:** matching README.
- **Historical verification:** evidence documents such as matrix results and
  performance findings.

No chat transcript, agent memory, `STATUS.md`, or `TODO.md` is authoritative.

## Architecture map

- [recipes/](../recipes/) contains independently runnable, copyable Cadence
  examples. Recipes do not import one another.
- [models.yaml](../models.yaml) and [classifiers.yaml](../classifiers.yaml) are
  repository-wide runtime catalogs; an entry is not proof that every recipe
  implements it.
- [templates/recipe/](../templates/recipe/) and
  [scripts/new-recipe.sh](../scripts/new-recipe.sh) provide the current recipe
  scaffold.
- [tools/explorer/](../tools/explorer/) is a loopback-only companion that reads
  this checkout, checks local dependencies, previews commands, and exposes only
  bounded explicit actions.
- All samples use the Cadence domain `cadence-ai-samples`.
- External and nondeterministic calls belong in Activities, never Workflow code.

## Canonical specifications

- [StreamWave ticket routing](../recipes/ticket-routing/SPEC.md)
- [Local Samples Explorer](../tools/explorer/SPEC-Explorer.md)
- [Future recipe template system](../templates/SPEC-Recipe-Templates.md)
- [Generated recipe specification template](../templates/recipe/SPEC.md)

Keep specifications colocated. Do not move them into this directory merely to
centralize documentation.

## Runbooks and contributor guidance

- [Project README](../README.md)
- [Contributing](../CONTRIBUTING.md)
- [Ticket routing README](../recipes/ticket-routing/README.md)
- [Recurring AI Watch README](../recipes/recurring-ai-watch/README.md)
- [Explorer README](../tools/explorer/README.md)
- [Recipe template README](../templates/recipe/README.md)

## Evidence, not requirements

- [Recurring AI Watch matrix results](../recipes/recurring-ai-watch/MATRIX_RESULTS.md)
- [Ticket routing performance findings](../recipes/ticket-routing/perf/FINDINGS.md)

These record what was tested at a point in time. They do not prove a changed
checkout is currently valid and do not define future work.

## Upstream work queues

All Issues and labels belong to
[`cadence-workflow/cadence-ai-samples`](https://github.com/cadence-workflow/cadence-ai-samples).
`origin` is whichever contributor fork is configured locally.

Approved, available work:

```bash
gh issue list --repo cadence-workflow/cadence-ai-samples \
  --state open --label status:agent-ready
```

Unapproved proposals:

```bash
gh issue list --repo cadence-workflow/cadence-ai-samples \
  --state open --label status:needs-triage
```

Claimed work:

```bash
gh issue list --repo cadence-workflow/cadence-ai-samples \
  --state open --label status:in-progress
```

Before taking an Issue, inspect its assignee, comments, labels, and active linked
PRs. Recheck immediately before claiming. Any contributor may claim an unclaimed
`status:agent-ready` Issue in this order:

```bash
gh issue comment N --repo cadence-workflow/cadence-ai-samples \
  --body "Claiming this approved Issue. Branch: issue-N-short-slug."
gh issue edit N --repo cadence-workflow/cadence-ai-samples \
  --remove-label status:agent-ready --add-label status:in-progress
git switch -c issue-N-short-slug
```

Only a human may mark an Issue agent-ready. Keep it in-progress through the draft
PR handoff. On abandonment, comment the reason and current state, then return it
to the approved queue:

```bash
gh issue edit N --repo cadence-workflow/cadence-ai-samples \
  --remove-label status:in-progress --add-label status:agent-ready
```

Preserve or delete the abandoned branch only with human direction. Active work is
reconstructed from the in-progress Issue, `issue-N-short-slug` branch, and linked
draft PR, never an agent-specific branch prefix or chat history.

## Creating a proposal

Agents may file discovered work without separate permission only as upstream
`status:needs-triage`:

```bash
gh issue create --repo cadence-workflow/cadence-ai-samples \
  --title "Short problem-focused title" \
  --body-file /tmp/cadence-ai-samples-issue.md \
  --label status:needs-triage \
  --label type:recipe \
  --label area:recipes \
  --label source:agent
```

The body must contain the problem, context/source, scope, non-goals, acceptance
criteria, validation approach, and canonical spec link or intended future spec
path. Replace the example `type:*` and `area:*` labels when the proposal is not a
recipe. Agents add `source:agent` explicitly; human-created web forms do not.
Never add `status:agent-ready`, assign priority, or create the Issue in a
contributor fork.

## Contribution and handoff

1. Branch from the agreed upstream base and push to the contributor's `origin`.
2. Keep one implementation PR per Issue.
3. Open a draft cross-fork PR with upstream `main` as base.
4. Link the Issue and record summary, spec/ADR impact, exact tests/results,
   untested behavior, and limitations.
5. Update the Issue with the PR link and blockers.
6. The repository owner reviews and approves every PR; contributors and agents do
   not self-approve or merge.
7. The next agent resumes from the branch, Issue, PR, linked spec, and this map.

## When context documents change

- Update this map only when canonical documents, architecture boundaries, or the
  collaboration protocol change.
- Update a spec only when requirements or acceptance criteria change.
- Add an ADR only after approval of a consequential decision.
- Put changing work status and priority in Issues/PRs.
- Update a README when run instructions or user-visible behavior changes.
- Otherwise, do not churn context documentation.
