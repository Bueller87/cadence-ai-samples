# Cadence Python agent/model compatibility findings

## Scope and evidence standard

This removable spike targets the released `cadence-python-client` `v0.4.0`
tag (`390a28eb664ee71898a383bee2332d0671e9bdc4`), not SDK main. Source-review
conclusions and execution-verified results are intentionally separate.

| Combination | Source review | Execution result | Required worker configuration |
|---|---|---|---|
| Google ADK + Gemini | Native supported path: `GoogleADKActivities.generate_content_async` | **Verified September 23, 2026**; `gemini-3.5-flash-lite`; replay evidence below | Google AI or Vertex credentials; explicit model and `--confirm-live` |
| Google ADK + local Ollama/Llama 3.2 | Source-compatible: ADK registry resolves `ollama_chat/...` to its LiteLLM integration inside the Cadence Activity | **Verified September 23, 2026**; `ollama_chat/llama3.2:latest`; replay evidence below | Local Ollama, `OLLAMA_API_BASE`, and optional LiteLLM dependency |
| OpenAI Agents SDK + OpenAI | Native supported path: `OpenAIActivities.invoke_model` | **Released v0.4.0 failed** Activity argument decoding with `gpt-5-nano`; **pinned PR #176 build verified** no-tool replay with `gpt-5-nano`; separate evidence below | `OPENAI_API_KEY`; explicit model and `--confirm-live` |
| OpenAI Agents SDK + Gemini | Released integration cannot inject an alternate client; the spike supplies an app-local JSON-safe Chat Completions model/Activity bridge | **Verified September 23, 2026**; `gemini-3.5-flash-lite`; replay evidence below | `GEMINI_API_KEY`; optional `GEMINI_OPENAI_BASE_URL`; explicit model and `--confirm-live` |
| OpenAI Agents SDK + local Ollama/Llama 3.2 | Released integration cannot inject an alternate client; the spike supplies the same app-local JSON-safe Chat Completions bridge | **Verified September 23, 2026**; `llama3.2:latest`; replay evidence below | Local Ollama; optional loopback `OLLAMA_OPENAI_BASE_URL`; model `llama3.2:latest` |
| OpenAI Agents SDK + Claude | Blocked as-is: Activity hardcodes `OpenAIProvider` and the runner rejects non-string model objects | Blocked by source review; no live call attempted | No supported released configuration |

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

## Execution-verified result: Google ADK + local Ollama/Llama 3.2

On September 23, 2026, the harness verified Google ADK + local Ollama using
`ollama_chat/llama3.2:latest` with `cadence-python-client==0.4.0`.

- Cadence domain: `default`
- Task list: `agent-compat-ollama`
- Workflow ID: `ollama-replay-001`
- Run ID: `a701a86a-ed3a-4492-9a60-6209559c243b`
- Before restart: `GoogleADKActivities.generate_content_async` had 1 scheduled
  and 1 completed Activity, with no failed or timed-out Activities. The
  Workflow terminal status was `RUNNING`; before-restart verification passed
  with a baseline model-Activity count of 1.
- After the worker restart and `resume-after-model` Signal: the same Activity
  counts remained 1 scheduled and 1 completed, with no failed or timed-out
  Activities. The terminal status was `COMPLETED`, and after-resume
  verification passed.

Observed fact: complete Workflow history contains no additional scheduled
`GoogleADKActivities.generate_content_async` Activity after the worker restart.

Conclusion: the Workflow completed after a worker restart without scheduling
another model Activity. We did not independently measure Ollama HTTP request
counts, so this result does not establish the number of provider requests made.

## Execution-verified result: OpenAI Agents SDK + Gemini

On September 23, 2026, the harness verified OpenAI Agents SDK + Gemini using
the app-local JSON-safe Chat Completions Activity with
`cadence-python-client==0.4.0`.

- Case: `openai-gemini`
- Model: `gemini-3.5-flash-lite`
- Cadence domain: `default`
- Task list: `agent-compat-openai-gemini`
- Workflow ID: `openai-gemini-replay-002`
- Run ID: `bfa23776-e1c7-4793-a750-70f0bfec556a`
- Before restart: `OpenAICompatibleChatCompletions.invoke_model` had 1
  scheduled and 1 completed Activity, with no failed or timed-out Activities.
  No resume Signal had been received. The Workflow terminal status was
  `RUNNING`; before-restart verification passed with a baseline model-Activity
  count of 1.
- After the worker restart and `resume-after-model` Signal: the same Activity
  counts remained 1 scheduled and 1 completed, with no failed or timed-out
  Activities. The terminal status was `COMPLETED`, after-resume verification
  passed, and no additional model Activity was scheduled.

Observed fact: complete Workflow history contains no additional scheduled
`OpenAICompatibleChatCompletions.invoke_model` Activity after the worker
restart.

Conclusion: Cadence reused the recorded model Activity result during Workflow
replay after the worker restart. This conclusion is based on Cadence history
and successful Workflow completion. We did not independently measure
provider-side HTTP request counts, so it does not establish the number of
requests received or billed by Gemini. Tool-call compatibility was not tested.

## Execution-verified result: OpenAI Agents SDK + local Ollama/Llama 3.2

On September 23, 2026, the harness verified OpenAI Agents SDK + local Ollama
using the same app-local JSON-safe Chat Completions Activity with
`cadence-python-client==0.4.0`.

- Case: `openai-ollama`
- Model: `llama3.2:latest`
- Cadence domain: `default`
- Task list: `agent-compat-openai-ollama`
- Workflow ID: `openai-ollama-replay-001`
- Run ID: `9d1f6648-51f1-44a9-934c-5a1c1438f536`
- Before restart: `OpenAICompatibleChatCompletions.invoke_model` had 1
  scheduled and 1 completed Activity, with no failed or timed-out Activities.
  No resume Signal had been received. The Workflow terminal status was
  `RUNNING`; before-restart verification passed with a baseline model-Activity
  count of 1.
- After the worker restart and `resume-after-model` Signal: the same Activity
  counts remained 1 scheduled and 1 completed, with no failed or timed-out
  Activities. The terminal status was `COMPLETED`, after-resume verification
  passed, and no additional model Activity was scheduled.

Observed fact: complete Workflow history contains no additional scheduled
`OpenAICompatibleChatCompletions.invoke_model` Activity after the worker
restart.

Conclusion: the Workflow completed after a worker restart without scheduling
another model Activity. We did not independently measure provider-side HTTP
request counts, so this result does not establish the number of requests made
to local Ollama. Tool-call compatibility was not tested.

## Live result: native OpenAI Agents SDK + OpenAI on released v0.4.0

On September 23, 2026, the native OpenAI Agents integration failed before a
provider call could be made using the normal spike environment with
`cadence-python-client==0.4.0`.

- Provider/model: OpenAI, `gpt-5-nano`
- Integration: native `OpenAIActivities.invoke_model`
- Cadence domain: `default`
- Task list: `agent-compat-openai-openai`
- Workflow ID: `openai-openai-replay-001`
- Run ID: `511ecffb-6a57-4556-b031-b06bba9796ca`
- Activity history: 1 scheduled, 0 completed, 1 failed; no Activity timeouts
  and no resume Signal.
- Workflow terminal status: `FAILED`; before-restart verification failed.

The Worker traceback places the failure in Cadence Activity argument decoding,
through `signature.params_from_payload` and `cadence/data_converter.py`, before
the Activity body could execute:

```
TypeError: Type unions may not contain more than one TypedDict type
```

This reproduces the native Activity argument-decoding problem investigated in
[upstream issue #173](https://github.com/cadence-workflow/cadence-python-client/issues/173).
It is not attributed to `gpt-5-nano`, the OpenAI API, authentication, or model
quality. Provider-side request counts were not independently measured, and no
worker-restart replay was attempted for this failed Workflow.

## Execution-verified result: native OpenAI Agents SDK + OpenAI on pinned PR #176

On September 23, 2026, the same harness and `gpt-5-nano` model completed the
native OpenAI Agents no-tool replay procedure with the original
`OpenAIActivities.invoke_model` Activity. This used the isolated `pr176/.venv`
environment with `cadence-python-client==0.4.1.dev12+g2bc1af420`, pinned to PR
[#176](https://github.com/cadence-workflow/cadence-python-client/pull/176)
commit `2bc1af4207d20cd99ae64fa1ef82d803905af947`. The provenance check passed:
the installed converter matched the pinned checkout.

- Provider/model: live OpenAI, `gpt-5-nano`
- Cadence domain: `default`
- Task list: `agent-compat-openai-pr176`
- Workflow ID: `openai-openai-pr176-replay-001`
- Run ID: `305feb81-5059-4766-899b-9ffaa0fb7914`
- Before restart: `OpenAIActivities.invoke_model` had 1 scheduled and 1
  completed Activity, with no failed or timed-out Activities and no resume
  Signal. The Workflow terminal status was `RUNNING`; before-restart
  verification passed with a baseline model-Activity count of 1.
- After the worker restart and `resume-after-model` Signal: the Activity counts
  remained 1 scheduled and 1 completed, with no failed or timed-out Activities.
  The Workflow terminal status was `COMPLETED`, after-resume verification
  passed, and no additional model Activity was scheduled.

Observed fact: complete Workflow history contains no additional scheduled
`OpenAIActivities.invoke_model` Activity after the worker restart.

Conclusion: this establishes successful native Activity execution and
worker-restart replay for this specific pinned PR #176 configuration. The
released and PR environments contained different Cadence SDK builds, and the
PR head includes changes since the release, so this is not a patch-only causal
experiment. It does not establish provider-side HTTP request counts, billing
counts, or live tool-call compatibility. The isolated PR #176 mock-provider
`model → greet → model` experiment is separate evidence; it was neither a
live-provider test nor a worker-restart test. PR #176 is not asserted here to
have merged or to be available in a released SDK.

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

### App-local OpenAI-compatible Chat Completions path

The spike adds two explicitly selected cases, `openai-gemini` and
`openai-ollama`, without modifying the released Cadence package. Both use the
OpenAI Agents SDK's existing runner loop and an app-local `CadenceModel`
subclass whose inherited `get_response` method schedules the distinctly named
`OpenAICompatibleChatCompletions.invoke_model` Cadence Activity. Only the
Activity worker constructs the provider client and reads its endpoint and
credential configuration. The Workflow receives the model name but never a
credential or provider endpoint.

The Activity uses `OpenAIProvider` with `use_responses=False`, so every request
uses Chat Completions. Gemini defaults to Google's OpenAI-compatible endpoint.
The Ollama case defaults to the local `/v1/` endpoint, permits loopback hosts
only, and uses a non-secret placeholder API key required by the OpenAI client.
Offline tests exercise model-to-Activity dispatch and parse a mocked Chat
Completions response without external network access.

Both paths are now execution-verified for the no-tool replay scenario described
above. They intentionally reject the optional tool flag until tool-call
compatibility is tested separately. The app-local JSON-safe Activity bridge is
separate from PR #176's SDK data-converter fix.

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

Four live replay passes use released `cadence-python-client==0.4.0`:
Google ADK + Gemini, Google ADK + local Ollama/Llama 3.2, OpenAI Agents SDK + Gemini, and
OpenAI Agents SDK + local Ollama/Llama 3.2. The two released-SDK OpenAI Agents
alternatives rely on the spike-local JSON-safe Chat Completions Activity, not
PR #176's converter fix.

The native OpenAI Agents + OpenAI path failed in the released v0.4.0 test at
Activity argument decoding, before the native Activity body executed. Separately,
the pinned PR #176 build completed a live OpenAI no-tool replay test using the
native `OpenAIActivities.invoke_model` Activity. Keep those results distinct:
the PR build is not a released SDK, and the native released-SDK path remains
failed in this test. Keep OpenAI Agents SDK + Claude unsupported on this
released Cadence integration until Cadence adds provider-aware Activity
reconstruction.
