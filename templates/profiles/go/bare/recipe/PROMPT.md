# Synthetic test data prompt

Use this optional prompt with a generative model after replacing every
instruction in brackets. The starter does not require generated data.

---

Create realistic **synthetic** test data for this business scenario:

## Business scenario

[Describe the domain, actors, process, and purpose of the test data.]

## Input format and schema

[Describe the input format and provide complete Go field definitions or a JSON
schema.]

```json
{
  "text": "[non-empty synthetic request text]"
}
```

## Number of records

[Specify the exact record count.]

## Required categories and distributions

[List categories, proportions, ranges, correlations, and balancing
requirements.]

## Edge cases

[List malformed, ambiguous, boundary, rare, and failure-path cases. Include
empty or whitespace-only text if input validation is under test.]

## Expected outcomes or ground-truth labels

[Define expected result fields and explain how each is derived.]

## Output filename and format

- **Filename:** `[fixture filename]`
- **Format:** [JSON, JSON Lines, CSV, or another specified format]

Return only the requested data unless validation notes are explicitly
requested.

Do not use or reproduce real customer information, personal data, credentials,
API keys, secrets, proprietary material, or other sensitive data. Invent every
name, identifier, organization, event, and value. Make generated identifiers
obviously fake so they cannot be mistaken for working credentials or real
account details.
