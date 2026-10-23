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
- [templates/profiles/](../templates/profiles/),
  [scripts/new-recipe.sh](../scripts/new-recipe.sh), and
  [scripts/add-recipe-implementation.sh](../scripts/add-recipe-implementation.sh)
  generate recipes from explicit language, classifier, and agent choices. Each
  profile is a complete, independent template tree.
- [tools/explorer/](../tools/explorer/) is a loopback-only companion that reads
  this checkout, checks local dependencies, previews commands, and exposes only
  bounded explicit actions.
- All samples use the Cadence domain `cadence-ai-samples`.
- External and nondeterministic calls belong in Activities, never Workflow code.

## Canonical specifications

- [Agent Handoffs trip planning](../recipes/agent-handoffs/SPEC.md)
- [Auto Research briefing](../recipes/auto-research/SPEC.md)
- [StreamWave ticket routing](../recipes/ticket-routing/SPEC.md)
- [Human-in-the-loop ride booking](../recipes/human-in-the-loop/SPEC.md)
- [Local Samples Explorer](../tools/explorer/SPEC-Explorer.md)
- [Recipe template system](../templates/SPEC-Recipe-Templates.md)
- [Generated `python/bare` recipe specification template](../templates/profiles/python/bare/recipe/SPEC.md)

Keep specifications colocated. Do not move them into this directory merely to
centralize documentation.

## Runbooks and contributor guidance

- [Project README](../README.md)
- [Contributing](../CONTRIBUTING.md)
- [Contribution workflow cheat sheet](../WORKFLOW.md)
- [KubeCon NA 2026 ContribFest participant guide](../CONTRIBFEST.md)
- [Agent Handoffs README](../recipes/agent-handoffs/README.md)
- [Auto Research README](../recipes/auto-research/README.md)
- [Ticket routing README](../recipes/ticket-routing/README.md)
- [Human-in-the-loop README](../recipes/human-in-the-loop/README.md)
- [Recurring AI Watch README](../recipes/recurring-ai-watch/README.md)
- [Explorer README](../tools/explorer/README.md)
- [Template profiles README](../templates/profiles/README.md)

## Architecture decisions

- [ADR 0001: Explicit recipe template profiles](decisions/0001-recipe-template-profiles.md)

## Evidence, not requirements

- [Recurring AI Watch matrix results](../recipes/recurring-ai-watch/MATRIX_RESULTS.md)
- [Human-in-the-loop validation results](../recipes/human-in-the-loop/MATRIX_RESULTS.md)

These record what was tested at a point in time. They do not prove a changed
checkout is currently valid and do not define future work.

## Upstream work queues

All Issues and labels belong to
[`cadence-workflow/cadence-ai-samples`](https://github.com/cadence-workflow/cadence-ai-samples).
`origin` is whichever contributor fork is configured locally.

Approved, available work:

```bash
gh issue list --repo cadence-workflow/cadence-ai-samples \
  --state open --label status:ready
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
`status:ready` Issue. Follow the
[permission-aware claim path](../CONTRIBUTING.md#claim-approved-work).
Contributors with upstream label permission claim in this order:

```bash
gh issue comment N --repo cadence-workflow/cadence-ai-samples \
  --body "Claiming this approved Issue. Branch: issue-N-short-slug."
gh issue edit N --repo cadence-workflow/cadence-ai-samples \
  --remove-label status:ready --add-label status:in-progress
git switch -c issue-N-short-slug
```

Without upstream label or assignment permission, request maintainer help and
wait for both `status:in-progress` and repository-visible ownership before
creating the branch.

Only a human may mark an Issue `status:ready`. Keep it in-progress through the draft
PR handoff. On abandonment, comment the reason and current state, then return it
to the approved queue:

```bash
gh issue edit N --repo cadence-workflow/cadence-ai-samples \
  --remove-label status:in-progress --add-label status:ready
```

Preserve or delete the abandoned branch only with human direction. Active work is
reconstructed from the in-progress Issue, `issue-N-short-slug` branch, and linked
draft PR, never an agent-specific branch prefix or chat history.

## Creating a proposal

For plain `Capture this idea`, first ask whether this is general idea intake or
a defined implementation task. General intake remains format-neutral. For a
defined task, ask whether it should be human-first or agent-first before
drafting it, then follow the matching permission-aware path.
Outside contributors should use the repository
[Issue forms](https://github.com/cadence-workflow/cadence-ai-samples/issues/new/choose)
by default. Choose the general work proposal for an idea that still needs
maintainer triage before implementation-task authoring. Choose the human-first
form for a defined task with a concise starting state, observable gap or
failure, exact edit and validation paths, and a useful stopping point. Choose
the agent-first form for a defined task expressed as a detailed durable
implementation contract. Specialized recipe intake remains format-neutral until
maintainer triage. Forms apply only their configured initial labels: every form
adds `status:needs-triage`, the implementation-task forms add their `format:*`
label, and the recipe proposal form also adds its configured type and area
labels.

An agent without upstream label permission prepares the proposal for a human to
submit through a form or requests maintainer assistance. It must not run a
labeled Issue command it cannot authorize. The labeled CLI proposal path is
agent-first only. Maintainers or contributors with upstream label permission
may file agent-first discovered work directly as `status:needs-triage`:

```bash
gh issue create --repo cadence-workflow/cadence-ai-samples \
  --title "Short problem-focused title" \
  --body-file /tmp/cadence-ai-samples-issue.md \
  --label status:needs-triage \
  --label format:agent-first \
  --label type:recipe \
  --label area:recipes \
  --label source:agent
```

The agent-first body must contain the problem, context/source, scope, non-goals,
decisions requiring human authority, acceptance criteria, specifications or
ADRs, exact validation, limitations, dependencies or blockers, and durable
handoff requirements. Replace the example `type:*` and `area:*` labels when the
proposal is not a recipe. The labeled command adds `source:agent` explicitly;
human-created web forms do not. When a proposer cannot apply `source:agent`,
type, area, or another classification label, maintainer triage applies it.
Missing classification or format labels never authorize implementation. Every
proposal must receive or request `status:needs-triage`; only a human maintainer
may later add `status:ready`. Never assign priority or create the Issue in a
contributor fork.

## Local completion and handoff

Before reporting implementation ready for review or commit, post the evidence
required by [AGENTS.md](../AGENTS.md) to the claimed Issue. Re-read the Issue,
open the new comment URL, and verify `status:in-progress`. This checkpoint does
not require a commit, push, or PR.

A fresh agent may resume pre-commit work from the claimed Issue and its preserved
matching local checkout. Cross-computer recovery additionally requires a signed
commit and pushed branch; the PR records the later review and merge state.

After local completion:

1. Follow the contributor's policy for creating the DCO-signed commit.
2. Push only when authorized, using the contributor's verified `origin`.
3. Verify the committed branch is pushed, then open or update one draft
   cross-fork PR with upstream `main` as base. The root template is the automatic
   human-first default. Agents explicitly select the detailed template with
   `gh pr create --template .github/PULL_REQUEST_TEMPLATE/agent-first.md ...`,
   or use a compare URL ending in `?expand=1&template=agent-first.md`.
4. Link the Issue and finalize the PR body from committed Git and Issue evidence,
   including spec/ADR impact, exact tests/results, untested behavior, and
   limitations.
5. Update the Issue with the PR link and blockers.
6. A non-owner PR requires one approval from a human member of the `cadence-dev`
   team. New commits dismiss prior approvals, so approval must cover the current
   head. The repository owner, `Bueller87`, may use the pull-request-only review
   bypass on an owner-authored PR without self-review and must record its use in
   the PR. Repository policy forbids using that bypass on a non-owner PR. Human
   review approval and repository-owner merge authorization are separate. Only
   the repository owner may authorize a merge. Agents never approve PRs or
   supply merge authority.
7. The next agent resumes from the branch, Issue, PR, linked spec, and this map,
   without chat or terminal history.

## When context documents change

- Update this map only when canonical documents, architecture boundaries, or the
  collaboration protocol change.
- Update a spec only when requirements or acceptance criteria change.
- Add an ADR only after approval of a consequential decision.
- Put changing work status and priority in Issues/PRs.
- Update a README when run instructions or user-visible behavior changes.
- Otherwise, do not churn context documentation.
