# Contribution workflow cheat sheet

Use these phrases as written. [AGENTS.md](AGENTS.md) is the canonical agent
contract; this page is its short interface. GitHub and Git, not chat or terminal
history, hold durable project state.

## Plan before building

Plan before claiming or editing. Use a dedicated planning mode when available;
otherwise explicitly prohibit repository and GitHub writes during planning.
Review assumptions, dependencies, scope, non-goals, validation, and authority
blockers before proceeding.

`Follow AGENTS.md. Read Issue #N and its linked specifications. Do not claim or
edit yet. Show me your implementation plan, validation, assumptions, and
blockers.`

For substantial work, see [Optional collaboration](#optional-collaboration).
If prior chat context is unavailable, see
[Recover current work without chat history](#recover-current-work-without-chat-history).

## From idea to merge

| Say | Expected behavior |
| --- | --- |
| `Capture this idea: ...` | Ask whether this is general idea intake or a defined implementation task. Keep general intake format-neutral; for a defined task, ask human-first or agent-first. Then follow the [permission-aware proposal path](agent-context/README.md#creating-a-proposal) to choose the matching form, use the authorized agent-first-only CLI path, or request maintainer help. Missing format or classification labels never authorize implementation. |
| `Find approved work.` | Inspect upstream open `status:ready` Issues and report claimable work. Do not claim anything. |
| `Proceed with Issue #N.` | Recheck the Issue's assignee, comments, labels, and active linked PRs, then follow the [claim path](CONTRIBUTING.md#claim-approved-work). Create `issue-N-short-slug` only after the Issue is `status:in-progress` and identifies the contributor as owner. |
| `Prepare a durable handoff.` | Post the local-completion evidence described below, re-read it, and verify its URL and `status:in-progress`. Do not commit, push, or open a PR unless separately authorized. |
| `Prepare this for commit.` | Verify the worktree and durable evidence, then follow the contributor's commit policy. If the contributor owns commits, provide the conventional commit message, remind them to create the commit with lowercase `-s`, and stop. |
| `Write the PR description.` | Return a ready-to-paste PR title and body only. Make no GitHub write. |
| `The branch is committed and pushed. Prepare the PR.` | Verify the signed commit and pushed branch, then create or update a **draft** PR from the contributor fork to upstream `main`. The root human-first template is the automatic default. Agents explicitly select `.github/PULL_REQUEST_TEMPLATE/agent-first.md` with `gh pr create --template`, or use the compare URL ending in `?expand=1&template=agent-first.md`. Finalize the body from committed Git and Issue evidence, link the Issue, and report each PR checklist item and current check result. |
| `Review PR #N and recommend approve or request changes.` | Read the current GitHub diff, discussion, checks, and requirements. Return or post evidence-based findings as requested. Agents cannot submit an approval. A non-owner PR needs one current human `cadence-dev` approval; an owner-authored PR may use the recorded owner review bypass. |
| `PR #N is approved. Merge it.` | Treat review and merge authority separately. Verify explicit repository-owner merge authorization, the applicable human review path, all required checks on a branch current with `main`, and resolved threads, then merge only if repository policy permits it. Otherwise report the remaining gate. |

### First-time fork workflow approval

A workflow run from a first-time outside contributor's fork may pause for
maintainer approval. The contributor waits rather than rerunning it, changing
workflow files to try to start it, or assuming that checks have begun. A
maintainer reviews the fork diff before clicking **Approve workflows to run**.

In the observed test, a corrected push to the same unmerged first-time pull
request paused again. Do not generalize that result to the account's behavior
after its first contribution merges. See
[Offline pull-request checks](CONTRIBUTING.md#offline-pull-request-checks) for
the local equivalents and scope. Required-check and branch-rule configuration
remains in [Issue #78](https://github.com/cadence-workflow/cadence-ai-samples/issues/78).

## Review and merge gates

A non-owner PR needs one approval from a human `cadence-dev` team member. New
commits dismiss prior approvals. First-time workflow approval starts a workflow
run but is not PR review approval.

For an owner-authored PR, `Bueller87` may use the pull-request-only review bypass
without self-review and must record its use in the PR. GitHub technically permits
that bypass on other PRs, but repository policy does not. The bypass affects only
the review requirement: DCO, `Scripts and profile contracts`, `Explorer Python`,
current-with-`main`, and resolved-thread gates still apply through a separate
ruleset with no bypass actors.

Only the repository owner authorizes a merge. Agents cannot approve PRs or
supply merge authority. Routine direct pushes and check bypasses are prohibited.
For a real incident, the owner records the reason, before/after rules, and
restoration in an Issue.

## Local implementation completion

Before saying implementation is `done`, `ready for review`, or `ready to
commit`, post a comment to the claimed Issue containing:

- branch and changed paths;
- exact validation commands and actual results;
- untested behavior, limitations, and blockers;
- working-tree, commit, and push state; and
- the exact next step.

Re-read the Issue, open the comment URL, and verify `status:in-progress`.
“Ready” is false until this evidence exists. This checkpoint comes before commit
or PR handoff and applies whether the contributor or an authorized agent creates
the required DCO-signed commit.

## Optional collaboration

For substantial work, an optional two-agent pattern separates coordination from
implementation:

- A coordination agent maintains cross-Issue context, grooms work, reviews plans
  and evidence, and prepares durable handoffs.
- A fresh implementation agent handles one approved Issue.

Exactly one writing implementation agent owns an Issue. Additional agents may
perform read-only planning or review, but they do not share implementation
ownership or write competing changes. This separation keeps implementation
context focused, provides early independent pushback, reduces scope drift, and
makes either role replaceable from GitHub and Git state. It does not require a
particular coding tool.

## Recover current work without chat history

Use this read-only prompt:

`Follow AGENTS.md and agent-context/README.md. Inspect Git, GitHub, and the
current checkout. Find work assigned to or otherwise owned by this contributor,
matching in-progress branches, and open PRs. Report the authoritative current
state and next action. Do not edit anything.`

Inspect the checkout and remotes, upstream Issue ownership and claim records,
matching `issue-N-*` branches, and open PRs. Report conflicts or one authoritative
next action without changing repository or GitHub state. Chat history and agent
memory are not recovery sources.

## Resume on another computer

Resume from the upstream Issue, pushed branch, draft PR, and repository documents.
Never depend on pasted chat or terminal output. Record decisions, ownership,
local evidence, limitations, and next steps in the Issue; committed work in the
branch; and review and merge evidence in the PR.

Label environment-specific instructions **Mac** or **Windows**. Record access
constraints in the Issue without credentials, private endpoints, proprietary
data, or machine-specific configuration.

## Human authority

Only a human may approve architecture or priority, add `status:ready`, or
approve a PR. Only the repository owner may authorize a merge. Agents never
approve PRs or supply merge authority. An agent may execute a merge only after
explicit repository-owner direction and verification of the applicable review
path, required checks, branch currency, and resolved threads; without
authorization, report the remaining gate and stop. Keep the Issue
`status:in-progress` through local completion and draft-PR handoff; merge closes
it.
