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
| `Capture this idea: ...` | Follow the [permission-aware proposal path](agent-context/README.md#creating-a-proposal) to create or update an upstream `status:needs-triage` Issue, prepare its web-form submission, or request maintainer help. Missing classification labels never authorize implementation. |
| `Find approved work.` | Inspect upstream open `status:agent-ready` Issues and report claimable work. Do not claim anything. |
| `Proceed with Issue #N.` | Recheck the Issue's assignee, comments, labels, and active linked PRs, then follow the [claim path](CONTRIBUTING.md#claim-approved-work). Create `issue-N-short-slug` only after the Issue is `status:in-progress` and identifies the contributor as owner. |
| `Prepare a durable handoff.` | Post the local-completion evidence described below, re-read it, and verify its URL and `status:in-progress`. Do not commit, push, or open a PR unless separately authorized. |
| `Prepare this for commit.` | Verify the worktree and durable evidence, then follow the contributor's commit policy. If the contributor owns commits, provide the conventional commit message, remind them to create the commit with lowercase `-s`, and stop. |
| `Write the PR description.` | Return a ready-to-paste PR title and body only. Make no GitHub write. |
| `The branch is committed and pushed. Prepare the PR.` | Verify the signed commit and pushed branch, then create or update a **draft** PR from the contributor fork to upstream `main`. Finalize its body from committed Git and Issue evidence, link the Issue, and report each PR checklist item and current check result. |
| `Review PR #N and recommend approve or request changes.` | Read the current GitHub diff, discussion, checks, and requirements. Return or post evidence-based findings as requested. Only the repository owner supplies official approval. |
| `PR #N is approved. Merge it.` | Verify explicit merge authorization, repository-owner approval, and required checks, then merge only if repository policy permits it. Otherwise report the remaining gate. |

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

Only a human may approve architecture or priority, add `status:agent-ready`,
approve a PR, or authorize a merge. Agents never self-approve. An agent may
execute a merge only after explicit repository-owner direction and verification
of required approval and checks; without authorization, report the remaining
gate and stop. Keep the Issue `status:in-progress` through local completion and
draft-PR handoff; merge closes it.
