# ADR 0002: Deterministic Issue claims

- Status: Accepted
- Date: 2026-11-07

## Context

Free-form claim comments do not select a durable winner when multiple
contributors observe the same unassigned `status:ready` Issue. GitHub Actions
concurrency serializes mutations but is not FIFO and can replace a pending run.
Outside contributors may not be assignable, so assignment cannot be required
for ownership.

## Decision

- An exact `/claim` Issue comment requests any open, claimable `status:ready`
  Issue.
- Each surviving per-Issue workflow run rescans authoritative comments and the
  current readiness window, then selects the earliest eligible comment by
  GitHub creation time and immutable comment ID. Queue or delivery order cannot
  choose a different winner.
- Durable ownership is the winning immutable comment plus the successful
  `status:in-progress` transition. A confirmation comment is retryable
  feedback, not part of ownership. If feedback fails after the transition, a
  rerun or later `/claim` delivery retries feedback only.
- Release remains the existing maintainer-authorized manual abandonment path.
  A new `status:ready` event starts a fresh claim window. There is no
  `/release`, automatic assignment, branch creation, or cross-Issue claimant
  limit.
- The workflow fails closed and leaves the manual coordinator path available
  when its GitHub data, permissions, or Actions execution cannot establish
  eligibility safely.

## Alternatives considered

- **Free-form/manual-only claims.** Rejected because they leave simultaneous
  claimers without a deterministic winner.
- **Always-on lifecycle guard.** Rejected because lifecycle repair and claim
  ownership are separate concerns; the existing audit stays read-only.
- **GitHub Project field.** Rejected because it adds a second ownership state
  outside the authoritative Issue record.
- **Hosted GitHub App.** Rejected because a repository workflow using the
  scoped `GITHUB_TOKEN` meets this bounded need without another service.

## Consequences

- Contributors wait for `status:in-progress` and the winning `/claim` comment
  before creating a branch.
- API failures and Actions outages require a maintainer to use the documented
  manual recheck-and-transition path.
- The workflow must preserve non-lifecycle labels, write no unrelated Issue
  state, and keep feedback idempotent across delayed or replayed deliveries.

## Known limitations

- REST timeline `connected` events do not expose the cross-reference source
  object needed to determine whether they represent an active implementation
  pull request. The workflow fails closed to the manual path for such an event.
  A read-only audit found no opaque connected events on current `status:ready`
  Issues. This decision does not add GraphQL or require a redesign.

## References

- [Issue #171](https://github.com/cadence-workflow/cadence-ai-samples/issues/171)
- [ADR 0001: Explicit recipe template profiles](0001-recipe-template-profiles.md)
