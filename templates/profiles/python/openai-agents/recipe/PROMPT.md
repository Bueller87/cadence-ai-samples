# Synthetic agent test data prompt

Use this optional prompt to create offline fixtures for the recipe. Replace
every bracketed field before use. Do not use the output as a live-mode
acceptance test.

---

Create exactly **[record count]** synthetic requests for this business scenario:

[Describe the fictional users, domain, agent task, constraints, and desired
outcome.]

Return JSON Lines with this schema:

```json
{
  "id": "obviously fake stable identifier",
  "request": "text supplied to the agent",
  "expected_mock_answer": "deterministic offline expectation",
  "expected_topics": ["facts a valid live answer should address"],
  "category": "normal, ambiguous, boundary, or invalid"
}
```

Include:

- [distribution of normal cases]
- [ambiguous cases that require bounded agent reasoning]
- [empty, malformed, or policy-rejected cases]
- [business-specific boundary cases]

Make every expected outcome independently checkable without comparing prose
word for word. Do not include real people, organizations, customer data,
credentials, API keys, proprietary material, provider responses, or copied
production prompts. Invent all names and identifiers.

Return only valid JSON Lines.
