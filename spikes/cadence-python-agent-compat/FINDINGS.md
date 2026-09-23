# Cadence Python agent/model compatibility findings

## Scope and evidence standard

This removable spike targets the released `cadence-python-client` `v0.4.0`
tag (`390a28eb664ee71898a383bee2332d0671e9bdc4`), not SDK main. Source-review
conclusions and execution-verified results are intentionally separate.

| Combination | Source review | Execution result | Required worker configuration |
|---|---|---|---|
| OpenAI Agents SDK + OpenAI | Native supported path: `OpenAIActivities.invoke_model` | Not tested | `OPENAI_API_KEY`; explicit model and `--confirm-live` |
| Google ADK + Gemini | Native supported path: `GoogleADKActivities.generate_content_async` | **Verified September 23, 2026**; `gemini-3.5-flash-lite`; replay evidence below | Google AI or Vertex credentials; explicit model and `--confirm-live` |
| OpenAI Agents SDK + Claude | Blocked as-is: Activity hardcodes `OpenAIProvider` and the runner rejects non-string model objects | Blocked by source review; no live call attempted | No supported released configuration |
| Google ADK + local Ollama | Source-compatible: ADK registry resolves `ollama_chat/...` to its LiteLLM integration inside the Cadence Activity | Not tested | Local Ollama, `OLLAMA_API_BASE`, and optional LiteLLM dependency |

## Released dependency baseline

- `cadence-python-client==0.4.0`
- `openai-agents==0.12.5`
- `openai==2.30.0`
- `google-adk==2.1.0`
- `pydantic==2.12.5`

The optional Ollama group intentionally adds LiteLLM only for the Ollama case.
It is not a base dependency or a provider gateway.

## Source-review references

- Cadence OpenAI runner and Activity at the
  [`v0.4.0` tag](https://github.com/cadence-workflow/cadence-python-client/tree/v0.4.0/cadence/contrib/openai)
- Cadence Google ADK runner and Activity at the
  [`v0.4.0` tag](https://github.com/cadence-workflow/cadence-python-client/tree/v0.4.0/cadence/contrib/google_adk)
- Google ADK's [`v2.1.0` model registry](https://github.com/google/adk-python/blob/v2.1.0/src/google/adk/models/__init__.py),
  which recognizes the `ollama_chat/` prefix through optional LiteLLM support

These links support the source-review column only. They are not execution
evidence for any model/provider combination.

## Execution-verified result: Google ADK + Gemini

On September 23, 2026, the harness verified Google ADK + Gemini using model
`gemini-3.5-flash-lite` in Cadence domain `default`.

- Workflow ID: `gemini-replay-003`
- Run ID: `af506501-17d5-460f-b194-6ad9b48e52c8`
- Before restart: `GoogleADKActivities.generate_content_async` had 1 scheduled,
  1 completed, 0 failed, and 0 timed out. The Workflow was `RUNNING`, awaiting
  `resume-after-model`; before-restart verification passed with a
  `baseline-model-scheduled` value of 1.
- After the worker restart and `resume-after-model` Signal: the same Activity
  counts remained 1 scheduled, 1 completed, 0 failed, and 0 timed out. The
  Signal was present in complete Workflow history, the Workflow status was
  `COMPLETED`, its output was `READY`, and after-resume verification passed.

Observed fact: complete Workflow history contains no additional scheduled
`GoogleADKActivities.generate_content_async` Activity after the worker restart.

Conclusion: Cadence reused the recorded model Activity result during Workflow
replay after the worker restart. This conclusion is based on Cadence history
and successful Workflow completion. We did not independently measure
provider-side request counts, so this result does not establish the number of
requests received or billed by Gemini.

## How Cadence intercepts model calls

### OpenAI Agents SDK

`CadenceAgentRunner` replaces each string model with `CadenceModel`.
`CadenceModel.get_response` calls the `OpenAIActivities.invoke_model` Cadence
Activity. The Activity reconstructs an `OpenAIProvider` around
`AsyncOpenAI(max_retries=0)`, calls the provider outside workflow replay, and
returns the typed model result to the workflow. Completed Activity results are
therefore replayed from Cadence history rather than re-invoked.

Limitations in the released integration: string models only; no streaming or
synchronous runs; no MCP servers; general Handoff objects are rejected; and
the integration documents function tools only. Most importantly, the Activity
does not honor Agents SDK LiteLLM or custom-provider routing. A Claude model
would require a Cadence SDK provider-selection change, which this spike does
not implement.

### Google ADK

`CadenceAgentRunner` replaces each string `LlmAgent.model` with
`CadenceModel`. `CadenceModel.generate_content_async` schedules
`GoogleADKActivities.generate_content_async`. That Activity calls
`LLMRegistry.new_llm(model_name)` outside workflow replay and returns the
non-streaming responses. Tools are not automatically made durable: any tool
with side effects must itself be a registered Cadence Activity.

The ADK registry supports the string form `ollama_chat/<model>` through its
optional LiteLLM integration. The released Cadence runner's string-only model
constraint is compatible with that form. ADK recommends `ollama_chat` rather
than `ollama` for tool/context correctness.

## Live verification procedure

For every permitted case, start a workflow with `--pause-after-model`. After
history shows the expected model Activity completed:

1. Stop the worker.
2. Restart the same worker and task list.
3. Send `resume-after-model` to the waiting workflow.
4. Inspect history before and after resuming.

Before restart, staged verification requires a completed model Activity and a
Workflow that is still waiting without the resume Signal. After restart and
resume, it requires the Signal in complete paginated history, successful
Workflow completion, and no increase from the recorded model-Activity baseline.
The combination of those facts and no second provider-invocation log line is
the replay evidence; unchanged counts alone are insufficient. The history
command reports only Activity names, Signal names, lifecycle counts, and
terminal status. It never decodes or prints workflow or Activity payloads.

When the optional tool check is selected, the agent instructions require one
`echo_token` call with a fixed synthetic token. Both staged checks additionally
require its Cadence Activity to have scheduled and completed successfully.

## Recommendation

The initial execution-verified Phase 5 combination is Google ADK + Gemini.
Treat ADK + Ollama and OpenAI Agents + OpenAI as unverified until their staged
replay checks are recorded. Keep OpenAI Agents + Claude unsupported on this
released Cadence integration until Cadence adds provider-aware Activity
reconstruction.
