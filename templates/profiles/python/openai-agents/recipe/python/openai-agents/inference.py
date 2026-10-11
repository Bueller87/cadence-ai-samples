"""OpenAI Agents model bridge; provider clients exist only on Workers."""

from typing import Any

from agents import (
    Agent,
    ModelResponse,
    ModelSettings,
    ModelTracing,
    OpenAIProvider,
    RunConfig,
    Runner,
)
from agents.items import TResponseOutputItem
from agents.usage import deserialize_usage, serialize_usage
from cadence import activity, workflow
from cadence.contrib.openai import OpenAIActivities
from cadence.contrib.openai.cadence_model import CadenceModel
from openai import AsyncOpenAI
from pydantic import TypeAdapter

from config import ModelConfig
from live import LiveConfigurationError, LiveSchemaError, raise_model_error
from workflow import AI_ACTIVITY_OPTIONS


NATIVE_ACTIVITY = "OpenAIActivities.invoke_model"
CHAT_ACTIVITY = "OpenAICompatibleChatCompletions.invoke_model"
OUTPUT_ITEM = TypeAdapter(TResponseOutputItem)
INSTRUCTION = (
    "Answer the user's request briefly and identify one practical next step. "
    "Do not use tools."
)


def openai_compatible_base_url(provider: str, endpoint: str) -> str:
    """Derive the OpenAI-compatible route from a provider-native catalog base."""
    if provider == "google":
        return endpoint.rstrip("/") + "/v1beta/openai/"
    if provider == "ollama":
        return endpoint.rstrip("/") + "/v1/"
    if provider == "openai":
        return endpoint
    raise LiveConfigurationError("unsupported model provider")


def build_model_activities(
    selection: ModelConfig, environ: dict[str, str]
) -> object:
    """Build Worker-only Activities for the selected catalog provider."""
    provider = selection.provider
    endpoint = selection.endpoint
    key = environ.get("MODEL_AI_KEY", "").strip()
    environ["OPENAI_AGENTS_DISABLE_TRACING"] = "1"
    if provider == "google":
        environ["GOOGLE_API_KEY"] = key
    elif provider == "openai":
        environ["OPENAI_API_KEY"] = key
        environ["OPENAI_BASE_URL"] = endpoint
    elif provider == "ollama":
        environ["OLLAMA_API_BASE"] = endpoint
    else:
        raise LiveConfigurationError("unsupported model provider")

    base_url = openai_compatible_base_url(provider, endpoint)
    if provider == "openai":
        return NativeOpenAIActivities(
            OpenAIProvider(
                openai_client=AsyncOpenAI(
                    api_key=key,
                    base_url=base_url,
                    max_retries=0,
                )
            )
        )
    return ChatCompletionsActivities(
        OpenAIProvider(
            openai_client=AsyncOpenAI(
                api_key=key if provider == "google" else "ollama",
                base_url=base_url,
                max_retries=0,
            ),
            use_responses=False,
        )
    )


class NativeOpenAIActivities(OpenAIActivities):
    def __init__(self, provider: OpenAIProvider) -> None:
        self._openai_provider = provider

    @activity.override(OpenAIActivities.invoke_model)
    async def invoke_model(self, *args: Any, **kwargs: Any) -> ModelResponse:
        try:
            return await super().invoke_model(*args, **kwargs)
        except Exception as error:
            raise_model_error(error)


class ChatCompletionsActivities:
    """JSON-safe Activity bridge for Google and Ollama compatibility APIs."""

    def __init__(self, provider: OpenAIProvider) -> None:
        self._provider = provider

    @activity.method(name=CHAT_ACTIVITY)
    async def invoke_model(
        self,
        model_name: str,
        instructions: str | None,
        input: Any,
        settings: dict[str, Any],
    ) -> dict[str, Any]:
        try:
            response = await self._provider.get_model(model_name).get_response(
                system_instructions=instructions,
                input=input,
                model_settings=ModelSettings(**settings),
                tools=[],
                output_schema=None,
                handoffs=[],
                tracing=ModelTracing.DISABLED,
                previous_response_id=None,
                conversation_id=None,
                prompt=None,
            )
            return {
                "output": [
                    item.model_dump(mode="json") for item in response.output
                ],
                "usage": serialize_usage(response.usage),
                "response_id": response.response_id,
                "request_id": response.request_id,
            }
        except Exception as error:
            raise_model_error(error)


class RecipeCadenceModel(CadenceModel):
    def __init__(self, model_name: str, provider: str) -> None:
        # Avoid constructing the SDK's default HTTP client in Workflow code.
        self._model_name = model_name
        self._provider = provider

    async def get_response(
        self,
        system_instructions: str | None,
        input: Any,
        model_settings: ModelSettings,
        tools: list[Any],
        output_schema: Any,
        handoffs: list[Any],
        tracing: ModelTracing,
        *,
        previous_response_id: str | None,
        conversation_id: str | None,
        prompt: Any,
    ) -> ModelResponse:
        if (
            tools
            or handoffs
            or output_schema
            or prompt
            or previous_response_id
            or conversation_id
        ):
            raise LiveConfigurationError(
                "starter supports plain no-tool agent responses only"
            )
        if self._provider == "openai":
            return await workflow.execute_activity(
                NATIVE_ACTIVITY,
                ModelResponse,
                self._model_name,
                system_instructions,
                input,
                model_settings,
                [],
                None,
                [],
                ModelTracing.DISABLED,
                None,
                None,
                None,
                **AI_ACTIVITY_OPTIONS,
            )
        result = await workflow.execute_activity(
            CHAT_ACTIVITY,
            dict,
            self._model_name,
            system_instructions,
            input,
            model_settings.to_json_dict(),
            **AI_ACTIVITY_OPTIONS,
        )
        return ModelResponse(
            output=[
                OUTPUT_ITEM.validate_python(item) for item in result["output"]
            ],
            usage=deserialize_usage(result["usage"]),
            response_id=result.get("response_id"),
            request_id=result.get("request_id"),
        )


async def generate_live_answer(
    text: str,
    model_name: str | None,
    provider: str | None,
) -> str:
    if not text.strip():
        raise LiveConfigurationError("text must not be empty")
    if not model_name or provider not in {"google", "ollama", "openai"}:
        raise LiveConfigurationError("live mode requires a supported model")
    result = await Runner.run(
        Agent(
            name="__RECIPE_SLUG__",
            model=RecipeCadenceModel(model_name, provider),
            instructions=INSTRUCTION,
        ),
        text,
        run_config=RunConfig(tracing_disabled=True),
    )
    answer = str(result.final_output or "").strip()
    if not answer:
        raise LiveSchemaError("model returned no answer")
    return answer
