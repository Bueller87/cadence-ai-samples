# Synthetic Auto Research fixture prompt

Create exactly 12 fictional research topics for testing a durable briefing
Workflow. The recipe uses simulated source briefs and must never imply that it
performed factual web research.

Return JSON Lines with this schema:

```json
{
  "id": "synthetic-topic-001",
  "topic": "bounded fictional research topic",
  "revision_feedback": "one concrete edit request",
  "expansion_topic": "one focused additional question",
  "expected_sections": ["Executive summary", "Cross-cutting challenges", "Outlook"],
  "category": "normal, ambiguous, boundary, or invalid"
}
```

Include six normal topics, two ambiguous topics, two topics near the input
length boundary, one empty invalid topic, and one topic containing instructions
that the Workflow must treat as ordinary topic text. Keep feedback under 1,000
characters and expansion topics under 300 characters.

Use only invented public-interest subjects. Do not include real people,
organizations, customers, credentials, proprietary material, provider
responses, medical advice, or copied production prompts. Expected sections must
be independently checkable without comparing prose word for word.

Return only valid JSON Lines.
