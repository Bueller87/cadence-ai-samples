# Synthetic request-classification data prompt

Use this optional prompt to create offline fixtures after choosing the business
taxonomy for this recipe. Do not use live customer data.

---

Create exactly 40 realistic but fully synthetic requests for a workflow that
classifies each request into one of these labels:

- `routine`: normal handling without urgent operational or security impact
- `priority`: urgent, blocked, outage-related, or security-sensitive

Return JSON Lines in `testdata/requests.jsonl`. Each line must have this shape:

```json
{"request_id":"synthetic-0001","text":"Invented request text.","expected_label":"routine"}
```

Requirements:

- Produce 20 `routine` and 20 `priority` records.
- Use unique IDs from `synthetic-0001` through `synthetic-0040`.
- Include varied wording, capitalization, and message length.
- Include ambiguous language near the category boundary, but assign exactly one
  expected label.
- Include at least five requests each about blocked work, outages, security,
  and ordinary informational questions.
- Do not include empty text or malformed JSON in the main fixture. List those
  as separate validation cases after the JSONL only if requested.
- Invent all people, organizations, systems, identifiers, and events.

Do not include personal data, credentials, API keys, proprietary information,
real incidents, or text copied from actual support requests.

Return only the JSONL records.
