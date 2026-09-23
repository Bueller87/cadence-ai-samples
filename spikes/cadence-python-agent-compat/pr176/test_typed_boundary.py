"""Offline experiment: use the installed SDK, never compatibility_spike.py."""

import pytest
from agents import ModelResponse, ModelSettings, ModelTracing
from agents.usage import Usage
from cadence.contrib.openai import OpenAIActivities
from cadence.contrib.pydantic import PydanticDataConverter
from openai.types.responses import ResponseOutputMessage, ResponseOutputText


@pytest.mark.parametrize("model_input", [
    "Return READY.",
    [{"role": "user", "content": "Return READY."}],
])
def test_original_activity_arguments(model_input):
    # The descriptor supplies the real signature used by the Activity executor.
    # No OpenAIActivities instance/client or provider request is constructed.
    signature = OpenAIActivities.invoke_model.signature
    arguments = dict(
        model_name="synthetic-model", system_instructions=None,
        input=model_input, model_settings=ModelSettings(), tools=[],
        output_schema=None, handoffs=[], tracing=ModelTracing.DISABLED,
        previous_response_id=None, conversation_id=None, prompt=None,
    )
    converter = PydanticDataConverter()
    payload = converter.to_data(signature.params_from_call((), arguments))
    decoded = signature.params_from_payload(converter, payload)
    assert decoded[2] == model_input
    assert isinstance(decoded[3], ModelSettings)
    assert decoded[7] is ModelTracing.DISABLED


def test_original_activity_result():
    response = ModelResponse(
        output=[ResponseOutputMessage(
            id="synthetic-message", type="message", role="assistant",
            status="completed", content=[ResponseOutputText(
                type="output_text", text="READY", annotations=[], logprobs=[],
            )],
        )], usage=Usage(), response_id=None,
    )
    converter = PydanticDataConverter()
    result_type = OpenAIActivities.invoke_model.signature.return_type
    decoded = converter.from_data(converter.to_data([response]), [result_type])[0]
    assert isinstance(decoded, ModelResponse)
    assert decoded.output[0].content[0].text == "READY"


@pytest.fixture(autouse=True)
def forbid_network(monkeypatch):
    import socket

    def blocked(*args, **kwargs):
        raise AssertionError("Network access is forbidden in this experiment")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)
