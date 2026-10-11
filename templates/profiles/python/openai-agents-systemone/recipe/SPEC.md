# __RECIPE_TITLE__ specification

## Profile

- Language: Python 3.12
- Classifier: SystemOne
- Agent: OpenAI Agents
- Domain: `cadence-ai-samples`
- Task list: `__RECIPE_SLUG__-openai-agents-systemone`

## Behavior

Mock mode classifies deterministic synthetic text and returns a synthetic agent
response. Live mode loads `classifiers.yaml` and `models.yaml` only in the
Worker, validates selected providers, endpoints, and credentials, and
constructs all clients there. The starter reads no catalog. Workflow input
contains only business text and execution mode, with no provider, model,
endpoint, or credential configuration.
Both Worker and start commands require an explicit `--mode mock|live`.

The Workflow calls SystemOne and a stable OpenAI Agents Activity. The
Worker-registered agent Activity owns the model selection and invokes OpenAI
Agents. Invalid catalog
entries, unsafe URLs, malformed classifier responses, and duplicate open
Workflow IDs fail with clear errors. The stable demo ID explicitly uses
`ALLOW_DUPLICATE` for reuse after a run closes.

## Acceptance

- Mock execution reads no catalog or credential.
- Catalog selections are the sole provider/model/endpoint source.
- Endpoint mutation reaches a loopback test transport without a live provider.
- External calls remain in Activities.
- The recipe imports no other recipe or profile.
