import unittest

from cadence.contrib.pydantic import PydanticDataConverter
from cadence.testing import TestActivityEnvironment, TestWorkflowEnvironment

from workflow import (
    LIVE_AGENT_ACTIVITY,
    MOCK_AGENT_ACTIVITY,
    WORKFLOW,
    RecipeInput,
    RecipeResult,
    build_registry,
    mock_agent,
)


class ActivityTests(unittest.IsolatedAsyncioTestCase):
    async def test_mock_agent_is_deterministic_and_offline(self) -> None:
        with TestActivityEnvironment() as environment:
            result = await environment.execute_activity(
                mock_agent, "  Review retries.  "
            )
        self.assertEqual(result, "Mock agent reviewed: Review retries.")

    async def test_mock_agent_rejects_empty_input(self) -> None:
        with TestActivityEnvironment() as environment:
            with self.assertRaisesRegex(ValueError, "must not be empty"):
                await environment.execute_activity(mock_agent, " ")


class WorkflowTests(unittest.IsolatedAsyncioTestCase):
    async def test_mock_workflow_returns_activity_result(self) -> None:
        with TestWorkflowEnvironment(
            build_registry(), data_converter=PydanticDataConverter()
        ) as environment:
            execution = await environment.client.start_workflow(
                WORKFLOW,
                RecipeInput("Review retries."),
                workflow_id="mock-success",
                task_list="test-task-list",
            )
            result = environment.get_workflow_result(
                RecipeResult, execution.workflow_id, execution.run_id
            )
        self.assertEqual(
            result,
            RecipeResult("Mock agent reviewed: Review retries.", "mock"),
        )

    async def test_live_workflow_schedules_stable_worker_activity(self) -> None:
        calls: list[str] = []

        def live_agent(text: str) -> str:
            calls.append(text)
            return "Review retry limits."

        with TestWorkflowEnvironment(
            build_registry(), data_converter=PydanticDataConverter()
        ) as environment:
            environment.on_activity(LIVE_AGENT_ACTIVITY, fn=live_agent)
            execution = await environment.client.start_workflow(
                WORKFLOW,
                RecipeInput("Review retries.", "live"),
                workflow_id="live-success",
                task_list="test-task-list",
            )
            result = environment.get_workflow_result(
                RecipeResult, execution.workflow_id, execution.run_id
            )
        self.assertEqual(calls, ["Review retries."])
        self.assertEqual(result, RecipeResult("Review retry limits.", "live"))

    async def test_activity_failure_fails_workflow(self) -> None:
        def unavailable(text: str) -> str:
            raise RuntimeError("temporary model boundary failure")

        with TestWorkflowEnvironment(
            build_registry(), data_converter=PydanticDataConverter()
        ) as environment:
            environment.on_activity(MOCK_AGENT_ACTIVITY, fn=unavailable)
            execution = await environment.client.start_workflow(
                WORKFLOW,
                RecipeInput("Review retries."),
                workflow_id="mock-failure",
                task_list="test-task-list",
            )
            error = environment.get_workflow_error(execution.workflow_id)
        self.assertIsNotNone(error)


if __name__ == "__main__":
    unittest.main()
