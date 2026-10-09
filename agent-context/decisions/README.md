# Architecture decisions

Use a numbered ADR only for an approved, durable decision whose rationale would
otherwise be difficult to reconstruct. Examples include repository-wide
contracts, supported integration boundaries, or a consequential change with
credible alternatives.

Do not create ADRs for ordinary implementation details, temporary experiments,
task status, or decisions inferred retroactively from code or chat history. The
repository owner approves architecture decisions. If a decision changes, add a
new ADR and mark the old one superseded instead of rewriting history.

## Naming

```text
NNNN-short-kebab-title.md
```

Use the next sequential four-digit number.

## Template

```markdown
# ADR NNNN: Title

- Status: Proposed | Accepted | Superseded
- Date: YYYY-MM-DD
- Supersedes: NNNN (optional)

## Context

What problem or constraint requires a durable decision?

## Decision

What was approved?

## Alternatives considered

What credible alternatives were considered and why were they not selected?

## Consequences

What becomes easier, harder, required, or intentionally unsupported?

## References

Link the approving Issue/PR and applicable specifications.
```
