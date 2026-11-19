"""Offline fake model served by the Worker; the Workflow never knows it is a mock."""

import json
import os
from typing import Any

from agents import Model, ModelResponse, Usage
from cadence.contrib.openai import OpenAIActivities
from openai.types.responses import (
    ResponseFunctionToolCall,
    ResponseOutputMessage,
    ResponseOutputText,
)

CALL_ID = "mock-ride-1"
RIDE = {
    "from_address": "123 Market Street",
    "to_address": "1 Ferry Building",
    "passengers": 1,
}
BOOKED_MARKER = "simulated-booking-complete"


class MockModel(Model):
    """Proposes one ride, then reports the human decision from the tool result."""

    async def get_response(self, *args: Any, **kwargs: Any) -> ModelResponse:
        tool_results = [
            item
            for item in kwargs["input"]
            if isinstance(item, dict) and item.get("type") == "function_call_output"
        ] if isinstance(kwargs["input"], list) else []

        if tool_results:
            # Approval produces the booking receipt; rejection produces the
            # Workflow's rejection message instead.
            booked = BOOKED_MARKER in str(tool_results[-1].get("output"))
            text = (
                "The simulated ride booking completed."
                if booked
                else "The simulated ride request was rejected."
            )
            return _response(
                ResponseOutputMessage(
                    id="mock-message-1",
                    role="assistant",
                    status="completed",
                    type="message",
                    content=[
                        ResponseOutputText(
                            text=text, annotations=[], type="output_text"
                        )
                    ],
                )
            )

        return _response(
            ResponseFunctionToolCall(
                call_id=CALL_ID,
                name=kwargs["tools"][0].name,
                arguments=json.dumps(RIDE),
                type="function_call",
            )
        )

    def stream_response(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError("the Cadence adapter does not stream")


def _response(item: Any) -> ModelResponse:
    return ModelResponse(output=[item], usage=Usage(), response_id=None)


class MockModelProvider:
    def get_model(self, _model_name: str | None = None) -> Model:
        return MockModel()


class MockOpenAIActivities(OpenAIActivities):
    """Model Activities that answer from the fake model instead of a provider."""

    def __init__(self) -> None:
        self._openai_provider = MockModelProvider()


def mock_model_activities() -> OpenAIActivities:
    # CadenceModel builds a proxy OpenAIActivities in Workflow code, and the
    # OpenAI client constructor requires a key it never uses.
    os.environ.setdefault("OPENAI_API_KEY", "mock")
    return MockOpenAIActivities()
