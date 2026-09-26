# Recurring AI Watch inference evidence

SDK: pinned PR #176 commit `2bc1af4207d20cd99ae64fa1ef82d803905af947`, installed
as `0.4.1.dev12+g2bc1af420`. Installed `direct_url.json` confirms this revision.
Google ADK 2.1.0, OpenAI Agents 0.12.5, OpenAI 2.30.0, LiteLLM 1.83.7.

| Framework | Model | Current Watch evidence |
|---|---|---|
| Google ADK | Gemini | PROVEN live Gemini model and Jev classification |
| Google ADK | Ollama/Llama 3.2 | PROVEN local Ollama model and live Jev classification |
| Google ADK | OpenAI nano | PROVEN live OpenAI model and Jev classification |
| OpenAI Agents | Gemini | PROVEN live Gemini model and Jev classification |
| OpenAI Agents | Ollama/Llama 3.2 | PROVEN local Ollama model and live Jev classification |
| OpenAI Agents | OpenAI nano | PROVEN live OpenAI model and Jev classification |

The offline suite exercises all six agent loops through Cadence's testing
environment. That is separate from provider execution evidence. Earlier
[Phase 5 evidence](../../spikes/cadence-python-agent-compat/FINDINGS.md) is also
separate from validation of the expanded Watch.

## Local Ollama executions

Domain: `cadence-ai-samples`. Both used the catalog's `llama3.2:latest`, an isolated
task list/Worker, mock classification, and a real local Ollama model. No cloud key
was supplied. Both finished three checks and returned `STOPPED`, with a nonempty
report. Check-now advanced exactly one check; no further check occurred during
the 16-second observation after stop. Both test Workers exited.

| Framework | Workflow ID | Run ID | Completed model Activity |
|---|---|---|---|
| Google ADK | `watch-matrix-google-adk-llama3.2-local-21a742de8a8d4f30ac2ca1af33a9cd5f` | `ed287b20-6375-4e13-9541-a604284177b0` | `GoogleADKActivities.generate_content_async` |
| OpenAI Agents | `watch-matrix-openai-agents-llama3.2-local-16424bab5d564578895be35aff909b9a` | `a19e7f9b-9d92-475d-9e3a-2fdee116eb56` | `OpenAICompatibleChatCompletions.invoke_model` |

Each complete history showed: classify → classify → model → classify, with all
four Activities completed, followed by Workflow completion. This proves local
inference and Watch control behavior, not live Jev classification or independent
provider HTTP request counts. The reproducible check is `python/tests/live_matrix_check.py`.

## Live Google ADK + OpenAI

- Workflow ID: `watch-matrix-google-adk-openai-nano-6bdbefdbce6c4b78a306589b27f47a55`
- Run ID: `594119c2-24d3-415f-b09d-d4c571636128`
- Domain: `cadence-ai-samples`; task list: `watch-matrix-adk-openai`.
- Catalog selections: `google-adk`, `openai-nano`, `jev-default`.
- Model: `gpt-5-nano`, resolved as `openai/gpt-5-nano` through ADK/LiteLLM.
- Complete history: classify → classify → `GoogleADKActivities.generate_content_async`
  → classify. All four Activities completed; no Activity failures or timeouts.
- Report present after check 2. Check-now advanced exactly one check. Stop while
  waiting completed the Workflow with `STOPPED`, three checks, and its report.
  No extra check occurred during the subsequent 16-second observation.
- Kevin restarted the Worker after entering credentials locally. No credentials
  were requested in chat or supplied to Workflow inputs.

The earlier interrupted run (`watch-matrix-google-adk-openai-nano-5a5b7bd8751e4372948947552cdd8fd4`,
run `f4c34bde-6535-43f9-a10b-10ba406d0290`) had failed model Activities and later
completed after its cleanup stop Signal. It is not counted as a pass. This live
test does not independently establish provider request counts or restart replay.

## Live OpenAI Agents + OpenAI

- Workflow ID: `watch-matrix-openai-agents-openai-nano-f5d863fdaf504799949d01d635e1800e`
- Run ID: `56602732-940b-4aab-bf16-860b0ad89508`
- Domain: `cadence-ai-samples`; task list: `watch-matrix-openai-openai`.
- Catalog selections: `openai-agents`, `openai-nano`, `jev-default`.
- Model: `gpt-5-nano`, using the native SDK OpenAI Activity on the pinned PR build.
- Complete history: classify → classify → `OpenAIActivities.invoke_model` → classify.
  All four Activities completed, and the Workflow completed with `STOPPED`.
- Report present after check 2; check-now added exactly one check. Stop while
  waiting finished with three checks and the report retained. No additional
  check occurred during the subsequent 16-second observation.
- Kevin started the Worker and entered both credentials through masked local
  prompts. No app-local Chat Completions bridge was used for this model path.

This run verifies native model Activity execution and Watch control behavior.
It does not independently measure provider request counts or restart replay.

## Live Google ADK + Gemini

- Workflow ID: `watch-matrix-google-adk-gemini-flash-lite-0e76c13eaffe433aa65603b9fe13be42`
- Run ID: `c2deb01e-ce5f-44c2-84b8-99bfa6ad8af1`
- Domain: `cadence-ai-samples`; task list: `watch-matrix-adk-gemini`.
- Catalog selections: `google-adk`, `gemini-flash-lite`, `jev-default`.
- Model: `gemini-3.5-flash-lite`, using the native Google ADK path.
- Complete history: classify → classify → `GoogleADKActivities.generate_content_async`
  → classify. All four Activities completed; the Workflow completed with `STOPPED`.
- Report present after check 2. Check-now advanced exactly one check; stop while
  waiting preserved three checks and the report, including the subsequent
  16-second observation with no additional check.
- Kevin started the Worker after masked local prompts for Gemini and Jev keys.

This verifies the native Google path with the sample's pinned PR SDK. It does
not independently measure provider request counts or restart replay.

## Live OpenAI Agents + Gemini

- Workflow ID: `watch-matrix-openai-agents-gemini-flash-lite-f692fea183b04d9eba10680a4a267486`
- Run ID: `1775ecb1-d33b-4d91-b3c5-afb081758db6`
- Domain: `cadence-ai-samples`; task list: `watch-matrix-openai-gemini`.
- Catalog selections: `openai-agents`, `gemini-flash-lite`, `jev-default`.
- Model: `gemini-3.5-flash-lite`, using the app-local JSON-safe Chat Completions bridge.
- Complete history: classify → classify → `OpenAICompatibleChatCompletions.invoke_model`
  → classify. All four Activities completed; the Workflow completed with `STOPPED`.
- Report present after check 2. Check-now advanced exactly one check. Stop while
  waiting preserved three checks and the report; no check occurred during the
  subsequent 16-second observation.
- Kevin started the Worker after masked local prompts for Gemini and Jev keys.

This verifies the bridge path in the Watch. It does not independently measure
provider request counts, tool compatibility, or restart replay.

## Live Jev + Google ADK + local Ollama

- Workflow ID: `watch-matrix-google-adk-llama3.2-local-d491dfabf2a548adb91d25debc38e314`
- Run ID: `21c584e3-39ea-42a4-97c1-ce9f1490f0b9`
- Domain: `cadence-ai-samples`; task list: `watch-matrix-adk-ollama`.
- Catalog selections: `google-adk`, `llama3.2-local`, `jev-default`.
- Model: local `llama3.2:latest`, translated to `ollama_chat/llama3.2:latest`.
- Complete history: classify → classify → `GoogleADKActivities.generate_content_async`
  → classify. All four Activities completed; the Workflow completed with `STOPPED`.
- Report present after check 2. Check-now advanced exactly one check; stop while
  waiting preserved three checks and the report. No extra check occurred during
  the subsequent 16-second observation.
- Kevin started the Worker with only the Jev credential after removing
  `MODEL_AI_KEY`. No real model API key was required.

This adds live Jev evidence to the earlier local inference test. It does not
independently establish HTTP request counts or restart replay.

## Live Jev + OpenAI Agents + local Ollama

- Workflow ID: `watch-matrix-openai-agents-llama3.2-local-eb245c448f194534aa2b9f4a60d96b17`
- Run ID: `91529a47-75cb-4d95-a3bf-3b4818857b04`
- Domain: `cadence-ai-samples`; task list: `watch-matrix-openai-ollama`.
- Catalog selections: `openai-agents`, `llama3.2-local`, `jev-default`.
- Model: local `llama3.2:latest`, using the app-local Chat Completions bridge.
- Complete history: classify → classify → `OpenAICompatibleChatCompletions.invoke_model`
  → classify. All four Activities completed; the Workflow completed with `STOPPED`.
- Report present after check 2; check-now added exactly one check. Stop while
  waiting retained three checks and the report. The following 16-second
  observation showed no additional check.
- Kevin started the Worker with only the Jev credential; `MODEL_AI_KEY` was unset.

All six framework/model combinations now have Watch execution evidence with
live Jev. The two earlier mock-classifier runs remain separate evidence. These
checks establish model Activity completion and Watch controls; they do not test
tools, independently count provider requests, or repeat Phase 5's restart replay.
