import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from cadence.contrib.pydantic import PydanticDataConverter
from cadence.testing import TestWorkflowEnvironment

from config import load_model
from inference import build_model_activities
from live import live_model_activities
from workflow import (
    BOOK_FLIGHT_ACTIVITY,
    BOOK_RIDE_ACTIVITY,
    COORDINATOR_AGENT_NAME,
    LONG_SPECIALIST_NAME,
    MODEL_ACTIVITY,
    SHORT_SPECIALIST_NAME,
    TOOL_ACTIVITY_OPTIONS,
    WORKFLOW,
    FlightReceipt,
    RecipeInput,
    RecipeResult,
    RideReceipt,
    _live_outcome_from_run,
    build_registry,
)


ROOT = next(
    parent
    for parent in Path(__file__).resolve().parents
    if (parent / "models.yaml").is_file()
)
SHORT_TRIP_TEXT = (
    "Book a ride from 123 Market Street to 1 Ferry Building in San Francisco."
)
LONG_TRIP_TEXT = (
    "Book a trip for me from Uber Seattle Office to Uber San Francisco Office "
    "tomorrow at 10:00 AM."
)


def _handoff_run(
    *,
    specialist_name: str,
    flight: FlightReceipt | None,
    rides: tuple[RideReceipt, ...],
    answer: str = "Synthetic trip complete.",
) -> SimpleNamespace:
    from agents import Agent
    from agents.items import HandoffOutputItem, ToolCallOutputItem

    coordinator = Agent(
        name=COORDINATOR_AGENT_NAME,
        model="agent-handoffs-worker-model",
        instructions="coordinate",
    )
    specialist = Agent(
        name=specialist_name,
        model="agent-handoffs-worker-model",
        instructions="specialist",
    )
    items = [
        HandoffOutputItem(
            agent=specialist,
            raw_item={"role": "user", "content": "handoff"},
            source_agent=coordinator,
            target_agent=specialist,
        )
    ]
    for ride in rides:
        items.append(
            ToolCallOutputItem(
                agent=specialist,
                raw_item={"type": "function_call_output"},
                output=ride,
            )
        )
    if flight is not None:
        items.append(
            ToolCallOutputItem(
                agent=specialist,
                raw_item={"type": "function_call_output"},
                output=flight,
            )
        )
    return SimpleNamespace(final_output=answer, new_items=items)


class LiveOutcomeTests(unittest.TestCase):
    def test_live_outcome_extracts_specialist_and_receipts(self) -> None:
        ride = RideReceipt(
            "simulated-ride-complete",
            "A",
            "B",
            1,
            24.5,
            "Jordan Lee",
            "555-0100",
            "Blue sedan",
            "SAMPLE1",
            "Blue",
        )
        run = _handoff_run(
            specialist_name=SHORT_SPECIALIST_NAME,
            flight=None,
            rides=(ride,),
        )
        route, specialist, flight, rides = _live_outcome_from_run(
            run, SHORT_TRIP_TEXT
        )
        self.assertEqual((route, specialist), ("SHORT_TRIP", SHORT_SPECIALIST_NAME))
        self.assertIsNone(flight)
        self.assertEqual(rides, (ride,))


class LiveWorkflowTests(unittest.IsolatedAsyncioTestCase):
    async def test_live_short_handoff_result_includes_ride_receipt(self) -> None:
        ride = RideReceipt(
            "simulated-ride-complete",
            "123 Market Street",
            "1 Ferry Building",
            1,
            24.5,
            "Jordan Lee",
            "555-0100",
            "Blue sedan",
            "SAMPLE1",
            "Blue",
        )
        run = _handoff_run(
            specialist_name=SHORT_SPECIALIST_NAME,
            flight=None,
            rides=(ride,),
        )
        with (
            patch("workflow.Runner.run", new=AsyncMock(return_value=run)),
            TestWorkflowEnvironment(
                build_registry(), data_converter=PydanticDataConverter()
            ) as environment,
        ):
            execution = await environment.client.start_workflow(
                WORKFLOW,
                RecipeInput(SHORT_TRIP_TEXT, "live"),
                workflow_id="live-short-handoff",
                task_list="test-task-list",
            )
            result = environment.get_workflow_result(
                RecipeResult, execution.workflow_id, execution.run_id
            )
        self.assertEqual(result.mode, "live")
        self.assertEqual(result.specialist, SHORT_SPECIALIST_NAME)
        self.assertEqual(len(result.rides), 1)
        self.assertEqual(result.rides[0].driver_car_plate, "SAMPLE1")

    async def test_live_long_handoff_result_includes_flight_and_rides(self) -> None:
        flight = FlightReceipt(
            "simulated-flight-complete",
            "Seattle",
            "San Francisco",
            "2026-10-14T10:00:00",
            425.0,
            "Sample Airways",
            "SA1234",
            "12A",
        )
        rides = (
            RideReceipt(
                "simulated-ride-complete",
                "Office",
                "Seattle Airport",
                1,
                24.5,
                "Jordan Lee",
                "555-0100",
                "Blue sedan",
                "SAMPLE1",
                "Blue",
            ),
            RideReceipt(
                "simulated-ride-complete",
                "San Francisco Airport",
                "Office",
                1,
                24.5,
                "Jordan Lee",
                "555-0100",
                "Blue sedan",
                "SAMPLE1",
                "Blue",
            ),
        )
        run = _handoff_run(
            specialist_name=LONG_SPECIALIST_NAME,
            flight=flight,
            rides=rides,
        )
        with (
            patch("workflow.Runner.run", new=AsyncMock(return_value=run)),
            TestWorkflowEnvironment(
                build_registry(), data_converter=PydanticDataConverter()
            ) as environment,
        ):
            execution = await environment.client.start_workflow(
                WORKFLOW,
                RecipeInput(LONG_TRIP_TEXT, "live"),
                workflow_id="live-long-handoff",
                task_list="test-task-list",
            )
            result = environment.get_workflow_result(
                RecipeResult, execution.workflow_id, execution.run_id
            )
        self.assertEqual(result.specialist, LONG_SPECIALIST_NAME)
        self.assertEqual(result.route, "LONG_TRIP")
        self.assertIsNotNone(result.flight)
        self.assertEqual(len(result.rides), 2)

    async def test_live_handoff_without_tool_outputs_leaves_receipts_empty(self) -> None:
        run = _handoff_run(
            specialist_name=SHORT_SPECIALIST_NAME,
            flight=None,
            rides=(),
            answer="Handoff complete.",
        )
        with (
            patch("workflow.Runner.run", new=AsyncMock(return_value=run)),
            TestWorkflowEnvironment(
                build_registry(), data_converter=PydanticDataConverter()
            ) as environment,
        ):
            execution = await environment.client.start_workflow(
                WORKFLOW,
                RecipeInput(SHORT_TRIP_TEXT, "live"),
                workflow_id="live-no-tools",
                task_list="test-task-list",
            )
            result = environment.get_workflow_result(
                RecipeResult, execution.workflow_id, execution.run_id
            )
        self.assertEqual(result.mode, "live")
        self.assertEqual(result.specialist, SHORT_SPECIALIST_NAME)
        self.assertEqual(result.rides, ())
        self.assertIsNone(result.flight)

    async def test_booking_activity_failure_fails_mock_workflow(self) -> None:
        async def failing_ride(*args: object) -> None:
            raise RuntimeError("synthetic booking unavailable")

        with TestWorkflowEnvironment(
            build_registry(), data_converter=PydanticDataConverter()
        ) as environment:
            environment.on_activity(BOOK_RIDE_ACTIVITY, fn=failing_ride)
            execution = await environment.client.start_workflow(
                WORKFLOW,
                RecipeInput(SHORT_TRIP_TEXT),
                workflow_id="mock-booking-failure",
                task_list="test-task-list",
            )
            error = environment.get_workflow_error(execution.workflow_id)
        self.assertIsNotNone(error)


class ToolPolicyTests(unittest.TestCase):
    def test_booking_tools_use_bounded_timeout_and_retries(self) -> None:
        self.assertEqual(
            TOOL_ACTIVITY_OPTIONS["schedule_to_close_timeout"].total_seconds(),
            30,
        )
        policy = TOOL_ACTIVITY_OPTIONS["retry_policy"]
        self.assertEqual(policy["maximum_attempts"], 3)
        self.assertEqual(policy["backoff_coefficient"], 2.0)

    def test_live_worker_registry_includes_model_activity(self) -> None:
        selection = load_model(ROOT, "llama3.2-local")
        with patch("inference.AsyncOpenAI"):
            registry = build_registry(live_model_activities(selection, {}))
        self.assertIsNotNone(registry.get_activity(MODEL_ACTIVITY))
        self.assertIsNotNone(registry.get_activity(BOOK_RIDE_ACTIVITY))
        self.assertIsNotNone(registry.get_activity(BOOK_FLIGHT_ACTIVITY))


if __name__ == "__main__":
    unittest.main()
