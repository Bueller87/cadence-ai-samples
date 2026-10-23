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
reading order, and handoff commands. Use the
[contribution workflow cheat sheet](WORKFLOW.md) for short intent phrases;
this file remains authoritative. Human contributors start with the
[first-time contributor path](CONTRIBUTING.md#first-time-contributor-path).

## Startup

1. Inspect `git status`, the current branch, recent commits, and remotes. Do not
   assume a chat summary matches the checkout.
2. Verify `upstream` is `cadence-workflow/cadence-ai-samples`. Treat `origin` as
   the current contributor's fork; never assume a particular fork owner.
3. Read only the assigned Issue/PR, linked specification, relevant README, code,
   and tests. Do not read the whole repository by default.
4. Claim new work only from an open Issue labeled `status:ready`. Begin or
   resume implementation only after the Issue is `status:in-progress`, its claim
   record or assignee identifies the contributor whose fork and checkout are in
   use, and a matching local or remote `issue-N-short-slug` branch exists. If
   authorization begins in conversation, record it in an upstream Issue and wait
   for `status:ready` so the next agent can reconstruct the decision
   without chat.
5. Before editing, restate the problem, acceptance criteria, scope, non-goals,
   and decisions that require human authority.

## Issues and ownership

- Agents may create new upstream Issues without separate approval only when the
  resulting Issue is labeled `status:needs-triage`. Follow the
  [permission-aware proposal path](agent-context/README.md#creating-a-proposal).
- An agent without upstream label permission prepares the proposal for human
  Issue-form submission or requests maintainer assistance. It must not run a
  labeled Issue command it cannot authorize.
- General idea intake is format-neutral. Defined implementation tasks may use
  human-first or agent-first forms. The labeled CLI proposal path is
  agent-first only.
- `status:needs-triage` is proposed work, not approved work. Never self-assign,
  claim, or implement it. Missing type, area, source, or other classification
  labels never authorize implementation; maintainer triage applies labels the
  proposer cannot.
- `format:human-first` and `format:agent-first` describe how an Issue is written,
  not who may claim it. Either format may be claimed by a human or implemented
  with an agent. A missing format label never authorizes implementation.
- Every Issue and label command must explicitly target
  `cadence-workflow/cadence-ai-samples`; never infer the target from `origin`.
- Only a human may promote an Issue to `status:ready`, approve architecture
  or priority, approve a PR, or authorize a merge.
- Any contributor may claim an unassigned `status:ready` Issue. Immediately
  before claiming, recheck its assignee, comments, labels, and active linked PRs.
- Assignment records ownership of approved work, not interest or priority.
- Claim in this order: comment intent, remove `status:ready`, add
  `status:in-progress`, then create the branch.
- Contributors without upstream label or assignment permission must comment to
  request a claim, then wait. Do not create the branch or begin implementation
  until the Issue has `status:in-progress` and identifies the contributor as
  owner through assignment or a maintainer confirmation comment.
- Skip work assigned to or claimed by another contributor, including work with a
  linked PR or `status:in-progress`. Resume only the current contributor's
  matching claimed Issue and branch. Do not open competing implementation PRs
  without agreement.
- If abandoning work, comment the reason and current state, remove
  `status:in-progress`, and restore `status:ready`. Preserve or delete the
  branch only with human direction.
- Keep `status:in-progress` through implementation and draft-PR handoff. Merge
  closes the Issue.

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

## Local implementation completion

Before reporting implementation done, ready for review, or ready to commit, post
a durable comment to the claimed Issue containing:

- the branch and changed paths;
- exact validation commands and actual results;
- untested behavior, limitations, and blockers;
- working-tree, commit, and push state; and
- the exact next step.

Re-read the Issue, open the comment URL, and verify that the Issue remains
`status:in-progress` before reporting completion. This checkpoint is required
before a commit or PR exists and applies whether the contributor or an authorized
agent creates the commit. Chat and terminal output do not satisfy it.

## Git and pull requests

- Branches and pushes use the contributor's verified `origin` fork.
- Name implementation branches `issue-N-short-slug`. Do not include agent or tool
  names such as `codex`, `cursor`, or `claude`; work may move between agents.
- PRs use the contributor fork as head and
  `cadence-workflow/cadence-ai-samples:main` as base.
- The root PR template is the automatic human-first default. Agents and other
  contributors choosing the detailed agent-first format explicitly pass
  `--template .github/PULL_REQUEST_TEMPLATE/agent-first.md` to `gh pr create`,
  or open
  `https://github.com/cadence-workflow/cadence-ai-samples/compare/main...<owner>:<branch>?expand=1&template=agent-first.md`.
- Sign every commit with DCO using lowercase `-s`.
- A non-owner PR requires one approval from a human member of the `cadence-dev`
  team. New commits dismiss prior approvals, so approval must cover the current
  head. Workflow-run approval for a first-time contributor is separate and does
  not count as PR approval.
- The repository owner, `Bueller87`, may use the pull-request-only review bypass
  on an owner-authored PR without self-review. GitHub technically makes this
  bypass available on other PRs, but repository policy forbids that use. Record
  every permitted use in the PR.
- The owner review bypass does not bypass DCO, required CI, the requirement that
  the branch be current with `main`, or unresolved review threads. Those gates
  are enforced in a separate ruleset with no bypass actors.
- Follow the contributor's declared commit policy. Do not create a commit unless
  authorized. When the contributor owns commits, provide the conventional commit
  message, remind them to create the commit with lowercase `-s`, and stop.
- `Write the PR description` returns text only and authorizes no GitHub write.
- `The branch is committed and pushed. Prepare the PR` requires verification of
  the committed and pushed state before creating or updating a draft PR.
- Never force-push or mark a PR ready unless the repository owner explicitly
  directs it. A human review approval and repository-owner merge authorization
  are separate decisions. Agents never approve PRs or supply merge authority. An
  agent may execute a merge only after explicit repository-owner direction and
  verification of the applicable review path, required checks, branch currency,
  and resolved threads; without merge authorization, report the remaining gate
  and stop.
- Link the Issue and include summary, spec/ADR impact, exact validation evidence,
  untested behavior, and known limitations.

Routine direct pushes to `main` and required-check bypasses are prohibited. For
a real incident, the repository owner may change a ruleset only after recording
the reason and current state in an Issue, and must record the before/after
configuration and restoration there.

## Documentation update triggers

- Requirement or acceptance change: update the applicable spec.
- New or moved canonical document: update the context map.
- Approved consequential design decision: add an ADR; do not invent historical
  rationale.
- Work status or priority change: update the Issue/PR, not a spec or duplicate
  TODO file.
- Run instructions or user-visible behavior change: update the relevant README.
- No trigger: do not churn context documents.

## Post-push PR handoff

After the signed commit is pushed:

1. Verify the committed branch is pushed to the contributor's `origin`.
2. Open or update the draft PR and link the Issue.
3. Finalize the PR description from committed Git and the durable Issue evidence
   so its commit, branch, checklist, and validation state are current.
4. Record commits, actual tests/results, limitations, blockers, and exact next
   steps.
5. Update specs/ADRs only when their triggers apply.
6. Leave the next agent able to resume from a fresh checkout, this file, the
   context map, and the linked Issue/PR without conversational history.
