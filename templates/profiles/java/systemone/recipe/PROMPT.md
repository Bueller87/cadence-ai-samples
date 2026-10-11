# Synthetic request-classification data prompt

Create exactly 40 fully synthetic requests as JSON Lines. Use IDs
`synthetic-0001` through `synthetic-0040`, with 20 `routine` and 20 `priority`
labels:

```json
{"request_id":"synthetic-0001","text":"Invented request.","expected_label":"routine"}
```

Vary wording, capitalization, and length. Include invented blocked work,
outages, security concerns, ordinary questions, and ambiguous boundary cases.
Do not include real people, organizations, incidents, credentials, secrets,
proprietary material, or copied production requests. Return only JSONL.
