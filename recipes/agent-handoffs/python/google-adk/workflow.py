"""Durable trip planning with Google ADK handoffs and synthetic bookings."""

import re
from dataclasses import dataclass
from datetime import timedelta

from cadence import activity, workflow
from cadence.worker import Registry


WORKFLOW = "AgentHandoffsWorkflow"
MOCK_ROUTE_ACTIVITY = "agent-handoffs.mock-route"
BOOK_FLIGHT_ACTIVITY = "agent-handoffs.book-flight"
BOOK_RIDE_ACTIVITY = "agent-handoffs.book-ride"
MODEL_ACTIVITY = "GoogleADKActivities.generate_content_async"
STATUS_QUERY = "status"
LOGICAL_MODEL = "agent-handoffs-worker-model"
COORDINATOR_AGENT_NAME = "book_trip_agent"
SHORT_SPECIALIST_NAME = "plan_short_trip_agent"
LONG_SPECIALIST_NAME = "plan_long_trip_agent"
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
    parts = TO_SPLIT.split(cleaned[start.end() :], maxsplit=1)
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
    for left, right in (
        ("seattle", "san francisco"),
        ("new york", "los angeles"),
    ):
        if left in lower and right in lower:
            return "LONG_TRIP"
    return "SHORT_TRIP"


def _passengers(text: str) -> int:
    match = re.search(r"(\d+)\s+passengers?", text, re.IGNORECASE)
    return max(1, int(match.group(1))) if match else 1


def _departure(text: str) -> str:
    return (
        "2026-10-14T10:00:00"
        if "tomorrow" in text.lower()
        else "2026-10-13T09:00:00"
    )


def _cities(
    route: str, from_address: str, to_address: str, text: str
) -> tuple[str, str]:
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
    """Return a synthetic receipt without contacting an airline."""
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
    """Return a synthetic receipt without contacting a ride service."""
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
    if flight:
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


class AgentHandoffsWorkflow:
    def __init__(self) -> None:
        self._state = "STARTING"
        self._mode = "mock"
        self._route: str | None = None

    @workflow.query(name=STATUS_QUERY)
    def status(self) -> WorkflowStatus:
        return WorkflowStatus(self._state, self._mode, self._route)

    @workflow.run
    async def run(self, recipe_input: RecipeInput) -> RecipeResult:
        if recipe_input.mode not in {"mock", "live"}:
            raise ValueError("mode must be 'mock' or 'live'")
        if not recipe_input.text.strip():
            raise ValueError("text must not be empty")
        self._mode = recipe_input.mode
        if recipe_input.mode == "mock":
            return await self._run_mock(recipe_input.text)
        _parse_addresses(recipe_input.text)
        from inference import generate_live_trip

        self._state = "RUNNING_AGENT"
        outcome = await generate_live_trip(recipe_input.text)
        self._route = outcome.route
        self._state = "COMPLETED"
        return RecipeResult(
            route=outcome.route,
            specialist=outcome.specialist,
            mode="live",
            answer=outcome.answer,
            flight=outcome.flight,
            rides=outcome.rides,
        )

    async def _run_mock(self, text: str) -> RecipeResult:
        plan = await workflow.execute_activity(
            MOCK_ROUTE_ACTIVITY, TripPlan, text, **AI_ACTIVITY_OPTIONS
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
        first = await workflow.execute_activity(
            BOOK_RIDE_ACTIVITY,
            RideReceipt,
            plan.from_address,
            f"{plan.from_city} Airport",
            plan.passengers,
            **TOOL_ACTIVITY_OPTIONS,
        )
        second = await workflow.execute_activity(
            BOOK_RIDE_ACTIVITY,
            RideReceipt,
            f"{plan.to_city} Airport",
            plan.to_address,
            plan.passengers,
            **TOOL_ACTIVITY_OPTIONS,
        )
        return flight, (first, second)


def build_registry(model_activities: object | None = None) -> Registry:
    registry = Registry()
    registry.workflow(AgentHandoffsWorkflow)
    registry.register_activity(mock_route)
    registry.register_activity(book_flight)
    registry.register_activity(book_ride)
    if model_activities is not None:
        registry.register_activities(model_activities)
    return registry
