# Human In The Loop validation results

These results are historical evidence from the checkout on October 11, 2026.
They do not replace the current tests or define runtime support.

## Environment

- macOS
- Python 3.13.5
- Ollama 0.34.4
- Docker Engine 29.8.0
- Cadence domain `cadence-ai-samples`
- Model selection `llama3.2-local`
- Catalog model `llama3.2:latest`

No remote provider, API key, real booking service, or paid inference was used.

## Offline and local Cadence

- Generated baseline: 19 tests passed before recipe-specific changes.
- Completed recipe: 23 tests passed.
- Generator contract: 15 tests passed.
- Explorer: 26 tests passed.
- Local Cadence approval:
  - Workflow ID
    `hitl-local-checks-2c003167c2004e21a03b6bafa6629b3d-approved`
  - Run ID `764d9c13-baf8-4c74-97f2-f51f766cf461`
  - The Worker stopped while pending, a replacement Worker reconstructed the
    same call, the proposal Activity completed once, and booking completed once.
- Local Cadence rejection:
  - Workflow ID
    `hitl-local-checks-2c003167c2004e21a03b6bafa6629b3d-rejected`
  - Run ID `d6190d3e-f90b-4a13-a278-59c4e80ae587`
  - Unknown and conflicting decisions did not change the first valid decision.
    The proposal Activity completed once, and booking was never scheduled.

## Local Ollama interruption

- Workflow ID `hitl-live-20261011-2`
- Run ID `1e0c7537-8bc3-4723-b937-dc3b382a336c`
- Pending call ID `call_d63muexk`
- Safe arguments:
  - From `123 Market Street`
  - To `1 Ferry Building`
  - Passengers `1`
- Decision: approved
- Outcome: `APPROVED_COMPLETED`
- Scheduled Activity order:
  1. `OpenAIActivities.invoke_model`
  2. `human-in-the-loop.book-ride`
  3. `OpenAIActivities.invoke_model`

The local model emitted a real OpenAI Agents tool interruption, the Workflow
waited for the human Signal, and the tool did not run until approval.

## Not tested

`gemini-flash-lite` and `openai-nano` were validated through offline catalog and
provider-construction tests only. Live remote validation requires API keys and
is deferred to Issue #36. This machine-specific limit does not restrict the
recipe's catalog-driven runtime support.
