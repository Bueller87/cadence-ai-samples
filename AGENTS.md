# Coding agent contract

This file is the canonical entry point for every coding agent working in this
repository. Tool-specific instruction files may point here but must not duplicate
or contradict it.

## Authority

- Git records what changed.
- GitHub Issues record proposed and approved work.
- Pull requests record implementation, review, limitations, and test evidence.
- Colocated specifications record intended behavior and acceptance criteria.
- ADRs record approved consequential decisions.
- Chat history and proprietary agent memory are never authoritative.

Read [agent-context/README.md](agent-context/README.md) for the repository map,
reading order, and handoff commands.

## Startup

1. Inspect `git status`, the current branch, recent commits, and remotes. Do not
   assume a chat summary matches the checkout.
2. Verify `upstream` is `cadence-workflow/cadence-ai-samples`. Treat `origin` as
   the current contributor's fork; never assume a particular fork owner.
3. Read only the assigned Issue/PR, linked specification, relevant README, code,
   and tests. Do not read the whole repository by default.
4. Implement only an open Issue labeled `status:agent-ready`. If authorization
   begins in conversation, record it in an upstream Issue and wait for that label
   so the next agent can reconstruct the decision without chat.
5. Before editing, restate the problem, acceptance criteria, scope, non-goals,
   and decisions that require human authority.

## Issues and ownership

- Agents may create new upstream Issues without separate approval only when the
  Issue is labeled `status:needs-triage`.
- Every Issue and label command must explicitly target
  `cadence-workflow/cadence-ai-samples`; never infer the target from `origin`.
- Only a human may promote an Issue to `status:agent-ready`, approve architecture
  or priority, approve a PR, or merge.
- Any contributor may claim an unassigned `status:agent-ready` Issue. First check
  for an assignee, a claim comment, or an active linked PR, then comment intent
  before branching.
- Skip claimed work. Do not open competing implementation PRs without agreement.

## Implementation and validation

- Preserve recipe independence and copyability. Avoid shared integration layers
  and unrelated refactors.
- Keep nondeterministic operations and external calls in Cadence Activities.
- Keep credentials, proprietary data, internal discussions, and machine-specific
  settings out of the repository and GitHub.
- Preserve a mock or synthetic offline path where practical.
- Use existing source, READMEs, catalogs, and tests as runtime truth. Do not guess
  supported commands or combinations.
- Run focused tests first, then the documented affected suites. Report commands
  and actual outcomes; distinguish tested, demonstrated, untested, and merged.
- Do not run paid inference, secret-bearing Workers, destructive git commands, or
  external writes beyond the authorized scope.

## Git and pull requests

- Branches and pushes use the contributor's verified `origin` fork.
- PRs use the contributor fork as head and
  `cadence-workflow/cadence-ai-samples:main` as base.
- Sign every commit with DCO using lowercase `-s`.
- Never force-push, merge, mark ready, or self-approve unless the repository owner
  explicitly directs it. Every PR requires repository-owner approval.
- Link the Issue and include summary, spec/ADR impact, exact validation evidence,
  untested behavior, and known limitations.

## Documentation update triggers

- Requirement or acceptance change: update the applicable spec.
- New or moved canonical document: update the context map.
- Approved consequential design decision: add an ADR; do not invent historical
  rationale.
- Work status or priority change: update the Issue/PR, not a spec or duplicate
  TODO file.
- Run instructions or user-visible behavior change: update the relevant README.
- No trigger: do not churn context documents.

## Handoff

Before handing work to another machine or agent:

1. Preserve the branch and working tree; push only when authorized.
2. Open or update the draft PR and linked Issue.
3. Record changed paths, commits, actual tests/results, limitations, blockers, and
   exact next steps.
4. Update specs/ADRs only when their triggers apply.
5. Leave the next agent able to resume from a fresh checkout, this file, the
   context map, and the linked Issue/PR without conversational history.
