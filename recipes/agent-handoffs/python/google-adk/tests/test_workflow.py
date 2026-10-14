import unittest

from cadence.contrib.pydantic import PydanticDataConverter
from cadence.testing import TestActivityEnvironment, TestWorkflowEnvironment

from workflow import (
    BOOK_FLIGHT_ACTIVITY,
    BOOK_RIDE_ACTIVITY,
    LONG_SPECIALIST_NAME,
    MOCK_ROUTE_ACTIVITY,
    SHORT_SPECIALIST_NAME,
    STATUS_QUERY,
    WORKFLOW,
    FlightReceipt,
    RecipeInput,
    RecipeResult,
    RideReceipt,
    TripPlan,
    WorkflowStatus,
    book_flight,
    book_ride,
    build_registry,
    mock_route,
)


LONG_TEXT = (
    "Book a trip for me from Uber Seattle Office to Uber San Francisco Office "
    "tomorrow at 10:00 AM."
)
SHORT_TEXT = (
    "Book a ride from 123 Market Street to 1 Ferry Building in San Francisco."
)


class ActivityTests(unittest.IsolatedAsyncioTestCase):
    async def test_route_parsing_and_typed_receipts(self) -> None:
        with TestActivityEnvironment() as env:
            short = await env.execute_activity(mock_route, SHORT_TEXT)
            long = await env.execute_activity(mock_route, LONG_TEXT)
            flight = await env.execute_activity(
                book_flight, "Seattle", "San Francisco", "2026-10-14T10:00:00"
            )
            ride = await env.execute_activity(
                book_ride, "123 Market Street", "1 Ferry Building", 2
            )
        self.assertEqual(short.route, "SHORT_TRIP")
        self.assertEqual(long.route, "LONG_TRIP")
        self.assertIsInstance(flight, FlightReceipt)
        self.assertIsInstance(ride, RideReceipt)
        self.assertEqual(flight.status, "simulated-flight-complete")
        self.assertEqual(ride.status, "simulated-ride-complete")

    async def test_invalid_activity_arguments_fail(self) -> None:
        with TestActivityEnvironment() as env:
            with self.assertRaisesRegex(ValueError, "from and to"):
                await env.execute_activity(mock_route, "not a trip")
            with self.assertRaisesRegex(ValueError, "cities"):
                await env.execute_activity(book_flight, "", "Seattle", "tomorrow")
            with self.assertRaisesRegex(ValueError, "positive"):
                await env.execute_activity(book_ride, "A", "B", 0)


class MockWorkflowTests(unittest.IsolatedAsyncioTestCase):
    async def test_short_trip_books_exactly_one_ride(self) -> None:
        calls: list[str] = []

        async def ride(
            from_address: str, to_address: str, passengers: int
        ) -> RideReceipt:
            calls.append(BOOK_RIDE_ACTIVITY)
            return await book_ride(from_address, to_address, passengers)

        def flight(*args: object) -> None:
            calls.append(BOOK_FLIGHT_ACTIVITY)
            raise AssertionError("short trip scheduled a flight")

        with TestWorkflowEnvironment(
            build_registry(), data_converter=PydanticDataConverter()
        ) as env:
            env.on_activity(BOOK_RIDE_ACTIVITY, fn=ride)
            env.on_activity(BOOK_FLIGHT_ACTIVITY, fn=flight)
            execution = await env.client.start_workflow(
                WORKFLOW,
                RecipeInput(SHORT_TEXT),
                workflow_id="mock-short",
                task_list="test-task-list",
            )
            result = env.get_workflow_result(
                RecipeResult, execution.workflow_id, execution.run_id
            )
        self.assertEqual(result.route, "SHORT_TRIP")
        self.assertEqual(result.specialist, SHORT_SPECIALIST_NAME)
        self.assertEqual(calls, [BOOK_RIDE_ACTIVITY])
        self.assertEqual(len(result.rides), 1)
        self.assertIsNone(result.flight)

    async def test_long_trip_books_exact_activity_set(self) -> None:
        calls: list[str] = []

        async def ride(
            from_address: str, to_address: str, passengers: int
        ) -> RideReceipt:
            calls.append(BOOK_RIDE_ACTIVITY)
            return await book_ride(from_address, to_address, passengers)

        async def flight(
            from_city: str, to_city: str, departure: str
        ) -> FlightReceipt:
            calls.append(BOOK_FLIGHT_ACTIVITY)
            return await book_flight(from_city, to_city, departure)

        with TestWorkflowEnvironment(
            build_registry(), data_converter=PydanticDataConverter()
        ) as env:
            env.on_activity(BOOK_RIDE_ACTIVITY, fn=ride)
            env.on_activity(BOOK_FLIGHT_ACTIVITY, fn=flight)
            execution = await env.client.start_workflow(
                WORKFLOW,
                RecipeInput(LONG_TEXT),
                workflow_id="mock-long",
                task_list="test-task-list",
            )
            result = env.get_workflow_result(
                RecipeResult, execution.workflow_id, execution.run_id
            )
            status = await env.client.query_workflow(
                execution.workflow_id,
                execution.run_id,
                STATUS_QUERY,
                result_type=WorkflowStatus,
            )
        self.assertEqual(result.route, "LONG_TRIP")
        self.assertEqual(result.specialist, LONG_SPECIALIST_NAME)
        self.assertEqual(
            calls,
            [BOOK_FLIGHT_ACTIVITY, BOOK_RIDE_ACTIVITY, BOOK_RIDE_ACTIVITY],
        )
        self.assertIsNotNone(result.flight)
        self.assertEqual(len(result.rides), 2)
        self.assertEqual(status, WorkflowStatus("COMPLETED", "mock", "LONG_TRIP"))

    async def test_invalid_input_fails_before_booking(self) -> None:
        bookings = 0

        def booking(*args: object) -> None:
            nonlocal bookings
            bookings += 1

        with TestWorkflowEnvironment(
            build_registry(), data_converter=PydanticDataConverter()
        ) as env:
            env.on_activity(BOOK_RIDE_ACTIVITY, fn=booking)
            env.on_activity(BOOK_FLIGHT_ACTIVITY, fn=booking)
            execution = await env.client.start_workflow(
                WORKFLOW,
                RecipeInput("invalid"),
                workflow_id="invalid",
                task_list="test-task-list",
            )
            error = env.get_workflow_error(execution.workflow_id)
        self.assertIsNotNone(error)
        self.assertEqual(bookings, 0)

    async def test_activity_failure_and_replay_counts(self) -> None:
        route_calls = 0
        ride_calls = 0

        def route(text: str) -> TripPlan:
            nonlocal route_calls
            route_calls += 1
            return mock_route(text)

        async def ride(
            from_address: str, to_address: str, passengers: int
        ) -> RideReceipt:
            nonlocal ride_calls
            ride_calls += 1
            return await book_ride(from_address, to_address, passengers)

        with TestWorkflowEnvironment(
            build_registry(), data_converter=PydanticDataConverter()
        ) as env:
            env.on_activity(MOCK_ROUTE_ACTIVITY, fn=route)
            env.on_activity(BOOK_RIDE_ACTIVITY, fn=ride)
            execution = await env.client.start_workflow(
                WORKFLOW,
                RecipeInput(SHORT_TEXT),
                workflow_id="replay",
                task_list="test-task-list",
            )
            result = env.get_workflow_result(
                RecipeResult, execution.workflow_id, execution.run_id
            )
        self.assertEqual(result.route, "SHORT_TRIP")
        self.assertEqual((route_calls, ride_calls), (1, 1))


if __name__ == "__main__":
    unittest.main()
