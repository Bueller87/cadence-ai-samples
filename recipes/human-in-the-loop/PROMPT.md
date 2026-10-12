# Synthetic ride-request test data prompt

Use this optional prompt to create offline fixtures. Do not use its output as
live-mode evidence.

---

Create exactly 20 fictional ride requests. Each request asks an assistant to
propose one ride with a pickup address, destination address, and passenger
count. No request represents a real booking or contains real customer data.

Return JSON Lines with this schema:

```json
{
  "id": "obviously fake stable identifier",
  "request": "text supplied to the agent",
  "expected_trip": {
    "from_address": "invented pickup",
    "to_address": "invented destination",
    "passengers": 1
  },
  "expected_decision": "approve or reject",
  "category": "normal, ambiguous, boundary, or invalid"
}
```

Include:

- 10 ordinary requests with all trip details.
- 4 ambiguous requests with one missing trip detail.
- 3 invalid passenger counts or empty addresses.
- 3 valid requests whose expected human decision is rejection.

Make each expected outcome independently checkable. Do not include real people,
organizations, customer data, credentials, API keys, proprietary material,
provider responses, or copied production prompts.

Return only valid JSON Lines.
