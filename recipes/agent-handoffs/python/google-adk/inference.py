"""Google ADK handoff orchestration and Worker-local provider activities."""

from collections.abc import AsyncGenerator, Mapping, MutableMapping
from dataclasses import asdict, dataclass, fields, is_dataclass
from typing import Any, TypeVar

from cadence import activity, workflow
from cadence.contrib.google_adk import CadenceAgentRunner, GoogleADKActivities
from cadence.contrib.google_adk.cadence_model import CadenceModel
from google.adk.agents import LlmAgent
from google.adk.models.google_llm import Gemini
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.adk.sessions import InMemorySessionService
from google.genai import errors as genai_errors
from google.genai import types

from config import ModelConfig
from live import (
    LiveAuthenticationError,
    LiveConfigurationError,
    LiveSchemaError,
    raise_model_error,
    retryable,
    validate_live,
)
from workflow import (
    AI_ACTIVITY_OPTIONS,
    COORDINATOR_AGENT_NAME,
    LOGICAL_MODEL,
    LONG_SPECIALIST_NAME,
    SHORT_SPECIALIST_NAME,
    TOOL_ACTIVITY_OPTIONS,
    FlightReceipt,
    RideReceipt,
    TripPlan,
    book_flight as book_flight_activity,
    book_ride as book_ride_activity,
    trip_plan_from_text,
)


@dataclass(frozen=True)
class LiveTripOutcome:
    route: str
    specialist: str
    answer: str
    flight: FlightReceipt | None
    rides: tuple[RideReceipt, ...]


def runtime_model(provider: str, model: str) -> str:
    if provider == "google":
        return model
    if provider == "ollama":
        return f"ollama_chat/{model}"
    if provider == "openai":
        return f"openai/{model}"
    raise LiveConfigurationError("unsupported model provider")


def build_model_activities(
    selection: ModelConfig, environ: MutableMapping[str, str]
) -> "ADKActivities":
    validate_live(selection)
    key = environ.get("MODEL_AI_KEY", "").strip()
    if selection.provider != "ollama" and not key:
        raise LiveConfigurationError("live worker requires MODEL_AI_KEY")
    environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
    environ["LITELLM_TELEMETRY"] = "False"
    if selection.provider == "google":
        environ["GOOGLE_API_KEY"] = key
        environ["GOOGLE_GENAI_USE_VERTEXAI"] = "FALSE"
    elif selection.provider == "openai":
        environ["OPENAI_API_KEY"] = key
        environ["OPENAI_BASE_URL"] = selection.endpoint
    else:
        environ["OLLAMA_API_BASE"] = selection.endpoint
    if selection.provider != "google":
        import litellm

        litellm.num_retries = 0
        litellm.telemetry = False
        litellm.suppress_debug_info = True
    return ADKActivities(selection)


class ADKActivities(GoogleADKActivities):
    """Resolve the Workflow's logical model through the Worker's selection."""

    def __init__(self, selection: ModelConfig) -> None:
        self._selection = selection

    @activity.override(GoogleADKActivities.generate_content_async)
    async def generate_content_async(
        self, model_name: str, llm_request: LlmRequest
    ) -> list[LlmResponse]:
        del model_name
        actual_model = runtime_model(
            self._selection.provider, self._selection.model
        )
        runtime_request = llm_request.model_copy(
            update={"model": actual_model}
        )
        try:
            if self._selection.provider == "google":
                model = Gemini(
                    model=self._selection.model,
                    base_url=self._selection.endpoint,
                )
                responses = []
                async for response in model.generate_content_async(
                    llm_request=runtime_request, stream=False
                ):
                    responses.append(response)
                return responses
            return await super().generate_content_async(
                actual_model, runtime_request
            )
        except genai_errors.APIError as error:
            if error.code in {401, 403} or _contains_invalid_key(error.details):
                raise LiveAuthenticationError(
                    "model rejected MODEL_AI_KEY"
                ) from None
            if retryable(error.code):
                raise RuntimeError(
                    f"temporary model request failure: HTTP {error.code}"
                ) from None
            raise LiveSchemaError(
                f"model rejected request: HTTP {error.code}"
            ) from None
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


async def book_flight(
    from_city: str, to_city: str, departure: str
) -> dict[str, Any]:
    """Book one synthetic flight and return its typed receipt."""
    call = book_flight_activity.with_options(**TOOL_ACTIVITY_OPTIONS)
    return asdict(await call(from_city, to_city, departure))


async def book_ride(
    from_address: str, to_address: str, passengers: int
) -> dict[str, Any]:
    """Book one synthetic ride and return its typed receipt."""
    call = book_ride_activity.with_options(**TOOL_ACTIVITY_OPTIONS)
    return asdict(await call(from_address, to_address, passengers))


class _BookingCallTracker:
    """Require every accepted booking response to match an observed call."""

    _NAMES = {"book_flight", "book_ride"}

    def __init__(self) -> None:
        self._identified: dict[str, str] = {}
        self._anonymous: list[str] = []
        self._invalid = False

    def observe(self, function_call: types.FunctionCall) -> None:
        if function_call.name not in self._NAMES:
            return
        if function_call.id:
            if function_call.id in self._identified:
                self._invalid = True
                return
            self._identified[function_call.id] = function_call.name
        else:
            self._anonymous.append(function_call.name)

    def accept(self, response: types.FunctionResponse) -> bool:
        if response.name not in self._NAMES:
            return False
        if response.id:
            observed_name = self._identified.pop(response.id, None)
            if observed_name != response.name:
                self._invalid = True
                return False
            return not self._invalid
        try:
            index = self._anonymous.index(response.name)
        except ValueError:
            self._invalid = True
            return False
        self._anonymous.pop(index)
        return not self._invalid

    def require_complete(self) -> None:
        if self._invalid or self._identified or self._anonymous:
            raise LiveSchemaError(
                "booking calls and responses did not correlate"
            )


def _agent_tree() -> LlmAgent:
    model = RetryingCadenceModel(model=LOGICAL_MODEL)
    short_trip = LlmAgent(
        name=SHORT_SPECIALIST_NAME,
        description="Local same-city trips requiring one synthetic ride.",
        model=model,
        instruction=(
            "Book a local trip. Call book_ride exactly once using the user's "
            "from and to addresses and passenger count. Do not call any other "
            "tool. Reply only after the tool returns its receipt."
        ),
        tools=[book_ride],
        disallow_transfer_to_parent=True,
        disallow_transfer_to_peers=True,
    )
    long_trip = LlmAgent(
        name=LONG_SPECIALIST_NAME,
        description=(
            "Inter-city or explicit-flight trips requiring a flight and airport rides."
        ),
        model=model,
        instruction=(
            "Book an inter-city trip. Call book_flight exactly once, then call "
            "book_ride exactly twice for the origin-to-airport and "
            "airport-to-destination legs. Reply only after all receipts return."
        ),
        tools=[book_flight, book_ride],
        disallow_transfer_to_parent=True,
        disallow_transfer_to_peers=True,
    )
    return LlmAgent(
        name=COORDINATOR_AGENT_NAME,
        description="Routes trip requests to the correct booking specialist.",
        model=model,
        instruction=(
            "Transfer same-city trips to plan_short_trip_agent. Transfer trips "
            "between cities, across metro areas, or explicitly requesting a "
            "flight to plan_long_trip_agent. Never call booking tools or answer "
            "the request yourself."
        ),
        sub_agents=[short_trip, long_trip],
    )


async def generate_live_trip(text: str) -> LiveTripOutcome:
    sessions = InMemorySessionService()
    runner = CadenceAgentRunner(
        app_name="agent-handoffs",
        agent=_agent_tree(),
        session_service=sessions,
    )
    session_id = workflow.WorkflowContext.get().info().workflow_id
    await sessions.create_session(
        app_name=runner.app_name,
        user_id="trip-planner",
        session_id=session_id,
    )
    transfers: list[str] = []
    flight: FlightReceipt | None = None
    rides: list[RideReceipt] = []
    booking_calls = _BookingCallTracker()
    answer = ""
    async for event in runner.run_async(
        user_id="trip-planner",
        session_id=session_id,
        new_message=types.Content(
            role="user", parts=[types.Part.from_text(text=text)]
        ),
    ):
        transfer = getattr(event.actions, "transfer_to_agent", None)
        if transfer:
            transfers.append(transfer)
        if event.content:
            for part in event.content.parts or []:
                if part.function_call:
                    booking_calls.observe(part.function_call)
                response = part.function_response
                if not response or not booking_calls.accept(response):
                    continue
                if response.name == "book_flight":
                    if flight is not None:
                        raise LiveSchemaError("book_flight was called more than once")
                    flight = _receipt(response.response, FlightReceipt)
                elif response.name == "book_ride":
                    rides.append(_receipt(response.response, RideReceipt))
        if event.is_final_response() and event.content:
            answer = "".join(
                part.text or "" for part in event.content.parts or []
            ).strip()
    booking_calls.require_complete()
    return _validated_outcome(
        transfers,
        answer,
        flight,
        rides,
        expected_plan=trip_plan_from_text(text),
    )


def _validated_outcome(
    transfers: list[str],
    answer: str,
    flight: FlightReceipt | None,
    rides: list[RideReceipt],
    *,
    expected_plan: TripPlan,
) -> LiveTripOutcome:
    if len(transfers) != 1:
        raise LiveSchemaError("live run must transfer to exactly one specialist")
    specialist = transfers[0]
    if specialist == SHORT_SPECIALIST_NAME:
        if flight is not None or len(rides) != 1:
            raise LiveSchemaError(
                "short-trip specialist must book one ride and no flight"
            )
        route = "SHORT_TRIP"
    elif specialist == LONG_SPECIALIST_NAME:
        if flight is None or len(rides) != 2:
            raise LiveSchemaError(
                "long-trip specialist must book one flight and two rides"
            )
        route = "LONG_TRIP"
    else:
        raise LiveSchemaError("live run transferred to an unknown specialist")
    if route != expected_plan.route:
        raise LiveSchemaError("live run transferred to the wrong specialist")
    if route == "SHORT_TRIP":
        expected_rides = {
            (
                expected_plan.from_address,
                expected_plan.to_address,
                expected_plan.passengers,
            )
        }
    else:
        assert flight is not None
        if (
            flight.from_city,
            flight.to_city,
            flight.departure,
        ) != (
            expected_plan.from_city,
            expected_plan.to_city,
            expected_plan.departure,
        ):
            raise LiveSchemaError("book_flight returned the wrong trip")
        expected_rides = {
            (
                expected_plan.from_address,
                f"{expected_plan.from_city} Airport",
                expected_plan.passengers,
            ),
            (
                f"{expected_plan.to_city} Airport",
                expected_plan.to_address,
                expected_plan.passengers,
            ),
        }
    actual_rides = {
        (ride.from_address, ride.to_address, ride.passengers) for ride in rides
    }
    if actual_rides != expected_rides:
        raise LiveSchemaError("book_ride returned the wrong trip")
    if not answer:
        raise LiveSchemaError("specialist returned no final response")
    return LiveTripOutcome(
        route=route,
        specialist=specialist,
        answer=answer,
        flight=flight,
        rides=tuple(rides),
    )


Receipt = TypeVar("Receipt", FlightReceipt, RideReceipt)


def _receipt(value: Any, receipt_type: type[Receipt]) -> Receipt:
    if isinstance(value, receipt_type):
        return value
    if is_dataclass(value):
        value = asdict(value)
    if isinstance(value, Mapping) and set(value) == {"result"}:
        value = value["result"]
        if is_dataclass(value):
            value = asdict(value)
    if not isinstance(value, Mapping):
        raise LiveSchemaError(f"{receipt_type.__name__} tool returned invalid data")
    allowed = {field.name for field in fields(receipt_type)}
    if set(value) != allowed:
        raise LiveSchemaError(f"{receipt_type.__name__} tool returned invalid data")
    try:
        return receipt_type(**value)
    except (TypeError, ValueError):
        raise LiveSchemaError(
            f"{receipt_type.__name__} tool returned invalid data"
        ) from None


def _contains_invalid_key(value: object) -> bool:
    if isinstance(value, dict):
        return any(
            (key == "reason" and item == "API_KEY_INVALID")
            or _contains_invalid_key(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_contains_invalid_key(item) for item in value)
    return False
