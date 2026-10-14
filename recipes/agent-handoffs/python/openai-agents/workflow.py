"""Durable trip planning with OpenAI Agents handoffs and synthetic bookings."""

import re
from dataclasses import dataclass
from datetime import timedelta
from agents import Agent, ModelSettings, RunConfig, Runner, function_tool
from cadence import activity, workflow
from cadence.worker import Registry


WORKFLOW = "AgentHandoffsWorkflow"
MOCK_ROUTE_ACTIVITY = "agent-handoffs.mock-route"
BOOK_FLIGHT_ACTIVITY = "agent-handoffs.book-flight"
BOOK_RIDE_ACTIVITY = "agent-handoffs.book-ride"
STATUS_QUERY = "status"
LOGICAL_MODEL = "agent-handoffs-worker-model"
COORDINATOR_AGENT_NAME = "Book trip agent"
SHORT_SPECIALIST_NAME = "Plan short trip agent"
LONG_SPECIALIST_NAME = "Plan long trip agent"
MODEL_ACTIVITY = "OpenAIActivities.invoke_model"
FROM_TO_START = re.compile(r"\bfrom\s+", re.IGNORECASE)
TO_SPLIT = re.compile(r"\s+to\s+", re.IGNORECASE)
TRAILING_CLAUSE = re.compile(
    r"\s+tomorrow\b.*$|\s+for\s+\d+\s+passengers?\b.*$|\s*[.!?]\s*$",
    re.IGNORECASE,
)
FATAL_AI_ERRORS = {
    "LiveAuthenticationError",
    "LiveConfigurationError",
    "LiveSchemaError",
}
AI_ACTIVITY_OPTIONS = {
    "schedule_to_close_timeout": timedelta(seconds=30),
    "retry_policy": {
        "initial_interval": timedelta(seconds=1),
        "backoff_coefficient": 2.0,
        "maximum_attempts": 3,
        "non_retryable_error_reasons": sorted(FATAL_AI_ERRORS),
    },
}
TOOL_ACTIVITY_OPTIONS = {
    "schedule_to_close_timeout": timedelta(seconds=30),
    "retry_policy": {
        "initial_interval": timedelta(seconds=1),
        "backoff_coefficient": 2.0,
        "maximum_attempts": 3,
    },
}
SPECIALIST_MODEL_SETTINGS = ModelSettings(tool_choice="required")


@dataclass(frozen=True)
class RecipeInput:
    text: str
    mode: str = "mock"


@dataclass(frozen=True)
class TripPlan:
    route: str
    from_address: str
    to_address: str
    from_city: str
    to_city: str
    passengers: int
    departure: str


@dataclass(frozen=True)
class FlightReceipt:
    status: str
    from_city: str
    to_city: str
    departure: str
    price: float
    airline: str
    flight_number: str
    seat_number: str


@dataclass(frozen=True)
class RideReceipt:
    status: str
    from_address: str
    to_address: str
    passengers: int
    price: float
    driver_name: str
    driver_phone: str
    driver_car: str
    driver_car_plate: str
    driver_car_color: str


@dataclass(frozen=True)
class WorkflowStatus:
    state: str
    mode: str
    route: str | None


@dataclass(frozen=True)
class RecipeResult:
    route: str
    specialist: str
    mode: str
    answer: str
    flight: FlightReceipt | None
    rides: tuple[RideReceipt, ...]


def _parse_addresses(text: str) -> tuple[str, str]:
    cleaned = text.strip()
    start = FROM_TO_START.search(cleaned)
    if not start:
        raise ValueError("trip request must include from and to addresses")
    remainder = cleaned[start.end() :]
    parts = TO_SPLIT.split(remainder, maxsplit=1)
    if len(parts) != 2:
        raise ValueError("trip request must include from and to addresses")
    from_address = parts[0].strip()
    to_address = TRAILING_CLAUSE.sub("", parts[1]).strip()
    if not from_address or not to_address:
        raise ValueError("trip request must include from and to addresses")
    return from_address, to_address


def _classify_route(text: str) -> str:
    lower = text.lower()
    if "flight" in lower:
        return "LONG_TRIP"
    pairs = (
        ("seattle", "san francisco"),
        ("new york", "los angeles"),
    )
    for left, right in pairs:
        if left in lower and right in lower:
            return "LONG_TRIP"
    return "SHORT_TRIP"


def _passengers(text: str) -> int:
    match = re.search(r"(\d+)\s+passengers?", text, re.IGNORECASE)
    if match:
        return max(1, int(match.group(1)))
    return 1


def _departure(text: str) -> str:
    if "tomorrow" in text.lower():
        return "2026-10-14T10:00:00"
    return "2026-10-13T09:00:00"


def _cities(route: str, from_address: str, to_address: str, text: str) -> tuple[str, str]:
    lower = text.lower()
    if route == "LONG_TRIP" and "seattle" in lower and "san francisco" in lower:
        return "Seattle", "San Francisco"
    if route == "LONG_TRIP" and "new york" in lower and "los angeles" in lower:
        return "New York", "Los Angeles"
    return from_address.split()[0], to_address.split()[0]


def trip_plan_from_text(text: str) -> TripPlan:
    cleaned = text.strip()
    if not cleaned:
        raise ValueError("text must not be empty")
    from_address, to_address = _parse_addresses(cleaned)
    route = _classify_route(cleaned)
    from_city, to_city = _cities(route, from_address, to_address, cleaned)
    return TripPlan(
        route=route,
        from_address=from_address,
        to_address=to_address,
        from_city=from_city,
        to_city=to_city,
        passengers=_passengers(cleaned),
        departure=_departure(cleaned),
    )


@activity.defn(name=MOCK_ROUTE_ACTIVITY)
def mock_route(text: str) -> TripPlan:
    """Deterministic offline routing without catalogs or providers."""
    return trip_plan_from_text(text)


@activity.defn(name=BOOK_FLIGHT_ACTIVITY)
async def book_flight(
    from_city: str, to_city: str, departure: str
) -> FlightReceipt:
    """Return a synthetic flight receipt. This recipe never contacts an airline."""
    if not from_city.strip() or not to_city.strip():
        raise ValueError("flight cities must not be empty")
    if not departure.strip():
        raise ValueError("departure must not be empty")
    return FlightReceipt(
        status="simulated-flight-complete",
        from_city=from_city.strip(),
        to_city=to_city.strip(),
        departure=departure.strip(),
        price=425.0,
        airline="Sample Airways",
        flight_number="SA1234",
        seat_number="12A",
    )


@activity.defn(name=BOOK_RIDE_ACTIVITY)
async def book_ride(
    from_address: str, to_address: str, passengers: int
) -> RideReceipt:
    """Return a synthetic ride receipt. This recipe never contacts a ride service."""
    if not from_address.strip() or not to_address.strip():
        raise ValueError("ride addresses must not be empty")
    if passengers < 1:
        raise ValueError("passengers must be positive")
    return RideReceipt(
        status="simulated-ride-complete",
        from_address=from_address.strip(),
        to_address=to_address.strip(),
        passengers=passengers,
        price=24.50,
        driver_name="Jordan Lee",
        driver_phone="555-0100",
        driver_car="Blue sedan",
        driver_car_plate="SAMPLE1",
        driver_car_color="Blue",
    )


def _format_answer(
    route: str,
    plan: TripPlan,
    flight: FlightReceipt | None,
    rides: tuple[RideReceipt, ...],
) -> str:
    lines = [
        f"Synthetic trip plan ({route.replace('_', ' ').lower()}).",
        f"From `{plan.from_address}` to `{plan.to_address}`.",
    ]
    if flight is not None:
        lines.append(
            f"Flight {flight.flight_number} on {flight.airline} "
            f"({flight.from_city} to {flight.to_city}, seat {flight.seat_number})."
        )
    for index, ride in enumerate(rides, start=1):
        lines.append(
            f"Ride {index}: {ride.from_address} to {ride.to_address} "
            f"for {ride.passengers} passenger(s)."
        )
    return " ".join(lines)


def _route_for_specialist(specialist: str) -> str | None:
    if specialist == LONG_SPECIALIST_NAME:
        return "LONG_TRIP"
    if specialist == SHORT_SPECIALIST_NAME:
        return "SHORT_TRIP"
    return None


def _live_outcome_from_run(
    result: object, text: str
) -> tuple[str, str, FlightReceipt | None, tuple[RideReceipt, ...]]:
    from agents.items import HandoffOutputItem, ToolCallOutputItem

    specialist = COORDINATOR_AGENT_NAME
    route: str | None = None
    flight: FlightReceipt | None = None
    rides: list[RideReceipt] = []
    for item in result.new_items:
        if isinstance(item, HandoffOutputItem):
            specialist = item.target_agent.name
            route = _route_for_specialist(specialist) or route
        if isinstance(item, ToolCallOutputItem):
            output = item.output
            if isinstance(output, FlightReceipt):
                flight = output
            elif isinstance(output, RideReceipt):
                rides.append(output)
    if route is None:
        route = _classify_route(text)
    return route, specialist, flight, tuple(rides)


class AgentHandoffsWorkflow:
    def __init__(self) -> None:
        self._state = "STARTING"
        self._mode = "mock"
        self._route: str | None = None

    @workflow.query(name=STATUS_QUERY)
    def status(self) -> WorkflowStatus:
        return WorkflowStatus(
            state=self._state,
            mode=self._mode,
            route=self._route,
        )

    @workflow.run
    async def run(self, recipe_input: RecipeInput) -> RecipeResult:
        if recipe_input.mode not in {"mock", "live"}:
            raise ValueError("mode must be 'mock' or 'live'")
        if not recipe_input.text.strip():
            raise ValueError("text must not be empty")
        self._mode = recipe_input.mode
        if recipe_input.mode == "mock":
            return await self._run_mock(recipe_input.text)
        return await self._run_live(recipe_input.text)

    async def _run_mock(self, text: str) -> RecipeResult:
        plan = await workflow.execute_activity(
            MOCK_ROUTE_ACTIVITY,
            TripPlan,
            text,
            **AI_ACTIVITY_OPTIONS,
        )
        self._route = plan.route
        self._state = "BOOKING"
        await workflow.sleep(timedelta(seconds=2))
        flight, rides = await self._book_mock_plan(plan)
        self._state = "COMPLETED"
        specialist = (
            SHORT_SPECIALIST_NAME
            if plan.route == "SHORT_TRIP"
            else LONG_SPECIALIST_NAME
        )
        return RecipeResult(
            route=plan.route,
            specialist=specialist,
            mode="mock",
            answer=_format_answer(plan.route, plan, flight, rides),
            flight=flight,
            rides=rides,
        )

    async def _book_mock_plan(
        self, plan: TripPlan
    ) -> tuple[FlightReceipt | None, tuple[RideReceipt, ...]]:
        if plan.route == "SHORT_TRIP":
            ride = await workflow.execute_activity(
                BOOK_RIDE_ACTIVITY,
                RideReceipt,
                plan.from_address,
                plan.to_address,
                plan.passengers,
                **TOOL_ACTIVITY_OPTIONS,
            )
            return None, (ride,)
        flight = await workflow.execute_activity(
            BOOK_FLIGHT_ACTIVITY,
            FlightReceipt,
            plan.from_city,
            plan.to_city,
            plan.departure,
            **TOOL_ACTIVITY_OPTIONS,
        )
        first_leg = await workflow.execute_activity(
            BOOK_RIDE_ACTIVITY,
            RideReceipt,
            plan.from_address,
            f"{plan.from_city} Airport",
            plan.passengers,
            **TOOL_ACTIVITY_OPTIONS,
        )
        second_leg = await workflow.execute_activity(
            BOOK_RIDE_ACTIVITY,
            RideReceipt,
            f"{plan.to_city} Airport",
            plan.to_address,
            plan.passengers,
            **TOOL_ACTIVITY_OPTIONS,
        )
        return flight, (first_leg, second_leg)

    async def _run_live(self, text: str) -> RecipeResult:
        long_trip_agent = Agent(
            name=LONG_SPECIALIST_NAME,
            handoff_description=(
                "Inter-city or flight trips that need a synthetic flight and airport rides."
            ),
            model=LOGICAL_MODEL,
            model_settings=SPECIALIST_MODEL_SETTINGS,
            instructions=(
                "You plan long inter-city trips. Call book_flight once for the cities, "
                "then call book_ride twice to connect local addresses to airports. "
                "Never reply until every required tool returns a synthetic receipt."
            ),
            tools=[
                function_tool(book_flight),
                function_tool(book_ride),
            ],
        )
        short_trip_agent = Agent(
            name=SHORT_SPECIALIST_NAME,
            handoff_description=(
                "Local rides within one city that need only one synthetic ride booking."
            ),
            model=LOGICAL_MODEL,
            model_settings=SPECIALIST_MODEL_SETTINGS,
            instructions=(
                "You plan local rides in one city. Call book_ride exactly once with the "
                "user's from and to addresses and passenger count 1. Never reply until "
                "book_ride returns a synthetic receipt."
            ),
            tools=[function_tool(book_ride)],
        )
        coordinator = Agent(
            name=COORDINATOR_AGENT_NAME,
            model=LOGICAL_MODEL,
            instructions=(
                "You are a trip planner. Hand off to the short-trip specialist only "
                "when pickup and destination are in the same city. Hand off to the "
                "long-trip specialist when cities differ, the request mentions a "
                "flight, or the trip crosses metro areas such as Seattle and San "
                "Francisco. Never call booking tools yourself."
            ),
            handoffs=[short_trip_agent, long_trip_agent],
        )
        self._state = "RUNNING_AGENT"
        result = await Runner.run(
            coordinator,
            text,
            run_config=RunConfig(tracing_disabled=True),
        )
        route, specialist, flight, rides = _live_outcome_from_run(result, text)
        specialist_route = _route_for_specialist(specialist)
        if specialist_route is not None:
            route = specialist_route
        self._route = route
        self._state = "COMPLETED"
        return RecipeResult(
            route=route,
            specialist=specialist,
            mode="live",
            answer=str(result.final_output or "").strip(),
            flight=flight,
            rides=rides,
        )


def build_registry(model_activities: object | None = None) -> Registry:
    registry = Registry()
    registry.workflow(AgentHandoffsWorkflow)
    registry.register_activity(mock_route)
    registry.register_activity(book_flight)
    registry.register_activity(book_ride)
    if model_activities is not None:
        registry.register_activities(model_activities)
    return registry
