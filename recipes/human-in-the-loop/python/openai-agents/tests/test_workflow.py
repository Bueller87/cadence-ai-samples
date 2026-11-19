import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from agents import ModelSettings, ModelTracing
from cadence.contrib.pydantic import PydanticDataConverter
from cadence.testing import TestActivityEnvironment, TestWorkflowEnvironment

from mock_model import MockModel, mock_model_activities
from workflow import (
    APPROVE_SIGNAL,
    BOOK_RIDE_ACTIVITY,
    REJECT_SIGNAL,
    WORKFLOW,
    HumanInTheLoopWorkflow,
    MarkdownQueryResponse,
    book_ride,
    build_registry,
)

BOOKING = ("123 Market Street", "1 Ferry Building", 1)


class ActivityTests(unittest.IsolatedAsyncioTestCase):
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

    async def test_booking_activity_rejects_invalid_arguments(self) -> None:
        with TestActivityEnvironment() as environment:
            with self.assertRaisesRegex(ValueError, "must not be empty"):
                await environment.execute_activity(book_ride, " ", "B", 1)
            with self.assertRaisesRegex(ValueError, "must be positive"):
                await environment.execute_activity(book_ride, "A", "B", 0)


class MockModelTests(unittest.IsolatedAsyncioTestCase):
    async def respond(self, model_input: object) -> object:
        return await MockModel().get_response(
            system_instructions=None,
            input=model_input,
            model_settings=ModelSettings(),
            tools=[SimpleNamespace(name="book_ride")],
            output_schema=None,
            handoffs=[],
            tracing=ModelTracing.DISABLED,
            previous_response_id=None,
            conversation_id=None,
            prompt=None,
        )

    async def test_first_request_proposes_the_fixed_ride(self) -> None:
        call = (await self.respond("anything")).output[0]
        self.assertEqual(
            (call.type, call.call_id, call.name),
            ("function_call", "mock-ride-1", "book_ride"),
        )
        self.assertIn("123 Market Street", call.arguments)

    async def test_final_message_reflects_the_tool_result(self) -> None:
        for output, expected in (
            ("{'status': 'simulated-booking-complete'}", "booking completed"),
            ("User rejected the tool call.", "was rejected"),
        ):
            with self.subTest(output=output):
                items = [{"type": "function_call_output", "output": output}]
                message = (await self.respond(items)).output[0]
                self.assertIn(expected, message.content[0].text)

    def test_mock_activities_provide_the_placeholder_key(self) -> None:
        with patch.dict(os.environ, clear=True):
            mock_model_activities()
            self.assertEqual(os.environ["OPENAI_API_KEY"], "mock")


class WorkflowTests(unittest.IsolatedAsyncioTestCase):
    async def run_decisions(
        self, workflow_id: str, *decisions: tuple[str, str]
    ) -> tuple[str, str, list[tuple[str, str, int]]]:
        bookings: list[tuple[str, str, int]] = []

        async def count_booking(
            from_address: str, to_address: str, passengers: int
        ) -> object:
            bookings.append((from_address, to_address, passengers))
            return await book_ride(from_address, to_address, passengers)

        with TestWorkflowEnvironment(
            build_registry(mock_model_activities()),
            data_converter=PydanticDataConverter(),
        ) as environment:
            environment.on_activity(BOOK_RIDE_ACTIVITY, fn=count_booking)
            execution = await environment.client.start_workflow(
                WORKFLOW,
                "Book a ride downtown.",
                workflow_id=workflow_id,
                task_list="test-task-list",
            )
            pending = await environment.client.query_workflow(
                execution.workflow_id,
                execution.run_id,
                "get_interruptions",
                result_type=MarkdownQueryResponse,
            )
            for signal, call_id in decisions:
                await environment.client.signal_workflow(
                    execution.workflow_id, execution.run_id, signal, call_id
                )
            result = environment.get_workflow_result(
                str, execution.workflow_id, execution.run_id
            )
        return pending.data, result, bookings

    async def test_approval_books_exactly_once(self) -> None:
        pending, result, bookings = await self.run_decisions(
            "mock-approved",
            (APPROVE_SIGNAL, "unknown-call"),
            (APPROVE_SIGNAL, "mock-ride-1"),
            (REJECT_SIGNAL, "mock-ride-1"),
        )
        self.assertIn("### `book_ride`", pending)
        self.assertIn("`mock-ride-1`", pending)
        self.assertIn('"to_address": "1 Ferry Building"', pending)
        for signal in (APPROVE_SIGNAL, REJECT_SIGNAL):
            self.assertIn(f'signalName="{signal}"', pending)
        self.assertIn('input="mock-ride-1"', pending)
        self.assertEqual(result, "The simulated ride booking completed.")
        self.assertEqual(bookings, [BOOKING])

    async def test_rejection_never_books(self) -> None:
        _, result, bookings = await self.run_decisions(
            "mock-rejected",
            (REJECT_SIGNAL, "stale-call"),
            (REJECT_SIGNAL, "mock-ride-1"),
            (APPROVE_SIGNAL, "mock-ride-1"),
        )
        self.assertEqual(result, "The simulated ride request was rejected.")
        self.assertEqual(bookings, [])

    def test_unknown_stale_and_conflicting_decisions_are_ignored(self) -> None:
        workflow = HumanInTheLoopWorkflow()
        workflow._pending = {"call-1": SimpleNamespace(call_id="call-1")}

        workflow.approve_tool_call("unknown")
        workflow.reject_tool_call("call-1")
        workflow.approve_tool_call("call-1")
        self.assertEqual(workflow._decisions, {"call-1": False})

        workflow._pending = {}
        workflow.approve_tool_call("call-1")
        self.assertEqual(workflow._decisions, {"call-1": False})


if __name__ == "__main__":
    unittest.main()
