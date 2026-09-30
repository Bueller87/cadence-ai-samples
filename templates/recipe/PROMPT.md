# Synthetic test data prompt

Use this optional prompt with a generative model. Replace every bracketed placeholder before use.

---

Create realistic **synthetic** test data for the following business scenario:

## Business scenario

[Describe the domain, actors, process, and purpose of the test data.]

## Input format and JSON schema

[Describe the input format and provide the complete JSON schema or equivalent field definitions.]

```json
{
  "placeholder": "[type, constraints, and meaning]"
}
```

## Number of records

[Specify the exact record count.]

## Required categories and distributions

[List categories, proportions, ranges, correlations, and any balancing requirements.]

## Edge cases

[List malformed, ambiguous, boundary, rare, and failure-path cases to include.]

## Expected outcomes or ground-truth labels

[Define the expected result fields or labels and explain how they should be derived.]

## Output filename and format

- **Filename:** `[fixture filename]`
- **Format:** [JSON, JSON Lines (JSONL), CSV, or another specified format]

Return only the requested data in the specified format unless validation notes are explicitly requested.

Do not use or reproduce real customer information, personal data, credentials, API keys, secrets, proprietary material, or other sensitive data. Invent every name, identifier, organization, event, and value. Make generated identifiers obviously fake so they cannot be mistaken for working credentials or real account details.
