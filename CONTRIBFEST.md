# Cadence Workflow ContribFest

Join Cadence maintainers at **Cadence Workflow ContribFest: Build Durable AI
Agent Examples** during KubeCon + CloudNativeCon North America 2026.

- **When:** Wednesday, November 11, 2026, 2:20–3:40 PM MST (80 minutes)
- **Where:** Salt Palace, Level 2, room 255 D, Salt Lake City, Utah
- **Event details:** [official KubeCon + CloudNativeCon North America
  schedule](https://events.linuxfoundation.org/kubecon-cloudnativecon-north-america/program/schedule/)
- **Event work:** [ContribFest
  milestone](https://github.com/cadence-workflow/cadence-ai-samples/milestone/2)

The goal is useful, reviewable progress with enough evidence for someone to
continue safely. A signed draft pull request is welcome, but a durable handoff
is also a successful outcome. Not every contribution will merge during the
session.

## Participant guide

### 1. Choose a workflow problem

Each event Issue starts with a common AI workflow failure and asks you to add or
demonstrate the Cadence protection for it. Examples include:

| Common failure | Cadence protection |
| --- | --- |
| A provider call fails temporarily or permanently | Bounded retries and non-retryable errors |
| Human approval never arrives or targets the wrong request | Signals, durable timers, and queryable state |
| A tool performs an external action but loses its response | Stable idempotency keys and activity retry boundaries |
| Long-running work stalls partway through | Heartbeats and progress recovery |
| A Worker restarts after completing part of the work | Event history and deterministic replay |
| A concurrent task fails or an agent loop never stops | Explicit partial-failure policies and hard bounds |

The Issue gives you the scaffold, injected failure, files to change, exact
validation, and visible proof. Choose a difficulty and failure mode that fit
your experience and the time remaining. Coding agents are optional.

Prepare only the local services named by your Issue:

| Service | Use | Fast setup |
| --- | --- | --- |
| Cadence | Run and inspect the workflow | [Ticket Routing requirements](recipes/ticket-routing/README.md#requirements) |
| Laya | Local classification | [Install Laya](README.md#install-laya) |
| Ollama with `llama3.2` | Local model inference | [Install Ollama and llama3.2](README.md#install-ollama-and-llama32) |
| Full local example | Exercise Laya and `llama3.2` together | [Recurring AI Watch setup](recipes/recurring-ai-watch/README.md#setup) |

The selected Issue and recipe README are authoritative because language
versions, ports, and validation commands vary. Event candidates do not require
paid inference or provider credentials.

### 2. Claim one Issue

Choose an open, unassigned candidate from the organizer-published event pool.
Candidates use `event:contribfest`, the event milestone, and one setup lane.
Those markers group event work; the `status:*` label records whether work is
available or claimed.

Use the [first-time contributor
path](CONTRIBUTING.md#first-time-contributor-path) and its [approved-work claim
instructions](CONTRIBUTING.md#claim-approved-work). During the event, the claim
coordinator applies that workflow:

1. You choose an open, unassigned `status:agent-ready` candidate.
2. You post the documented claim request with your GitHub username and proposed
   `issue-N-short-slug` branch.
3. The coordinator rechecks assignees, comments, labels, and active linked pull
   requests.
4. The coordinator records ownership and changes `status:agent-ready` to
   `status:in-progress`.
5. Only then do you create the branch on your fork and begin work.

There is no informal event claim. If two people want the same Issue, wait for
the coordinator's ownership record rather than creating competing branches.

### 3. Plan, work, and validate

Use [Plan before building](WORKFLOW.md#plan-before-building), the
[contribution workflow](CONTRIBUTING.md), and, if applicable, the [coding-agent
contract](AGENTS.md).

- Humans working alone and humans using coding agents follow the same Issue
  scope, non-goals, and validation contract. Coding agents are optional.
- One writing contributor or agent owns an Issue at a time. Other helpers may
  investigate or review without creating competing edits.
- The human contributor owns decisions and DCO-signed commits.
- Run the candidate's focused offline checks before requesting review.
- Approval to start a first-time contributor's GitHub Actions workflow is not
  pull-request approval. Pull-request approval is not merge authorization.
  Architecture, priority, pull-request approval, and merge authorization stay
  with the designated humans.

Ask the in-room team for help first. The facilitator will publish the event
Slack channel and escalation format before the session; room staff route
overflow questions to the remote support pool so participants do not have to
coordinate that queue.

### 4. Finish or hand off

Choose the safe outcome that fits the remaining time:

- **Draft pull request:** create a DCO-signed commit on your fork branch, push
  it, and open a draft pull request with exact validation evidence.
- **Durable Issue handoff:** comment on the claimed Issue with the fork and
  branch, changed paths, exact commands and results, untested behavior,
  limitations, blockers, working-tree/commit/push state, exact next step, and
  next owner if known.

Unfinished work is expected. Do not rush an unsafe commit or pull request to
meet the session clock. Keep the Issue `status:in-progress` until merge, or use
the normal abandonment workflow only after preserving the current state.

## Troubleshooting

- **A local service is not ready:** Re-run the health or minimal-prompt check
  linked in the setup table. Share the command and exact output with event
  staff. Do not paste credentials.
- **Setup is consuming the work session:** Ask for a candidate with fewer local
  requirements. A smaller completed proof is better than spending the session
  debugging one machine.
- **You cannot claim an Issue:** Post the documented claim request and ask event
  staff to record ownership and update the lifecycle label.
- **GitHub Actions is waiting for approval:** Stop and ask a maintainer to
  review the fork changes. Workflow-run approval is not pull-request approval.
- **A validation command fails:** Preserve the command, complete output, current
  branch, and changed paths. That evidence is still a useful handoff.
- **Internet access is unreliable:** Keep working from the prepared checkout
  and Issue notes. Do not create a competing claim; coordinate ownership with
  event staff before pushing when access returns.
