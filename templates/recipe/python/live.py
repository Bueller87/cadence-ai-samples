"""Worker-side Jev classification and Google ADK model Activities."""

import json
import os
from collections.abc import AsyncGenerator, Callable, MutableMapping
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlsplit
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
from workflow import AI_OPTIONS, CLASSIFY_ACTIVITY, ClassificationDecision


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
        raise LiveConfigurationError(
            "only jev-default is implemented; other classifiers are catalog candidates"
        )
    if selection.agent.framework not in {"google-adk", "openai-agents"}:
        raise LiveConfigurationError("unsupported agent framework")
    if selection.model.provider not in {"google", "ollama", "openai"}:
        raise LiveConfigurationError("unsupported model provider")
    endpoint = urlsplit(selection.model.endpoint)
    if (endpoint.scheme not in {"http", "https"} or not endpoint.hostname
            or endpoint.username or endpoint.password or endpoint.query or endpoint.fragment):
        raise LiveConfigurationError("model endpoint must be a credential-free HTTP URL")
    if (selection.model.provider == "google"
            and selection.model.endpoint.rstrip("/") != GOOGLE_ENDPOINT):
        raise LiveConfigurationError("Google models require the official endpoint")
    if (selection.model.provider == "ollama"
            and endpoint.hostname not in {"localhost", "127.0.0.1", "::1"}):
        raise LiveConfigurationError("Ollama requires a loopback endpoint")


def live_activities(selection: CatalogSelection,
                    environ: MutableMapping[str, str] = os.environ) -> tuple["JevClassifier", object]:
    validate_live(selection)
    model_key = environ.get("MODEL_AI_KEY", "").strip()
    classifier_key = environ.get("CLASSIFIER_AI_KEY", "").strip()
    if (selection.model.provider != "ollama" and not model_key) or not classifier_key:
        raise LiveConfigurationError(
            "live worker requires MODEL_AI_KEY and CLASSIFIER_AI_KEY"
        )
    from inference import build_model_activities

    return (JevClassifier(classifier_key, selection.classifier.endpoint,
                          selection.classifier.model),
            build_model_activities(selection, environ))


class JevClassifier:
    def __init__(self, api_key: str, endpoint: str, model: str,
                 opener: Callable[..., Any] = urlopen) -> None:
        self._api_key = api_key
        self._endpoint = endpoint
        self._model = model
        self._open = opener

    @activity.method(name=CLASSIFY_ACTIVITY)
    def classify(self, text: str) -> ClassificationDecision:
        question = {
            "type": "choice",
            "instructions": "Does this input warrant generative analysis?",
            "criteria": {
                "yes": "Flexible analysis would add useful information.",
                "no": "Deterministic handling is sufficient.",
            },
        }
        request = Request(
            self._endpoint,
            data=json.dumps({"state": text, "model": self._model,
                             "questions": {"analyze": question}}).encode(),
            headers={"Authorization": f"Bearer {self._api_key}",
                     "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with self._open(request, timeout=20) as response:
                answer = json.loads(response.read(1_048_577))["answers"]["analyze"]
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
        choice, confidence = answer.get("choice"), answer.get("confidence")
        if answer.get("type") != "choice" or choice not in {"yes", "no"}:
            raise LiveSchemaError("Jev returned an invalid choice")
        if not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
            raise LiveSchemaError("Jev returned an invalid confidence")
        return ClassificationDecision(choice == "yes",
                                      f"Jev selected {choice} ({confidence:.2f})")


def raise_model_error(error: Exception) -> None:
    """Keep provider bodies, which may echo headers, out of Activity failures."""
    status = getattr(error, "status_code", None)
    if status in {401, 403}:
        raise LiveAuthenticationError("model rejected MODEL_AI_KEY") from None
    if isinstance(status, int) and not retryable(status):
        raise LiveSchemaError(f"model rejected request: HTTP {status}") from None
    if isinstance(error, (TypeError, ValueError)):
        raise LiveSchemaError("model configuration or schema is invalid") from None
    raise RuntimeError("temporary model request failure") from None


class ADKActivities(GoogleADKActivities):
    @activity.override(GoogleADKActivities.generate_content_async)
    async def generate_content_async(self, model_name: str,
                                     llm_request: LlmRequest) -> list[LlmResponse]:
        try:
            return await super().generate_content_async(model_name, llm_request)
        except genai_errors.APIError as error:
            details = error.details if isinstance(error.details, dict) else {}
            details = details.get("error", details)
            details = details.get("details", []) if isinstance(details, dict) else []
            invalid_key = any(isinstance(item, dict) and item.get("reason") == "API_KEY_INVALID"
                              for item in details if isinstance(details, list))
            if error.code in {401, 403} or invalid_key:
                raise LiveAuthenticationError("Gemini rejected MODEL_AI_KEY") from None
            if retryable(error.code):
                raise RuntimeError(f"temporary model failure: HTTP {error.code}") from None
            raise LiveSchemaError(f"model rejected request: HTTP {error.code}") from None
        except Exception as error:
            raise_model_error(error)


class RetryingCadenceModel(CadenceModel):
    async def generate_content_async(self, llm_request: LlmRequest,
                                     stream: bool = False) -> AsyncGenerator[LlmResponse, None]:
        if stream:
            raise RuntimeError("streaming is not supported")
        call = self._google_adk_activities.generate_content_async.with_options(**AI_OPTIONS)
        for response in await call(model_name=self.model, llm_request=llm_request):
            yield response


async def generate_adk_output(text: str, model_name: str) -> str:
    agent = LlmAgent(name="__PYTHON_PACKAGE__", model=RetryingCadenceModel(model=model_name),
                     instruction="Analyze the input and return a concise useful result.")
    sessions = InMemorySessionService()
    runner = CadenceAgentRunner(app_name="__RECIPE_SLUG__", agent=agent,
                                session_service=sessions)
    session_id = workflow.WorkflowContext.get().info().workflow_id
    await sessions.create_session(app_name=runner.app_name, user_id="recipe",
                                  session_id=session_id)
    output = ""
    async for event in runner.run_async(
        user_id="recipe", session_id=session_id,
        new_message=types.Content(role="user", parts=[types.Part.from_text(text=text)]),
    ):
        if event.is_final_response() and event.content and event.content.parts:
            output = "".join(part.text or "" for part in event.content.parts).strip()
    if not output:
        raise LiveSchemaError("model returned no output")
    return output
