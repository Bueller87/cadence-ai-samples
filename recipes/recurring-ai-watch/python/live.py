"""TypeSafe Jev classification and the Cadence Google ADK report path."""

from __future__ import annotations

import json
import os
from collections.abc import AsyncGenerator, Callable, MutableMapping
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from urllib.parse import urlsplit

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
from workflow import (
    AI_ACTIVITY_OPTIONS,
    CLASSIFY_ACTIVITY,
    ClassificationDecision,
    ReleaseNote,
)


GOOGLE_ENDPOINT = "https://generativelanguage.googleapis.com"


class LiveAuthenticationError(Exception):
    pass


class LiveConfigurationError(ValueError):
    pass


class LiveSchemaError(Exception):
    pass


def retryable(status: int) -> bool:
    return status in {408, 409, 425, 429, 529} or status >= 500


def validate_live(selection: CatalogSelection) -> None:
    if selection.classifier.id != "jev-default" or selection.classifier.provider != "typesafe":
        raise LiveConfigurationError("only jev-default is implemented; other classifiers are catalog candidates")
    if selection.agent.framework not in {"google-adk", "openai-agents"}:
        raise LiveConfigurationError("unsupported agent framework")
    if selection.model.provider not in {"google", "ollama", "openai"}:
        raise LiveConfigurationError("unsupported model provider")
    endpoint = urlsplit(selection.model.endpoint)
    if (endpoint.scheme not in {"http", "https"} or not endpoint.hostname
            or endpoint.username or endpoint.password or endpoint.query or endpoint.fragment):
        raise LiveConfigurationError("model endpoint must be an HTTP URL without credentials, query, or fragment")
    if selection.model.provider == "google" and selection.model.endpoint.rstrip("/") != GOOGLE_ENDPOINT:
        raise LiveConfigurationError("live mode requires Google's official endpoint")
    if selection.model.provider == "ollama" and endpoint.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise LiveConfigurationError("Ollama requires a loopback endpoint")


def live_activities(
    selection: CatalogSelection,
    environ: MutableMapping[str, str] = os.environ,
) -> tuple["JevClassifier", object]:
    validate_live(selection)
    model_key = environ.get("MODEL_AI_KEY", "").strip()
    classifier_key = environ.get("CLASSIFIER_AI_KEY", "").strip()
    if (selection.model.provider != "ollama" and not model_key) or not classifier_key:
        raise LiveConfigurationError(
            "live worker requires MODEL_AI_KEY and CLASSIFIER_AI_KEY"
        )
    from inference import build_model_activities

    return (
        JevClassifier(
            classifier_key,
            selection.classifier.endpoint,
            selection.classifier.model,
        ),
        build_model_activities(selection, environ),
    )


class JevClassifier:
    def __init__(
        self,
        api_key: str,
        endpoint: str,
        model: str,
        opener: Callable[..., Any] = urlopen,
    ) -> None:
        self._api_key = api_key
        self._endpoint = endpoint
        self._model = model
        self._open = opener

    @activity.method(name=CLASSIFY_ACTIVITY)
    def classify(self, update: ReleaseNote) -> ClassificationDecision:
        question = {
            "type": "choice",
            "instructions": (
                "Does this release potentially affect the fictional application's "
                "background-job behavior?"
            ),
            "criteria": {
                "yes": "It may affect retries, queues, workers, scheduling, or timeouts.",
                "no": "It does not affect background-job behavior.",
            },
        }
        request = Request(
            self._endpoint,
            data=json.dumps(
                {
                    "state": f"Version: {update.version}\nRelease notes: {update.notes}",
                    "model": self._model,
                    "questions": {"relevant": question},
                }
            ).encode(),
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with self._open(request, timeout=20) as response:
                answer = json.loads(response.read(1_048_577))["answers"]["relevant"]
        except HTTPError as error:
            if retryable(error.code):
                raise
            if error.code in {401, 403}:
                raise LiveAuthenticationError("Jev authentication failed") from error
            raise LiveSchemaError(f"Jev rejected the request: HTTP {error.code}") from error
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise LiveSchemaError("Jev returned an invalid response") from error

        if not isinstance(answer, dict):
            raise LiveSchemaError("Jev returned an invalid response")

        choice = answer.get("choice")
        confidence = answer.get("confidence")
        if answer.get("type") != "choice" or choice not in ("yes", "no"):
            raise LiveSchemaError("Jev returned an invalid choice")
        if not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
            raise LiveSchemaError("Jev returned an invalid confidence")
        return ClassificationDecision(
            choice == "yes", f"Jev selected {choice} ({confidence:.2f})"
        )


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


def raise_model_error(error: Exception) -> None:
    """Keep provider bodies (which may echo headers) out of Activity failures."""
    status = getattr(error, "status_code", None)
    if status in {401, 403}:
        raise LiveAuthenticationError("model rejected MODEL_AI_KEY") from None
    if isinstance(status, int) and not retryable(status):
        raise LiveSchemaError(f"model rejected request: HTTP {status}") from None
    if isinstance(error, (TypeError, ValueError)):
        raise LiveSchemaError("model configuration or schema is invalid") from None
    raise RuntimeError("temporary model request failure") from None


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


async def generate_adk_report(update: ReleaseNote, model_name: str) -> str:
    if not model_name:
        raise LiveConfigurationError("live mode requires a model name")
    agent = LlmAgent(
        name="recurring_ai_watch",
        model=RetryingCadenceModel(model=model_name),
        instruction=(
            "Briefly explain what changed, why it may affect this application's "
            "background jobs, and what a developer should examine next. No tools."
        ),
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
