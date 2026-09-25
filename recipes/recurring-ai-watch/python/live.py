"""The one supported live path: TypeSafe Jev then Google ADK/Gemini."""

from __future__ import annotations

import json
import os
from collections.abc import AsyncGenerator, Callable, MutableMapping
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen

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
    MockClassification,
    ReleaseNote,
)


GOOGLE_ENDPOINT = "https://generativelanguage.googleapis.com"


class LiveAuthenticationError(Exception):
    pass


class LiveConfigurationError(Exception):
    pass


class LiveSchemaError(Exception):
    pass


def retryable(status: int) -> bool:
    return status in {408, 409, 425, 429, 529} or status >= 500


def validate_live(selection: CatalogSelection) -> None:
    supported = (
        selection.agent.id == "google-adk"
        and selection.agent.framework == "google-adk"
        and selection.model.id == "gemini-flash-lite"
        and selection.model.provider == "google"
        and selection.classifier.id == "jev-default"
        and selection.classifier.provider == "typesafe"
    )
    if not supported:
        raise LiveConfigurationError(
            "live mode supports only google-adk + gemini-flash-lite + jev-default"
        )
    if selection.model.endpoint.rstrip("/") != GOOGLE_ENDPOINT:
        raise LiveConfigurationError("live mode requires Google's official endpoint")


def live_activities(
    selection: CatalogSelection,
    environ: MutableMapping[str, str] = os.environ,
) -> tuple["JevClassifier", "GeminiActivities"]:
    validate_live(selection)
    model_key = environ.get("MODEL_AI_KEY", "").strip()
    classifier_key = environ.get("CLASSIFIER_AI_KEY", "").strip()
    if not model_key or not classifier_key:
        raise LiveConfigurationError(
            "live worker requires MODEL_AI_KEY and CLASSIFIER_AI_KEY"
        )
    environ["GOOGLE_API_KEY"] = model_key
    return (
        JevClassifier(
            classifier_key,
            selection.classifier.endpoint,
            selection.classifier.model,
        ),
        GeminiActivities(),
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
    def classify(self, update: ReleaseNote) -> MockClassification:
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
        return MockClassification(
            choice == "yes", f"Jev selected {choice} ({confidence:.2f})"
        )


class GeminiActivities(GoogleADKActivities):
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


async def generate_live_report(update: ReleaseNote, model_name: str | None) -> str:
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
