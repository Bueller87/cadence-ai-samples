# Synthetic test data prompt

Use this optional prompt to create another fictional incident with the same
fixture contract. Generated data must be reviewed before it replaces the
checked-in deterministic fixture.

---

Create realistic synthetic test data for an on-call incident-triage agent.

## Business scenario

A fictional online checkout request returns HTTP 500. The agent starts at an
API gateway, reads service logs, follows downstream errors through two or three
services, encounters one plausible but misleading warning, and eventually
finds one clear root cause. A recent fictional deployment may be relevant.

## Required files

Return these files as separate fenced blocks labeled with their relative paths:

- `trace.json`: one object with `endpoint_id`, `service_name`, `trace_id`,
  numeric `status`, `at`, and `synthetic: true`.
- `graph.json`: one object with `endpoint`, `service`, `max_depth`, nested
  `tree`, and an `ignore` list.
- `context.json`: a note, recent fictional deploys, and one resource trend that
  confirms the true root cause. Runtime code will not read this file.
- `logs/<service>.log`: one line per event using space-delimited `key=value`
  fields. Quote message values. Every relevant line must include the same fake
  trace ID and level `WARN` or `ERROR`.

## Required behavior

- The gateway names one upstream service.
- The first application service has one misleading `WARN` toward a wrong branch
  and one `ERROR` toward the correct branch.
- Following the wrong branch cannot reveal the true root cause.
- Following the correct branch reaches one final dependency whose error clearly
  explains the request failure.
- The graph and logs must agree on every service name and edge.
- Use five to seven fictional services and no more than twelve log lines.

## Expected outcomes

After the files, return a small JSON validation object with:

```json
{
  "wrong_branch": ["service names in order"],
  "correct_path": ["service names in order"],
  "root_cause": "one precise sentence",
  "misleading_evidence": "the exact warning message"
}
```

Do not use real customer information, people, companies, incidents, credentials,
API keys, internal hostnames, private URLs, proprietary material, or production
claims. Invent every identifier and value. The trace ID must be obviously fake
and must not resemble a credential.
