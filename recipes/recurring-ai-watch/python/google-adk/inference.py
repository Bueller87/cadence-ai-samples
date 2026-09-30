"""Google ADK report path for three model providers; clients exist only on Workers."""

from collections.abc import AsyncGenerator

from cadence import activity, workflow
from cadence.contrib.google_adk import CadenceAgentRunner, GoogleADKActivities
from cadence.contrib.google_adk.cadence_model import CadenceModel
from google.adk.agents import LlmAgent
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.adk.sessions import InMemorySessionService
from google.genai import errors as genai_errors
from google.genai import types

from config import CatalogSelection
from live import (
    LiveAuthenticationError,
    LiveConfigurationError,
    LiveSchemaError,
    raise_model_error,
    retryable,
)
from workflow import AI_ACTIVITY_OPTIONS, ReleaseNote


INSTRUCTION = (
    "Briefly explain what changed, why it may affect this application's "
    "background jobs, and what a developer should examine next. No tools."
)


def runtime_model(provider: str, model: str) -> str:
    if provider == "google":
        return model
    if provider == "ollama":
        return f"ollama_chat/{model}"
    if provider == "openai":
        return f"openai/{model}"
    raise LiveConfigurationError("unsupported model provider")


def build_model_activities(selection: CatalogSelection, environ):
    """Called by Worker startup only; never serialize this configuration."""
    provider = selection.model.provider
    endpoint = selection.model.endpoint.rstrip("/")
    key = environ.get("MODEL_AI_KEY", "").strip()
    # Disable optional telemetry; model requests are the only AI traffic.
    environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
    environ["LITELLM_TELEMETRY"] = "False"
    if provider == "google":
        environ["GOOGLE_API_KEY"] = key
        environ["GOOGLE_GENAI_USE_VERTEXAI"] = "FALSE"
    else:
        # Cadence's Activity resolves non-Gemini models through ADK's LiteLLM registry.
        if provider == "openai":
            environ["OPENAI_API_KEY"] = key
            environ["OPENAI_BASE_URL"] = endpoint
        else:
            environ["OLLAMA_API_BASE"] = endpoint
        import litellm

        litellm.num_retries = 0
        litellm.telemetry = False
        litellm.suppress_debug_info = True
    return ADKActivities()


class ADKActivities(GoogleADKActivities):
    @activity.override(GoogleADKActivities.generate_content_async)
    async def generate_content_async(
        self, model_name: str, llm_request: LlmRequest
    ) -> list[LlmResponse]:
        try:
            return await super().generate_content_async(model_name, llm_request)
        except genai_errors.APIError as error:
            # Google reports invalid API keys as HTTP 400, not necessarily 401.
            body = error.details if isinstance(error.details, dict) else {}
            body = body.get("error", body)
            details = body.get("details", []) if isinstance(body, dict) else []
            invalid_key = any(
                isinstance(detail, dict) and detail.get("reason") == "API_KEY_INVALID"
                for detail in (details if isinstance(details, list) else [])
            )
            if error.code in {401, 403} or invalid_key:
                raise LiveAuthenticationError("Gemini rejected MODEL_AI_KEY") from None
            if retryable(error.code):
                raise RuntimeError(f"Gemini temporary failure: HTTP {error.code}") from None
            raise LiveSchemaError(f"Gemini rejected the request: HTTP {error.code}") from None
        except (TypeError, ValueError):
            raise LiveSchemaError("Gemini configuration or schema is invalid") from None
        except Exception as error:
            raise_model_error(error)


class RetryingCadenceModel(CadenceModel):
    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        if stream:
            raise RuntimeError("Streaming is not supported")
        call = self._google_adk_activities.generate_content_async.with_options(
            **AI_ACTIVITY_OPTIONS
        )
        for response in await call(model_name=self.model, llm_request=llm_request):
            yield response


async def generate_live_report(update: ReleaseNote, model_name: str | None,
                               provider: str) -> str:
    if not model_name:
        raise LiveConfigurationError("live mode requires a model name")
    agent = LlmAgent(
        name="recurring_ai_watch",
        model=RetryingCadenceModel(model=runtime_model(provider, model_name)),
        instruction=INSTRUCTION,
    )
    sessions = InMemorySessionService()
    runner = CadenceAgentRunner(
        app_name="recurring-ai-watch", agent=agent, session_service=sessions
    )
    workflow_id = workflow.WorkflowContext.get().info().workflow_id
    await sessions.create_session(
        app_name=runner.app_name, user_id="watch", session_id=workflow_id
    )
    report = ""
    async for event in runner.run_async(
        user_id="watch",
        session_id=workflow_id,
        new_message=types.Content(
            role="user",
            parts=[types.Part.from_text(text=f"{update.version}: {update.notes}")],
        ),
    ):
        if event.is_final_response() and event.content and event.content.parts:
            report = "".join(part.text or "" for part in event.content.parts).strip()
    if not report:
        raise LiveSchemaError("Gemini returned no report")
    return report
