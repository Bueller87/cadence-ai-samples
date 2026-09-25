"""Worker-local TypeSafe Jev and Google ADK integrations for live mode."""

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
    CLASSIFY_UPDATE_ACTIVITY,
    PROCESSING_ACTIVITY_OPTIONS,
    MockClassification,
    ReleaseNote,
)


OFFICIAL_GOOGLE_ENDPOINT = "https://generativelanguage.googleapis.com"
LIVE_AGENT_ID = "google-adk"
LIVE_MODEL_ID = "gemini-flash-lite"
LIVE_CLASSIFIER_ID = "jev-default"


class LiveAuthenticationError(Exception):
    """A non-retryable provider credential failure."""


class LiveConfigurationError(Exception):
    """A non-retryable live worker configuration failure."""


class LiveSchemaError(Exception):
    """A non-retryable provider request or response schema failure."""


def is_retryable_provider_status(status_code: int) -> bool:
    return status_code in {408, 409, 425, 429, 529} or status_code >= 500


def validate_live_selection(selection: CatalogSelection) -> None:
    """Accept only the integration tuple verified by the Phase 5 spike."""

    if (
        selection.agent.id != LIVE_AGENT_ID
        or selection.agent.framework != "google-adk"
        or selection.model.id != LIVE_MODEL_ID
        or selection.model.provider != "google"
        or selection.classifier.id != LIVE_CLASSIFIER_ID
        or selection.classifier.provider != "typesafe"
    ):
        raise LiveConfigurationError(
            "live mode supports only google-adk + gemini-flash-lite + jev-default"
        )
    if selection.model.endpoint.rstrip("/") != OFFICIAL_GOOGLE_ENDPOINT:
        raise LiveConfigurationError(
            "live mode requires the official Google Generative Language endpoint"
        )


def configure_live_worker(
    selection: CatalogSelection,
    environ: MutableMapping[str, str] = os.environ,
) -> tuple["LiveJevClassifier", "LiveGoogleADKActivities"]:
    """Validate worker-only settings and construct live Activity implementations."""

    validate_live_selection(selection)
    model_key = environ.get("MODEL_AI_KEY", "").strip()
    classifier_key = environ.get("CLASSIFIER_AI_KEY", "").strip()
    if not model_key:
        raise LiveConfigurationError("live worker requires MODEL_AI_KEY")
    if not classifier_key:
        raise LiveConfigurationError("live worker requires CLASSIFIER_AI_KEY")

    # Google ADK reads this provider-specific name inside the model Activity.
    environ["GOOGLE_API_KEY"] = model_key
    return (
        LiveJevClassifier(
            api_key=classifier_key,
            endpoint=selection.classifier.endpoint,
            model=selection.classifier.model,
        ),
        LiveGoogleADKActivities(),
    )


class LiveJevClassifier:
    """One-question TypeSafe System One classifier using the proven Jev contract."""

    def __init__(
        self,
        *,
        api_key: str,
        endpoint: str,
        model: str,
        opener: Callable[..., Any] = urlopen,
    ) -> None:
        self._api_key = api_key
        self._endpoint = endpoint
        self._model = model
        self._opener = opener

    @activity.method(name=CLASSIFY_UPDATE_ACTIVITY)
    def classify_update(self, update: ReleaseNote) -> MockClassification:
        payload = {
            "state": f"Version: {update.version}\nRelease notes: {update.notes}",
            "model": self._model,
            "questions": {
                "warrants_report": {
                    "type": "choice",
                    "instructions": (
                        "Does this release potentially affect the fictional "
                        "application's background-job behavior?"
                    ),
                    "criteria": {
                        "yes": (
                            "The release may change retries, scheduling, queues, "
                            "workers, timeouts, or other background-job behavior."
                        ),
                        "no": (
                            "The release is unrelated to the fictional application's "
                            "background-job behavior."
                        ),
                    },
                }
            },
        }
        request = Request(
            self._endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with self._opener(request, timeout=20) as response:
                body = response.read(1_048_577)
        except HTTPError as error:
            if error.code in {401, 403}:
                raise LiveAuthenticationError(
                    "Jev rejected the configured classifier credential"
                ) from error
            if is_retryable_provider_status(error.code):
                raise
            raise LiveSchemaError(
                f"Jev rejected the classifier request with HTTP {error.code}"
            ) from error

        if len(body) > 1_048_576:
            raise LiveSchemaError("Jev response exceeded the size limit")
        try:
            decoded = json.loads(body)
            if not isinstance(decoded["model"], str) or not decoded["model"].strip():
                raise ValueError("missing model")
            answer = decoded["answers"]["warrants_report"]
            if answer["type"] != "choice" or answer["choice"] not in {"yes", "no"}:
                raise ValueError("invalid choice answer")
            confidence = answer["confidence"]
            probabilities = answer["probabilities"]
            if (
                isinstance(confidence, bool)
                or not isinstance(confidence, (int, float))
                or not 0 <= confidence <= 1
                or not isinstance(probabilities, dict)
                or not probabilities
                or answer["choice"] not in probabilities
                or any(
                    isinstance(probability, bool)
                    or not isinstance(probability, (int, float))
                    or not 0 <= probability <= 1
                    for probability in probabilities.values()
                )
                or abs(sum(probabilities.values()) - 1) > 0.02
            ):
                raise ValueError("invalid choice confidence")
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise LiveSchemaError("Jev returned an invalid classifier response") from error

        selected = answer["choice"]
        return MockClassification(
            warrants_report=selected == "yes",
            reason=f"Jev selected {selected} with confidence {confidence:.2f}",
        )


class LiveGoogleADKActivities(GoogleADKActivities):
    """Native Google ADK Activity with non-retryable failures made explicit."""

    @activity.override(GoogleADKActivities.generate_content_async)
    async def generate_content_async(
        self,
        model_name: str,
        llm_request: LlmRequest,
    ) -> list[LlmResponse]:
        try:
            return await super().generate_content_async(model_name, llm_request)
        except genai_errors.APIError as error:
            if is_retryable_provider_status(error.code):
                raise
            if error.code in {401, 403}:
                raise LiveAuthenticationError(
                    "Gemini rejected the configured model credential"
                ) from error
            raise LiveSchemaError(
                f"Gemini rejected the model request with HTTP {error.code}"
            ) from error
        except (TypeError, ValueError) as error:
            raise LiveSchemaError("Gemini model configuration or schema is invalid") from error


class RetryingCadenceModel(CadenceModel):
    """Cadence's native ADK model bridge with this sample's bounded policy."""

    async def generate_content_async(
        self,
        llm_request: LlmRequest,
        stream: bool = False,
    ) -> AsyncGenerator[LlmResponse, None]:
        if stream:
            raise RuntimeError("Streaming is not supported")
        model_activity = (
            self._google_adk_activities.generate_content_async.with_options(
                **PROCESSING_ACTIVITY_OPTIONS
            )
        )
        responses = await model_activity(
            model_name=self.model,
            llm_request=llm_request,
        )
        for response in responses:
            yield response


async def generate_live_report(update: ReleaseNote, model_name: str | None) -> str:
    """Run the verified no-tool, non-streaming Google ADK path in Workflow code."""

    if not model_name:
        raise LiveConfigurationError("live report generation requires a model name")
    agent = LlmAgent(
        name="recurring_ai_watch",
        model=RetryingCadenceModel(model=model_name),
        instruction=(
            "Write a concise application impact report with three parts: what changed; "
            "why it may matter to the fictional application's background jobs; and "
            "what a developer should examine next. Use only the supplied release note."
        ),
        tools=[],
    )
    session_service = InMemorySessionService()
    runner = CadenceAgentRunner(
        app_name="recurring-ai-watch",
        agent=agent,
        session_service=session_service,
    )
    workflow_id = workflow.WorkflowContext.get().info().workflow_id
    session_id = f"{workflow_id}-{update.version}"
    await session_service.create_session(
        app_name=runner.app_name,
        user_id="watch-user",
        session_id=session_id,
    )
    final_text = ""
    async for event in runner.run_async(
        user_id="watch-user",
        session_id=session_id,
        new_message=types.Content(
            role="user",
            parts=[
                types.Part.from_text(
                    text=f"Version: {update.version}\nRelease notes: {update.notes}"
                )
            ],
        ),
    ):
        if event.is_final_response() and event.content and event.content.parts:
            final_text = "".join(part.text or "" for part in event.content.parts).strip()
    if not final_text:
        raise LiveSchemaError("Gemini returned no report text")
    return final_text
