import unittest

from cadence.contrib.pydantic import PydanticDataConverter
from cadence.testing import TestActivityEnvironment, TestWorkflowEnvironment

from workflow import (
    BOOK_FLIGHT_ACTIVITY,
    BOOK_RIDE_ACTIVITY,
    MOCK_ROUTE_ACTIVITY,
    STATUS_QUERY,
    WORKFLOW,
    RecipeInput,
    RecipeResult,
    TripPlan,
    WorkflowStatus,
    book_flight,
    book_ride,
    build_registry,
    mock_route,
)


LONG_TRIP_TEXT = (
    "Book a trip for me from Uber Seattle Office to Uber San Francisco Office "
    "tomorrow at 10:00 AM."
)
SHORT_TRIP_TEXT = (
    "Book a ride from 123 Market Street to 1 Ferry Building in San Francisco."
)


class ActivityTests(unittest.IsolatedAsyncioTestCase):
    async def test_mock_route_parses_minimal_from_to_addresses(self) -> None:
        with TestActivityEnvironment() as environment:
            plan = await environment.execute_activity(mock_route, "from A to B")
        self.assertEqual((plan.from_address, plan.to_address), ("A", "B"))
        self.assertEqual(plan.route, "SHORT_TRIP")

    async def test_mock_route_treats_explicit_flight_requests_as_long(self) -> None:
        with TestActivityEnvironment() as environment:
            plan = await environment.execute_activity(
                mock_route, "Book a flight from Boston to Chicago."
            )
        self.assertEqual(plan.route, "LONG_TRIP")
        self.assertEqual((plan.from_city, plan.to_city), ("Boston", "Chicago"))

    async def test_mock_route_classifies_long_and_short_trips(self) -> None:
        with TestActivityEnvironment() as environment:
            long_plan = await environment.execute_activity(mock_route, LONG_TRIP_TEXT)
            short_plan = await environment.execute_activity(
                mock_route, SHORT_TRIP_TEXT
            )
        self.assertEqual(long_plan.route, "LONG_TRIP")
        self.assertEqual((long_plan.from_city, long_plan.to_city), ("Seattle", "San Francisco"))
        self.assertEqual(short_plan.route, "SHORT_TRIP")

    async def test_mock_route_rejects_empty_input(self) -> None:
        with TestActivityEnvironment() as environment:
            with self.assertRaisesRegex(ValueError, "must not be empty"):
                await environment.execute_activity(mock_route, " ")

    async def test_booking_activities_return_only_synthetic_data(self) -> None:
        with TestActivityEnvironment() as environment:
            flight = await environment.execute_activity(
                book_flight, "Seattle", "San Francisco", "2026-10-14T10:00:00"
            )
            ride = await environment.execute_activity(
                book_ride, "123 Market Street", "1 Ferry Building", 2
            )
        self.assertEqual(flight.status, "simulated-flight-complete")
        self.assertEqual(flight.flight_number, "SA1234")
        self.assertEqual(ride.status, "simulated-ride-complete")
        self.assertEqual(ride.driver_car_plate, "SAMPLE1")


class WorkflowTests(unittest.IsolatedAsyncioTestCase):
    async def test_mock_short_trip_books_one_ride(self) -> None:
        rides: list[tuple[str, str, int]] = []
        flights = 0

        async def count_ride(
            from_address: str, to_address: str, passengers: int
        ) -> object:
            rides.append((from_address, to_address, passengers))
            return await book_ride(from_address, to_address, passengers)

        def unexpected_flight(*args: object) -> None:
            nonlocal flights
            flights += 1
            raise AssertionError("short trip scheduled a flight")

        with TestWorkflowEnvironment(
            build_registry(), data_converter=PydanticDataConverter()
        ) as environment:
            environment.on_activity(BOOK_RIDE_ACTIVITY, fn=count_ride)
            environment.on_activity(BOOK_FLIGHT_ACTIVITY, fn=unexpected_flight)
            execution = await environment.client.start_workflow(
                WORKFLOW,
                RecipeInput(SHORT_TRIP_TEXT),
                workflow_id="mock-short",
                task_list="test-task-list",
            )
            result = environment.get_workflow_result(
                RecipeResult, execution.workflow_id, execution.run_id
            )
        self.assertEqual(result.route, "SHORT_TRIP")
        self.assertEqual(result.specialist, "Plan short trip agent")
        self.assertEqual(len(result.rides), 1)
        self.assertIsNone(result.flight)
        self.assertEqual(flights, 0)
        self.assertEqual(
            rides,
            [("123 Market Street", "1 Ferry Building in San Francisco", 1)],
        )

    async def test_mock_long_trip_books_flight_and_two_rides(self) -> None:
        rides: list[tuple[str, str, int]] = []
        flights: list[tuple[str, str, str]] = []

        async def count_ride(
            from_address: str, to_address: str, passengers: int
        ) -> object:
            rides.append((from_address, to_address, passengers))
            return await book_ride(from_address, to_address, passengers)

        async def count_flight(
            from_city: str, to_city: str, departure: str
        ) -> object:
            flights.append((from_city, to_city, departure))
            return await book_flight(from_city, to_city, departure)

        with TestWorkflowEnvironment(
            build_registry(), data_converter=PydanticDataConverter()
        ) as environment:
            environment.on_activity(BOOK_RIDE_ACTIVITY, fn=count_ride)
            environment.on_activity(BOOK_FLIGHT_ACTIVITY, fn=count_flight)
            execution = await environment.client.start_workflow(
                WORKFLOW,
                RecipeInput(LONG_TRIP_TEXT),
                workflow_id="mock-long",
                task_list="test-task-list",
            )
            result = environment.get_workflow_result(
                RecipeResult, execution.workflow_id, execution.run_id
            )
        self.assertEqual(result.route, "LONG_TRIP")
        self.assertIsNotNone(result.flight)
        self.assertEqual(len(result.rides), 2)
        self.assertEqual(
            flights,
            [("Seattle", "San Francisco", "2026-10-14T10:00:00")],
        )
        self.assertEqual(
            rides[0][0],
            "Uber Seattle Office",
        )
        self.assertIn("Airport", rides[0][1])
        self.assertIn("Airport", rides[1][0])

    async def test_mock_workflow_rejects_invalid_input(self) -> None:
        with TestWorkflowEnvironment(
            build_registry(), data_converter=PydanticDataConverter()
        ) as environment:
            execution = await environment.client.start_workflow(
                WORKFLOW,
                RecipeInput(" "),
                workflow_id="mock-invalid",
                task_list="test-task-list",
            )
            error = environment.get_workflow_error(execution.workflow_id)
        self.assertIsNotNone(error)

    async def test_activity_failure_fails_workflow(self) -> None:
        def unavailable(text: str) -> TripPlan:
            raise RuntimeError("temporary route failure")

        with TestWorkflowEnvironment(
            build_registry(), data_converter=PydanticDataConverter()
        ) as environment:
            environment.on_activity(MOCK_ROUTE_ACTIVITY, fn=unavailable)
            execution = await environment.client.start_workflow(
                WORKFLOW,
                RecipeInput(SHORT_TRIP_TEXT),
                workflow_id="mock-failure",
                task_list="test-task-list",
            )
            error = environment.get_workflow_error(execution.workflow_id)
        self.assertIsNotNone(error)

    async def test_completed_activities_are_not_rescheduled_on_replay(self) -> None:
        route_calls = 0
        ride_calls = 0

        def counting_route(text: str) -> TripPlan:
            nonlocal route_calls
            route_calls += 1
            return mock_route(text)

        async def counting_ride(
            from_address: str, to_address: str, passengers: int
        ) -> object:
            nonlocal ride_calls
            ride_calls += 1
            return await book_ride(from_address, to_address, passengers)

        with TestWorkflowEnvironment(
            build_registry(), data_converter=PydanticDataConverter()
        ) as environment:
            environment.on_activity(MOCK_ROUTE_ACTIVITY, fn=counting_route)
            environment.on_activity(BOOK_RIDE_ACTIVITY, fn=counting_ride)
            execution = await environment.client.start_workflow(
                WORKFLOW,
                RecipeInput(SHORT_TRIP_TEXT),
                workflow_id="mock-replay",
                task_list="test-task-list",
            )
            result = environment.get_workflow_result(
                RecipeResult, execution.workflow_id, execution.run_id
            )
        self.assertEqual(result.route, "SHORT_TRIP")
        self.assertEqual(route_calls, 1)
        self.assertEqual(ride_calls, 1)

    async def test_status_query_reports_route(self) -> None:
        with TestWorkflowEnvironment(
            build_registry(), data_converter=PydanticDataConverter()
        ) as environment:
            execution = await environment.client.start_workflow(
                WORKFLOW,
                RecipeInput(SHORT_TRIP_TEXT),
                workflow_id="mock-status",
                task_list="test-task-list",
            )
            result = environment.get_workflow_result(
                RecipeResult, execution.workflow_id, execution.run_id
            )
            status = await environment.client.query_workflow(
                execution.workflow_id,
                execution.run_id,
                STATUS_QUERY,
                result_type=WorkflowStatus,
            )
        self.assertEqual(result.mode, "mock")
        self.assertEqual(status.state, "COMPLETED")
        self.assertEqual(status.route, "SHORT_TRIP")

    async def test_invalid_mode_fails_workflow(self) -> None:
        with TestWorkflowEnvironment(
            build_registry(), data_converter=PydanticDataConverter()
        ) as environment:
            execution = await environment.client.start_workflow(
                WORKFLOW,
                RecipeInput(SHORT_TRIP_TEXT, "offline"),
                workflow_id="mock-bad-mode",
                task_list="test-task-list",
            )
            error = environment.get_workflow_error(execution.workflow_id)
        self.assertIsNotNone(error)


if __name__ == "__main__":
    unittest.main()
