import unittest

from cadence.contrib.pydantic import PydanticDataConverter
from cadence.testing import TestActivityEnvironment, TestWorkflowEnvironment

from workflow import (
    APPROVE_SIGNAL,
    BOOK_RIDE_ACTIVITY,
    MOCK_PROPOSAL_ACTIVITY,
    REJECT_SIGNAL,
    STATUS_QUERY,
    WORKFLOW,
    HumanInTheLoopWorkflow,
    PendingRide,
    RecipeInput,
    RecipeResult,
    RideDetails,
    WorkflowStatus,
    book_ride,
    build_registry,
    mock_proposal,
)


class ActivityTests(unittest.IsolatedAsyncioTestCase):
    async def test_mock_proposal_is_deterministic_and_offline(self) -> None:
        with TestActivityEnvironment() as environment:
            result = await environment.execute_activity(
                mock_proposal, "  Book a ride downtown.  "
            )
        self.assertEqual(
            result,
            PendingRide(
                "mock-ride-1",
                BOOK_RIDE_ACTIVITY,
                RideDetails(
                    "123 Market Street",
                    "1 Ferry Building",
                    1,
                ),
            ),
        )

    async def test_mock_proposal_rejects_empty_input(self) -> None:
        with TestActivityEnvironment() as environment:
            with self.assertRaisesRegex(ValueError, "must not be empty"):
                await environment.execute_activity(mock_proposal, " ")

    async def test_booking_activity_returns_only_synthetic_data(self) -> None:
        with TestActivityEnvironment() as environment:
            receipt = await environment.execute_activity(
                book_ride,
                "123 Market Street",
                "1 Ferry Building",
                2,
            )
        self.assertEqual(receipt.status, "simulated-booking-complete")
        self.assertEqual(receipt.passengers, 2)
        self.assertEqual(receipt.driver_car_plate, "SAMPLE1")


class WorkflowTests(unittest.IsolatedAsyncioTestCase):
    async def test_mock_approval_exposes_status_and_books_once(self) -> None:
        bookings: list[tuple[str, str, int]] = []

        async def count_booking(
            from_address: str, to_address: str, passengers: int
        ) -> object:
            bookings.append((from_address, to_address, passengers))
            return await book_ride(from_address, to_address, passengers)

        with TestWorkflowEnvironment(
            build_registry(), data_converter=PydanticDataConverter()
        ) as environment:
            environment.on_activity(BOOK_RIDE_ACTIVITY, fn=count_booking)
            execution = await environment.client.start_workflow(
                WORKFLOW,
                RecipeInput("Book a ride downtown."),
                workflow_id="mock-approved",
                task_list="test-task-list",
            )
            status = await environment.client.query_workflow(
                execution.workflow_id,
                execution.run_id,
                STATUS_QUERY,
                result_type=WorkflowStatus,
            )
            self.assertEqual(status.state, "WAITING_FOR_DECISION")
            self.assertEqual(status.pending[0].call_id, "mock-ride-1")
            self.assertEqual(status.pending[0].trip.passengers, 1)

            await environment.client.signal_workflow(
                execution.workflow_id,
                execution.run_id,
                APPROVE_SIGNAL,
                "unknown-call",
            )
            await environment.client.signal_workflow(
                execution.workflow_id,
                execution.run_id,
                APPROVE_SIGNAL,
                "mock-ride-1",
            )
            await environment.client.signal_workflow(
                execution.workflow_id,
                execution.run_id,
                REJECT_SIGNAL,
                "mock-ride-1",
            )
            result = environment.get_workflow_result(
                RecipeResult, execution.workflow_id, execution.run_id
            )
        self.assertEqual(result.outcome, "APPROVED_COMPLETED")
        self.assertEqual(result.trip.to_address, "1 Ferry Building")
        self.assertEqual(
            bookings,
            [("123 Market Street", "1 Ferry Building", 1)],
        )

    async def test_mock_rejection_never_books(self) -> None:
        bookings = 0

        def unexpected_booking(*args: object) -> None:
            nonlocal bookings
            bookings += 1
            raise AssertionError("rejected proposal scheduled booking")

        with TestWorkflowEnvironment(
            build_registry(), data_converter=PydanticDataConverter()
        ) as environment:
            environment.on_activity(BOOK_RIDE_ACTIVITY, fn=unexpected_booking)
            execution = await environment.client.start_workflow(
                WORKFLOW,
                RecipeInput("Book a ride downtown."),
                workflow_id="mock-rejected",
                task_list="test-task-list",
            )
            status = await environment.client.query_workflow(
                execution.workflow_id,
                execution.run_id,
                STATUS_QUERY,
                result_type=WorkflowStatus,
            )
            await environment.client.signal_workflow(
                execution.workflow_id,
                execution.run_id,
                REJECT_SIGNAL,
                status.pending[0].call_id,
            )
            result = environment.get_workflow_result(
                RecipeResult, execution.workflow_id, execution.run_id
            )
        self.assertEqual(result.outcome, "REJECTED")
        self.assertEqual(bookings, 0)

    def test_unknown_stale_and_conflicting_decisions_are_ignored(self) -> None:
        workflow = HumanInTheLoopWorkflow()
        pending = PendingRide(
            "call-1",
            BOOK_RIDE_ACTIVITY,
            RideDetails("A", "B", 1),
        )
        workflow._pending = {"call-1": pending}

        workflow.approve("unknown")
        workflow.reject("call-1")
        workflow.approve("call-1")
        self.assertEqual(workflow._decisions, {"call-1": False})

        workflow._pending = {}
        workflow.approve("call-1")
        self.assertEqual(workflow._decisions, {"call-1": False})


if __name__ == "__main__":
    unittest.main()
