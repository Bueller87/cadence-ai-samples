# Synthetic StreamWave ticket prompt

Use this prompt with an AI coding assistant to generate additional synthetic customer-support requests for the StreamWave ticket-routing recipe. Replace bracketed values before use.

---

Generate exactly **[record count]** realistic, fully synthetic customer-support requests for StreamWave, a fictional streaming service.

Return JSON Lines (JSONL): one complete JSON object per line, with no Markdown fences, commentary, blank lines, or surrounding array. Use this exact schema:

```json
{"ticket_id":"synthetic-[unique sequential ID]","message":"[customer request]","expected_department":"billing|technical|account|content","expected_priority":"low|normal|high|critical","expected_complexity":"tier1|tier2|tier3"}
```

The runtime application consumes `ticket_id` and `message`. The three `expected_*` fields are test-fixture annotations only.

## Classification labels

Choose exactly one value for each label:

- `expected_department`
  - `billing`: subscription charges, payments, invoices, refunds, or billing disputes.
  - `technical`: playback failures, buffering, application crashes, device errors, downloads, casting, or service outages.
  - `account`: sign-in, passwords, profiles, email changes, account access, security, or account management.
  - `content`: catalog availability, missing episodes, subtitles, captions, audio tracks, regional availability, or content metadata.
- `expected_priority`
  - `low`: minor question, suggestion, or inconvenience without time pressure.
  - `normal`: ordinary support request affecting normal use without severe or urgent impact.
  - `high`: major loss of functionality, repeated financial issue, or time-sensitive access problem.
  - `critical`: widespread outage, credible account-security incident, severe repeated charging, or similarly urgent impact. Do not equate angry wording alone with critical priority.
- `expected_complexity`
  - `tier1`: common issue with a straightforward first-line response or standard troubleshooting.
  - `tier2`: issue requiring investigation across several facts, settings, transactions, devices, or intents.
  - `tier3`: unusual, systemic, security-sensitive, or technically complex investigation.

## Required coverage

- Distribute records across all four departments. Unless another distribution is provided, keep the department counts approximately balanced.
- Include billing questions, technical support requests, account-management requests, and content-related questions.
- Make roughly 15–25% of requests ambiguous, incomplete, unusual, or multi-intent. Still assign the single department that should own the primary issue.
- Vary priority and complexity while keeping the labels plausible. Include all priority and complexity values, but keep `critical` uncommon.
- Mix writing styles and lengths: terse fragments, ordinary sentences, detailed descriptions, polite questions, frustrated messages, casual mobile text, and messages from less-technical users.
- Include realistic misspellings, omitted punctuation, shorthand, and incomplete information in some—not all—messages.
- Avoid repetitive templates and obvious label words in every message. Make the classification depend on meaning, not only keywords.
- Keep every `ticket_id` unique within the output and distinct from IDs already present in the target fixture.

## Safety and provenance

Invent all requests and identifiers. Do not use or imitate real customer records, names, email addresses, account numbers, payment-card details, credentials, API keys, proprietary documentation, or non-public company information.

The expected labels are **proposed ground truth**, not independently verified truth. A human familiar with the classification policy must review and approve them before the data is used for classification-accuracy benchmarks, model comparisons, or quality claims. Ambiguous and multi-intent examples deserve particular review.

Before returning the data, silently verify that every line is valid JSON, every ID is unique, every label is from the allowed sets, and the output contains exactly **[record count]** records.
