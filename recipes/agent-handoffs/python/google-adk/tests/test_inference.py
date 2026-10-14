import unittest
from dataclasses import asdict

from cadence.contrib.pydantic import PydanticDataConverter
from cadence.testing import TestWorkflowEnvironment
from google.adk.models.llm_response import LlmResponse
from google.genai import types

from inference import _BookingCallTracker, _agent_tree
from live import LiveSchemaError
from workflow import (
    AI_ACTIVITY_OPTIONS,
    BOOK_FLIGHT_ACTIVITY,
    BOOK_RIDE_ACTIVITY,
    LONG_SPECIALIST_NAME,
    MODEL_ACTIVITY,
    SHORT_SPECIALIST_NAME,
    TOOL_ACTIVITY_OPTIONS,
    WORKFLOW,
    FlightReceipt,
    RecipeInput,
    RecipeResult,
    RideReceipt,
    book_flight,
    book_ride,
    build_registry,
)


SHORT_TEXT = (
    "Book a ride from 123 Market Street to 1 Ferry Building in San Francisco."
)
LONG_TEXT = (
    "Book a trip from Uber Seattle Office to Uber San Francisco Office tomorrow."
)


def response(*parts: types.Part) -> list[LlmResponse]:
    return [LlmResponse(content=types.Content(role="model", parts=list(parts)))]


def call(name: str, **args: object) -> types.Part:
    return types.Part(
        function_call=types.FunctionCall(name=name, args=args)
    )


def final(text: str = "Synthetic booking complete.") -> types.Part:
    return types.Part(text=text)


def returned_ride(call_id: str | None) -> types.Part:
    return types.Part(
        function_response=types.FunctionResponse(
            id=call_id,
            name="book_ride",
            response={
                "status": "simulated-ride-complete",
                "from_address": "123 Market Street",
                "to_address": "1 Ferry Building in San Francisco",
                "passengers": 1,
                "price": 24.5,
                "driver_name": "Jordan Lee",
                "driver_phone": "555-0100",
                "driver_car": "Blue sedan",
                "driver_car_plate": "SAMPLE1",
                "driver_car_color": "Blue",
            },
        )
    )


class AgentTreeTests(unittest.TestCase):
    def test_tree_uses_native_sub_agents_and_activity_tools(self) -> None:
        coordinator = _agent_tree()
        self.assertEqual(coordinator.name, "book_trip_agent")
        self.assertEqual(
            [agent.name for agent in coordinator.sub_agents],
            [SHORT_SPECIALIST_NAME, LONG_SPECIALIST_NAME],
        )
        self.assertEqual(
            [tool.__name__ for tool in coordinator.sub_agents[0].tools],
            ["book_ride"],
        )
        self.assertEqual(
            [tool.__name__ for tool in coordinator.sub_agents[1].tools],
            ["book_flight", "book_ride"],
        )

    def test_model_and_booking_activities_have_bounded_retries(self) -> None:
        for options in (AI_ACTIVITY_OPTIONS, TOOL_ACTIVITY_OPTIONS):
            self.assertEqual(
                options["schedule_to_close_timeout"].total_seconds(), 30
            )
            self.assertEqual(options["retry_policy"]["maximum_attempts"], 3)
            self.assertEqual(
                options["retry_policy"]["initial_interval"].total_seconds(), 1
            )
        self.assertIn(
            "LiveSchemaError",
            AI_ACTIVITY_OPTIONS["retry_policy"][
                "non_retryable_error_reasons"
            ],
        )


class BookingCorrelationTests(unittest.TestCase):
    def test_standalone_response_is_rejected_without_a_completed_booking(
        self,
    ) -> None:
        tracker = _BookingCallTracker()
        completed: list[str] = []
        response = returned_ride("fabricated-call-id").function_response
        assert response is not None
        if tracker.accept(response):
            completed.append(response.name)
        self.assertEqual(completed, [])
        with self.assertRaisesRegex(
            LiveSchemaError, "booking calls and responses did not correlate"
        ):
            tracker.require_complete()

    def test_response_with_wrong_call_id_does_not_match_observed_call(
        self,
    ) -> None:
        tracker = _BookingCallTracker()
        tracker.observe(
            types.FunctionCall(
                id="observed-call-id", name="book_ride", args={}
            )
        )
        completed: list[str] = []
        response = returned_ride("different-call-id").function_response
        assert response is not None
        if tracker.accept(response):
            completed.append(response.name)
        self.assertEqual(completed, [])
        with self.assertRaisesRegex(
            LiveSchemaError, "booking calls and responses did not correlate"
        ):
            tracker.require_complete()


class LiveRunnerTests(unittest.IsolatedAsyncioTestCase):
    async def _run(
        self, text: str, scripted: list[list[LlmResponse]]
    ) -> tuple[RecipeResult | None, object | None, list[str]]:
        model_calls = 0
        bookings: list[str] = []

        def model(*args: object, **kwargs: object) -> list[LlmResponse]:
            nonlocal model_calls
            try:
                value = scripted[model_calls]
            except IndexError as error:
                raise AssertionError("unexpected model request") from error
            model_calls += 1
            return value

        async def flight(
            from_city: str, to_city: str, departure: str
        ) -> FlightReceipt:
            bookings.append(BOOK_FLIGHT_ACTIVITY)
            return await book_flight(from_city, to_city, departure)

        async def ride(
            from_address: str, to_address: str, passengers: int
        ) -> RideReceipt:
            bookings.append(BOOK_RIDE_ACTIVITY)
            return await book_ride(from_address, to_address, passengers)

        with TestWorkflowEnvironment(
            build_registry(), data_converter=PydanticDataConverter()
        ) as env:
            env.on_activity(MODEL_ACTIVITY, fn=model)
            env.on_activity(BOOK_FLIGHT_ACTIVITY, fn=flight)
            env.on_activity(BOOK_RIDE_ACTIVITY, fn=ride)
            execution = await env.client.start_workflow(
                WORKFLOW,
                RecipeInput(text, "live"),
                workflow_id=f"live-{id(scripted)}",
                task_list="test-task-list",
            )
            error = env.get_workflow_error(execution.workflow_id)
            result = None
            if error is None:
                result = env.get_workflow_result(
                    RecipeResult, execution.workflow_id, execution.run_id
                )
        self.assertEqual(model_calls, len(scripted))
        return result, error, bookings

    async def test_short_transfer_runs_real_adk_loop_and_one_ride(self) -> None:
        scripted = [
            response(
                call("transfer_to_agent", agent_name=SHORT_SPECIALIST_NAME)
            ),
            response(
                call(
                    "book_ride",
                    from_address="123 Market Street",
                    to_address="1 Ferry Building in San Francisco",
                    passengers=1,
                )
            ),
            response(final()),
        ]
        result, error, bookings = await self._run(SHORT_TEXT, scripted)
        self.assertIsNone(error)
        assert result is not None
        self.assertEqual(result.route, "SHORT_TRIP")
        self.assertEqual(result.specialist, SHORT_SPECIALIST_NAME)
        self.assertEqual(bookings, [BOOK_RIDE_ACTIVITY])
        self.assertEqual(len(result.rides), 1)

    async def test_long_transfer_runs_exact_booking_activities(self) -> None:
        scripted = [
            response(
                call("transfer_to_agent", agent_name=LONG_SPECIALIST_NAME)
            ),
            response(
                call(
                    "book_flight",
                    from_city="Seattle",
                    to_city="San Francisco",
                    departure="2026-10-14T10:00:00",
                ),
                call(
                    "book_ride",
                    from_address="Uber Seattle Office",
                    to_address="Seattle Airport",
                    passengers=1,
                ),
                call(
                    "book_ride",
                    from_address="San Francisco Airport",
                    to_address="Uber San Francisco Office",
                    passengers=1,
                ),
            ),
            response(final()),
        ]
        result, error, bookings = await self._run(LONG_TEXT, scripted)
        self.assertIsNone(error)
        assert result is not None
        self.assertEqual(result.route, "LONG_TRIP")
        self.assertEqual(result.specialist, LONG_SPECIALIST_NAME)
        self.assertEqual(
            bookings,
            [BOOK_FLIGHT_ACTIVITY, BOOK_RIDE_ACTIVITY, BOOK_RIDE_ACTIVITY],
        )
        self.assertIsInstance(result.flight, FlightReceipt)
        self.assertEqual(len(result.rides), 2)

    async def test_missing_transfer_fails_without_booking_fallback(self) -> None:
        result, error, bookings = await self._run(
            SHORT_TEXT, [response(final("I cannot transfer."))]
        )
        self.assertIsNone(result)
        self.assertIsNotNone(error)
        self.assertEqual(bookings, [])

    async def test_wrong_transfer_fails_without_booking_fallback(self) -> None:
        scripted = [
            response(
                call("transfer_to_agent", agent_name=SHORT_SPECIALIST_NAME)
            ),
            response(
                call(
                    "book_ride",
                    from_address="Uber Seattle Office",
                    to_address="Uber San Francisco Office",
                    passengers=1,
                )
            ),
            response(final()),
        ]
        result, error, bookings = await self._run(LONG_TEXT, scripted)
        self.assertIsNone(result)
        self.assertIsNotNone(error)
        self.assertEqual(bookings, [BOOK_RIDE_ACTIVITY])

    async def test_missing_and_duplicate_tools_fail_strict_validation(self) -> None:
        missing = [
            response(
                call("transfer_to_agent", agent_name=SHORT_SPECIALIST_NAME)
            ),
            response(final()),
        ]
        result, error, bookings = await self._run(SHORT_TEXT, missing)
        self.assertIsNone(result)
        self.assertIsNotNone(error)
        self.assertEqual(bookings, [])

        duplicate = [
            response(
                call("transfer_to_agent", agent_name=SHORT_SPECIALIST_NAME)
            ),
            response(
                call("book_ride", from_address="A", to_address="B", passengers=1),
                call("book_ride", from_address="A", to_address="B", passengers=1),
            ),
            response(final()),
        ]
        result, error, bookings = await self._run(SHORT_TEXT, duplicate)
        self.assertIsNone(result)
        self.assertIsNotNone(error)
        self.assertEqual(bookings.count(BOOK_RIDE_ACTIVITY), 2)

    async def test_unmatched_function_responses_are_not_completed_bookings(
        self,
    ) -> None:
        for call_id in (None, "fabricated-call-id"):
            with self.subTest(call_id=call_id):
                scripted = [
                    response(
                        call(
                            "transfer_to_agent",
                            agent_name=SHORT_SPECIALIST_NAME,
                        )
                    ),
                    response(returned_ride(call_id)),
                ]
                result, error, bookings = await self._run(
                    SHORT_TEXT, scripted
                )
                self.assertIsNone(result)
                self.assertIsNotNone(error)
                self.assertEqual(bookings, [])

    async def test_invalid_tool_arguments_do_not_create_typed_receipt(self) -> None:
        scripted = [
            response(
                call("transfer_to_agent", agent_name=SHORT_SPECIALIST_NAME)
            ),
            response(
                call("book_ride", from_address="A", to_address="B", passengers=0)
            ),
        ]
        result, error, bookings = await self._run(SHORT_TEXT, scripted)
        self.assertIsNone(result)
        self.assertIsNotNone(error)
        self.assertEqual(bookings, [BOOK_RIDE_ACTIVITY])

    async def test_schema_valid_wrong_booking_arguments_fail(self) -> None:
        scripted = [
            response(
                call("transfer_to_agent", agent_name=SHORT_SPECIALIST_NAME)
            ),
            response(
                call(
                    "book_ride",
                    from_address="Wrong origin",
                    to_address="Wrong destination",
                    passengers=1,
                )
            ),
            response(final()),
        ]
        result, error, bookings = await self._run(SHORT_TEXT, scripted)
        self.assertIsNone(result)
        self.assertIsNotNone(error)
        self.assertEqual(bookings, [BOOK_RIDE_ACTIVITY])

    def test_receipts_remain_typed_and_serializable(self) -> None:
        receipt = RideReceipt(
            "simulated-ride-complete",
            "A",
            "B",
            1,
            24.5,
            "Jordan",
            "555",
            "Sedan",
            "SAMPLE",
            "Blue",
        )
        self.assertEqual(asdict(receipt)["passengers"], 1)


if __name__ == "__main__":
    unittest.main()
