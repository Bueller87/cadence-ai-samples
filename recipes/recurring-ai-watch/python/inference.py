"""Six explicit framework/provider combinations; clients exist only on Workers."""

from typing import Any

from agents import Agent, ModelResponse, ModelSettings, ModelTracing, OpenAIProvider, RunConfig, Runner
from agents.items import TResponseOutputItem
from agents.usage import deserialize_usage, serialize_usage
from cadence import activity, workflow
from cadence.contrib.openai import OpenAIActivities
from cadence.contrib.openai.cadence_model import CadenceModel
from openai import AsyncOpenAI
from pydantic import TypeAdapter

from config import CatalogSelection
from live import ADKActivities, LiveConfigurationError, LiveSchemaError, generate_adk_report, raise_model_error
from workflow import AI_ACTIVITY_OPTIONS, ReleaseNote


NATIVE_ACTIVITY = "OpenAIActivities.invoke_model"
CHAT_ACTIVITY = "OpenAICompatibleChatCompletions.invoke_model"
OUTPUT_ITEM = TypeAdapter(TResponseOutputItem)
INSTRUCTION = (
    "Briefly explain what changed, why it may affect this application's "
    "background jobs, and what a developer should examine next. No tools."
)


def runtime_model(framework: str, provider: str, model: str) -> str:
    if framework == "google-adk":
        if provider == "google":
            return model
        if provider == "ollama":
            return f"ollama_chat/{model}"
        if provider == "openai":
            return f"openai/{model}"
    elif framework == "openai-agents" and provider in {"google", "ollama", "openai"}:
        return model
    raise LiveConfigurationError("unsupported agent/model combination")


def build_model_activities(selection: CatalogSelection, environ):
    """Called by Worker startup only; never serialize this configuration."""
    framework, provider = selection.agent.framework, selection.model.provider
    endpoint = selection.model.endpoint.rstrip("/")
    key = environ.get("MODEL_AI_KEY", "").strip()
    # Disable optional tracing/telemetry; model requests are the only AI traffic.
    environ["OPENAI_AGENTS_DISABLE_TRACING"] = "1"
    environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
    environ["LITELLM_TELEMETRY"] = "False"
    if provider == "google":
        environ["GOOGLE_API_KEY"] = key
        environ["GOOGLE_GENAI_USE_VERTEXAI"] = "FALSE"
    elif provider == "openai":
        environ["OPENAI_API_KEY"] = key
        environ["OPENAI_BASE_URL"] = endpoint
    else:
        environ["OLLAMA_API_BASE"] = endpoint

    if framework == "google-adk":
        # Cadence's Activity resolves native Gemini or ADK's LiteLLM registry.
        if provider != "google":
            import litellm

            litellm.num_retries = 0
            litellm.telemetry = False
            litellm.suppress_debug_info = True
        return ADKActivities()
    if provider == "openai":
        return NativeOpenAIActivities(OpenAIProvider(openai_client=AsyncOpenAI(
            api_key=key, base_url=endpoint, max_retries=0,
        )))
    # Catalog endpoints are native bases; derive only these protocol suffixes.
    base_url = endpoint + ("/v1beta/openai/" if provider == "google" else "/v1/")
    return ChatCompletionsActivities(OpenAIProvider(
        openai_client=AsyncOpenAI(api_key=key if provider == "google" else "ollama",
                                  base_url=base_url, max_retries=0),
        use_responses=False,
    ))


class NativeOpenAIActivities(OpenAIActivities):
    def __init__(self, provider):
        self._openai_provider = provider

    @activity.override(OpenAIActivities.invoke_model)
    async def invoke_model(self, *args, **kwargs):
        # The SDK retains its original typed signature and native implementation.
        try:
            return await super().invoke_model(*args, **kwargs)
        except Exception as error:
            raise_model_error(error)


class ChatCompletionsActivities:
    """The Phase 5 JSON-safe Activity bridge, restricted to no-tool reports."""

    def __init__(self, provider):
        self._provider = provider

    @activity.method(name=CHAT_ACTIVITY)
    async def invoke_model(
        self, model_name: str, instructions: str | None, input: Any,
        settings: dict[str, Any],
    ) -> dict[str, Any]:
        try:
            response = await self._provider.get_model(model_name).get_response(
                system_instructions=instructions, input=input,
                model_settings=ModelSettings(**settings), tools=[], output_schema=None,
                handoffs=[], tracing=ModelTracing.DISABLED,
                previous_response_id=None, conversation_id=None, prompt=None,
            )
            return {"output": [item.model_dump(mode="json") for item in response.output],
                    "usage": serialize_usage(response.usage),
                    "response_id": response.response_id, "request_id": response.request_id}
        except Exception as error:
            raise_model_error(error)


class WatchOpenAIModel(CadenceModel):
    def __init__(self, model_name: str, provider: str):
        # Do not construct the SDK's default HTTP client in Workflow code.
        self._model_name = model_name
        self._provider = provider

    async def get_response(
        self, system_instructions, input, model_settings, tools, output_schema,
        handoffs, tracing, *, previous_response_id, conversation_id, prompt,
    ) -> ModelResponse:
        if tools or handoffs or output_schema or prompt or previous_response_id or conversation_id:
            raise LiveConfigurationError("Watch supports plain no-tool reports only")
        if self._provider == "openai":
            return await workflow.execute_activity(
                NATIVE_ACTIVITY, ModelResponse, self._model_name, system_instructions,
                input, model_settings, [], None, [], ModelTracing.DISABLED,
                None, None, None, **AI_ACTIVITY_OPTIONS,
            )
        result = await workflow.execute_activity(
            CHAT_ACTIVITY, dict, self._model_name, system_instructions, input,
            model_settings.to_json_dict(), **AI_ACTIVITY_OPTIONS,
        )
        return ModelResponse(
            output=[OUTPUT_ITEM.validate_python(item) for item in result["output"]],
            usage=deserialize_usage(result["usage"]),
            response_id=result.get("response_id"), request_id=result.get("request_id"),
        )


async def generate_live_report(update: ReleaseNote, model_name: str | None,
                               framework: str, provider: str) -> str:
    if not model_name:
        raise LiveConfigurationError("live mode requires a model name")
    model = runtime_model(framework, provider, model_name)
    if framework == "google-adk":
        return await generate_adk_report(update, model)
    result = await Runner.run(
        Agent(name="recurring_ai_watch", model=WatchOpenAIModel(model, provider),
              instructions=INSTRUCTION),
        f"{update.version}: {update.notes}",
        run_config=RunConfig(tracing_disabled=True),
    )
    report = str(result.final_output or "").strip()
    if not report:
        raise LiveSchemaError("model returned no report")
    return report
