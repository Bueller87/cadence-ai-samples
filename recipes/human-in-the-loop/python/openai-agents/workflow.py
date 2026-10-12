"""Durable ride-booking approval orchestration."""

import json
from dataclasses import dataclass
from datetime import timedelta
from textwrap import dedent
from typing import Any

from agents import Agent, RunConfig, Runner, ToolApprovalItem, function_tool
from cadence import activity, workflow
from cadence.worker import Registry


WORKFLOW = "HumanInTheLoopWorkflow"
MOCK_PROPOSAL_ACTIVITY = "human-in-the-loop.mock-proposal"
BOOK_RIDE_ACTIVITY = "human-in-the-loop.book-ride"
STATUS_QUERY = "status"
INTERRUPTIONS_QUERY = "get_interruptions"
APPROVE_SIGNAL = "approve_tool_call"
REJECT_SIGNAL = "reject_tool_call"
LOGICAL_MODEL = "human-in-the-loop-worker-model"
AI_ACTIVITY_OPTIONS = {
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
class RideDetails:
    from_address: str
    to_address: str
    passengers: int


@dataclass(frozen=True)
class PendingRide:
    call_id: str
    tool_name: str
    trip: RideDetails


@dataclass(frozen=True)
class BookingReceipt:
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
    pending: tuple[PendingRide, ...]
    outcome: str | None


@dataclass(frozen=True)
class RecipeResult:
    outcome: str
    mode: str
    trip: RideDetails
    answer: str


@dataclass(frozen=True)
class MarkdownQueryResponse:
    cadenceResponseType: str
    format: str
    data: str


@activity.defn(name=MOCK_PROPOSAL_ACTIVITY)
def mock_proposal(text: str) -> PendingRide:
    """Create a deterministic offline proposal without catalogs or providers."""
    if not text.strip():
        raise ValueError("text must not be empty")
    return PendingRide(
        call_id="mock-ride-1",
        tool_name=BOOK_RIDE_ACTIVITY,
        trip=RideDetails(
            from_address="123 Market Street",
            to_address="1 Ferry Building",
            passengers=1,
        ),
    )


@activity.defn(name=BOOK_RIDE_ACTIVITY)
async def book_ride(
    from_address: str, to_address: str, passengers: int
) -> BookingReceipt:
    """Simulate booking a ride. This recipe never contacts a booking system."""
    if not from_address.strip() or not to_address.strip():
        raise ValueError("ride addresses must not be empty")
    if passengers < 1:
        raise ValueError("passengers must be positive")
    return BookingReceipt(
        status="simulated-booking-complete",
        from_address=from_address,
        to_address=to_address,
        passengers=passengers,
        price=24.50,
        driver_name="Jordan Lee",
        driver_phone="555-0100",
        driver_car="Blue sedan",
        driver_car_plate="SAMPLE1",
        driver_car_color="Blue",
    )


def _pending_ride(item: ToolApprovalItem) -> PendingRide:
    if not item.call_id:
        raise ValueError("model tool call is missing call_id")
    if item.tool_name not in {BOOK_RIDE_ACTIVITY, "book_ride"}:
        raise ValueError("model requested an unsupported tool")
    try:
        arguments = json.loads(item.arguments or "{}")
        trip = RideDetails(
            from_address=str(arguments["from_address"]).strip(),
            to_address=str(arguments["to_address"]).strip(),
            passengers=int(arguments["passengers"]),
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise ValueError("model returned invalid ride arguments") from error
    if not trip.from_address or not trip.to_address or trip.passengers < 1:
        raise ValueError("model returned invalid ride arguments")
    return PendingRide(item.call_id, item.tool_name, trip)


class HumanInTheLoopWorkflow:
    def __init__(self) -> None:
        self._mode = "mock"
        self._state = "STARTING"
        self._pending: dict[str, PendingRide] = {}
        self._decisions: dict[str, bool] = {}
        self._outcome: str | None = None

    @workflow.query(name=STATUS_QUERY)
    def status(self) -> WorkflowStatus:
        return WorkflowStatus(
            state=self._state,
            mode=self._mode,
            pending=tuple(self._pending.values()),
            outcome=self._outcome,
        )

    @workflow.query(name=INTERRUPTIONS_QUERY)
    def interruptions(self) -> MarkdownQueryResponse:
        info = workflow.WorkflowContext.get().info()
        return MarkdownQueryResponse(
            cadenceResponseType="formattedData",
            format="text/markdown",
            data=_render_interruptions_markdown(
                domain=info.workflow_domain,
                workflow_id=info.workflow_id,
                run_id=info.workflow_run_id,
                pending=self._pending,
            ),
        )

    @workflow.signal(name=APPROVE_SIGNAL)
    def approve(self, call_id: str) -> None:
        self._record_decision(call_id, True)

    @workflow.signal(name=REJECT_SIGNAL)
    def reject(self, call_id: str) -> None:
        self._record_decision(call_id, False)

    def _record_decision(self, call_id: str, approved: bool) -> None:
        if call_id in self._pending and call_id not in self._decisions:
            self._decisions[call_id] = approved

    async def _wait_for_decisions(self) -> dict[str, bool]:
        await workflow.wait_condition(
            lambda: all(call_id in self._decisions for call_id in self._pending)
        )
        return dict(self._decisions)

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
        proposal = await workflow.execute_activity(
            MOCK_PROPOSAL_ACTIVITY,
            PendingRide,
            text,
            **AI_ACTIVITY_OPTIONS,
        )
        self._pending = {proposal.call_id: proposal}
        self._state = "WAITING_FOR_DECISION"
        decisions = await self._wait_for_decisions()
        approved = decisions[proposal.call_id]
        self._pending = {}
        self._decisions = {}
        if not approved:
            self._state = "COMPLETED"
            self._outcome = "REJECTED"
            return RecipeResult(
                outcome="REJECTED",
                mode="mock",
                trip=proposal.trip,
                answer="The simulated ride request was rejected.",
            )
        self._state = "BOOKING"
        await workflow.execute_activity(
            BOOK_RIDE_ACTIVITY,
            BookingReceipt,
            proposal.trip.from_address,
            proposal.trip.to_address,
            proposal.trip.passengers,
            schedule_to_close_timeout=timedelta(seconds=10),
        )
        self._state = "COMPLETED"
        self._outcome = "APPROVED_COMPLETED"
        return RecipeResult(
            outcome="APPROVED_COMPLETED",
            mode="mock",
            trip=proposal.trip,
            answer="The simulated ride booking completed.",
        )

    async def _run_live(self, text: str) -> RecipeResult:
        agent = Agent(
            name="Ride booking assistant",
            instructions=(
                "Propose one ride by calling book_ride with the user's requested "
                "pickup, destination, and passenger count. Do not claim a booking "
                "completed unless the tool result says it completed."
            ),
            model=LOGICAL_MODEL,
            tools=[function_tool(book_ride, needs_approval=True)],
        )
        run_input: Any = text
        last_trip: RideDetails | None = None
        approved_any = False
        while True:
            result = await Runner.run(
                agent,
                run_input,
                run_config=RunConfig(tracing_disabled=True),
            )
            if not result.interruptions:
                if last_trip is None:
                    raise ValueError("model completed without proposing a ride")
                self._state = "COMPLETED"
                self._outcome = (
                    "APPROVED_COMPLETED" if approved_any else "REJECTED"
                )
                return RecipeResult(
                    outcome=self._outcome,
                    mode="live",
                    trip=last_trip,
                    answer=str(result.final_output or "").strip(),
                )

            items = {_pending_ride(item).call_id: item for item in result.interruptions}
            self._pending = {
                call_id: _pending_ride(item) for call_id, item in items.items()
            }
            last_trip = next(iter(self._pending.values())).trip
            self._decisions = {}
            self._state = "WAITING_FOR_DECISION"
            decisions = await self._wait_for_decisions()

            state = result.to_state()
            for call_id, item in items.items():
                if decisions[call_id]:
                    approved_any = True
                    state.approve(item)
                else:
                    state.reject(
                        item,
                        rejection_message="The user rejected the simulated ride.",
                    )
            run_input = state
            self._pending = {}
            self._decisions = {}
            self._state = "RUNNING_AGENT"


def _render_interruptions_markdown(
    *,
    domain: str,
    workflow_id: str,
    run_id: str,
    pending: dict[str, PendingRide],
) -> str:
    if not pending:
        return "## Ride approvals\n\n_No ride is awaiting approval._\n"
    sections = [
        "## Ride approvals",
        "",
        "The Workflow is paused for a human decision.",
        "",
    ]
    for proposal in pending.values():
        sections.extend(
            [
                f"### Call `{proposal.call_id}`",
                "",
                f"- From: `{proposal.trip.from_address}`",
                f"- To: `{proposal.trip.to_address}`",
                f"- Passengers: `{proposal.trip.passengers}`",
                "",
                _render_decision_buttons(
                    domain, workflow_id, run_id, proposal.call_id
                ),
                "",
            ]
        )
    return "\n".join(sections)


def _render_decision_buttons(
    domain: str, workflow_id: str, run_id: str, call_id: str
) -> str:
    return dedent(
        f"""\
        {{% signal
            signalName="{APPROVE_SIGNAL}"
            label="Approve"
            domain="{domain}"
            cluster="cluster0"
            workflowId="{workflow_id}"
            runId="{run_id}"
            input="{call_id}"
        /%}}
        {{% signal
            signalName="{REJECT_SIGNAL}"
            label="Reject"
            domain="{domain}"
            cluster="cluster0"
            workflowId="{workflow_id}"
            runId="{run_id}"
            input="{call_id}"
        /%}}"""
    )


def build_registry(model_activities: object | None = None) -> Registry:
    registry = Registry()
    registry.workflow(HumanInTheLoopWorkflow)
    registry.register_activity(mock_proposal)
    registry.register_activity(book_ride)
    if model_activities is not None:
        registry.register_activities(model_activities)
    return registry
