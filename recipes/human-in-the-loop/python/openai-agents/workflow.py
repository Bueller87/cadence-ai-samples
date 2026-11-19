"""Durable ride-booking approval orchestration."""

from dataclasses import dataclass
from typing import Any

from agents import Agent, RunConfig, Runner, ToolApprovalItem, function_tool
from cadence import activity, workflow
from cadence.worker import Registry

WORKFLOW = "HumanInTheLoopWorkflow"
BOOK_RIDE_ACTIVITY = "human-in-the-loop.book-ride"
APPROVE_SIGNAL = "approve_tool_call"
REJECT_SIGNAL = "reject_tool_call"


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


# Shape that Cadence Web expects to render a query result as markdown.
@dataclass(frozen=True)
class MarkdownQueryResponse:
    cadenceResponseType: str
    format: str
    data: str


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


class HumanInTheLoopWorkflow:
    def __init__(self) -> None:
        # Tool calls awaiting a decision, keyed by call_id.
        self._pending: dict[str, ToolApprovalItem] = {}
        # Decisions delivered via signals: call_id -> True (approve) / False (reject).
        self._decisions: dict[str, bool] = {}

    @workflow.query(name="get_interruptions")
    def get_interruptions(self) -> MarkdownQueryResponse:
        info = workflow.WorkflowContext.get().info()
        return MarkdownQueryResponse(
            cadenceResponseType="formattedData",
            format="text/markdown",
            data=_render_interruptions_markdown(info, self._pending),
        )

    @workflow.signal(name=APPROVE_SIGNAL)
    def approve_tool_call(self, call_id: str) -> None:
        self._decide(call_id, True)

    @workflow.signal(name=REJECT_SIGNAL)
    def reject_tool_call(self, call_id: str) -> None:
        self._decide(call_id, False)

    def _decide(self, call_id: str, approved: bool) -> None:
        # Unknown, stale and repeated call IDs are ignored; the first decision wins.
        if call_id in self._pending and call_id not in self._decisions:
            self._decisions[call_id] = approved

    @workflow.run
    async def run(self, text: str) -> str:
        agent = Agent(
            name="Ride booking assistant",
            instructions=(
                "Propose one ride by calling book_ride with the user's requested "
                "pickup, destination, and passenger count. Do not claim a booking "
                "completed unless the tool result says it completed."
            ),
            model="human-in-the-loop-worker-model",
            tools=[function_tool(book_ride, needs_approval=True)],
        )

        run_config = RunConfig(tracing_disabled=True)
        run_input: Any = text

        while True:
            result = await Runner.run(agent, run_input, run_config=run_config)

            if not result.interruptions:
                return str(result.final_output)

            for item in result.interruptions:
                if not item.call_id:
                    raise RuntimeError(f"tool call {item.tool_name} has no call_id")
                self._pending[item.call_id] = item

            self._decisions = {}

            # Block until every pending tool call has an approve/reject signal.
            await workflow.wait_condition(
                lambda: all(call_id in self._decisions for call_id in self._pending)
            )

            # Resume from the existing run state with each decision applied.
            state = result.to_state()
            for call_id, item in self._pending.items():
                if self._decisions[call_id]:
                    state.approve(item)
                else:
                    state.reject(item, rejection_message="User rejected the tool call.")

            run_input = state
            self._pending = {}
            self._decisions = {}


def _render_interruptions_markdown(
    info: workflow.WorkflowInfo, pending: dict[str, ToolApprovalItem]
) -> str:
    if not pending:
        return "## Tool Approvals\n\n_No tool calls are awaiting approval._\n"

    sections = [
        "## Tool Approvals",
        "",
        "The agent is paused. Approve or reject each pending tool call below.",
        "",
    ]
    for item in pending.values():
        sections += [f"### `{item.tool_name}`", "", f"- **Call ID:** `{item.call_id}`"]
        if item.arguments:
            sections += ["- **Arguments:**", "", "```json", item.arguments, "```"]
        sections.append("")
        for signal, label in ((APPROVE_SIGNAL, "Approve"), (REJECT_SIGNAL, "Reject")):
            sections += [
                "{% signal",
                f'    signalName="{signal}"',
                f'    label="{label}"',
                f'    domain="{info.workflow_domain}"',
                '    cluster="cluster0"',
                f'    workflowId="{info.workflow_id}"',
                f'    runId="{info.workflow_run_id}"',
                f'    input="{item.call_id}"',
                "/%}",
            ]
        sections.append("")
    return "\n".join(sections)


def build_registry(model_activities: object) -> Registry:
    registry = Registry()
    registry.workflow(HumanInTheLoopWorkflow)
    registry.register_activity(book_ride)
    registry.register_activities(model_activities)
    return registry
