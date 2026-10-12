# Contribution workflow cheat sheet

Use these phrases as written. [AGENTS.md](AGENTS.md) is the canonical agent
contract; this page is its short interface. GitHub and Git, not chat or terminal
history, hold durable project state.

## From idea to merge

| Say | Expected behavior |
| --- | --- |
| `Capture this idea: ...` | Create or update an upstream `status:needs-triage` Issue with the problem, scope, non-goals, acceptance criteria, validation approach, and spec path. Do not implement it or mark it agent-ready. |
| `Find approved work.` | Inspect upstream open `status:agent-ready` Issues and report claimable work. Do not claim anything. |
| `Proceed with Issue #N.` | Recheck the Issue's assignee, comments, labels, and active linked PRs. If it remains available, comment intent, replace `status:agent-ready` with `status:in-progress`, then create `issue-N-short-slug`. |
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
