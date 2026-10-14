import unittest

from cadence.contrib.pydantic import PydanticDataConverter
from cadence.testing import TestWorkflowEnvironment
from google.adk.models.llm_response import LlmResponse
from google.genai import types

from activities import MAX_SUMMARY_LENGTH, PUBLISH_ACTIVITY, PublicationReceipt
from workflow import (
    AI_ACTIVITY_OPTIONS,
    APPROVE_SIGNAL,
    EXPAND_SIGNAL,
    MODEL_ACTIVITY,
    REVISE_SIGNAL,
    STATUS_QUERY,
    WORKFLOW,
    ExpandReportRequest,
    RecipeInput,
    RecipeResult,
    ReviseReportRequest,
    WorkflowStatus,
    build_registry,
)


def response(text: str) -> list[LlmResponse]:
    return [
        LlmResponse(
            content=types.Content(role="model", parts=[types.Part(text=text)])
        )
    ]


PLAN = "1. What evidence supports durable orchestration?"
SUMMARY = (
    "### Durable orchestration\n\nA bounded summary from both simulated source "
    "briefs. Sources are simulated."
)
REPORT = (
    "# Durable orchestration\n\n## Executive summary\n\nSynthetic evidence.\n\n"
    "## Key findings\n\nBounded finding.\n\n"
    "## Cross-cutting challenges\n\nVerification.\n\n"
    "## Outlook\n\nFurther validation."
)


class LiveRunnerTests(unittest.IsolatedAsyncioTestCase):
    async def test_all_role_paths_use_real_runner_and_model_activity(self) -> None:
        scripted = [
            PLAN,
            SUMMARY,
            REPORT,
            REPORT + "\n\n## Revised\n\nShorter conclusion.",
            SUMMARY,
            REPORT + "\n\n## Expanded\n\nOperational recovery.",
        ]
        calls: list[object] = []

        def model(*args: object, **kwargs: object) -> list[LlmResponse]:
            calls.append((args, kwargs))
            try:
                return response(scripted[len(calls) - 1])
            except IndexError as error:
                raise AssertionError("unexpected model request") from error

        with TestWorkflowEnvironment(
            build_registry(), data_converter=PydanticDataConverter()
        ) as environment:
            environment.on_activity(MODEL_ACTIVITY, fn=model)
            environment.on_activity(
                PUBLISH_ACTIVITY,
                fn=lambda request: PublicationReceipt(
                    request.publication_id,
                    "live-report.md",
                    request.revision,
                    "e" * 64,
                ),
            )
            execution = await environment.client.start_workflow(
                WORKFLOW,
                RecipeInput("Durable orchestration", "live"),
                workflow_id="live-all-role-paths",
                task_list="test-task-list",
            )
            initial = await environment.client.query_workflow(
                execution.workflow_id,
                execution.run_id,
                STATUS_QUERY,
                result_type=WorkflowStatus,
            )
            self.assertEqual((initial.state, initial.revision), ("WAITING_FOR_REVIEW", 0))
            await environment.client.signal_workflow(
                execution.workflow_id,
                execution.run_id,
                REVISE_SIGNAL,
                ReviseReportRequest(0, "Shorten the conclusion."),
            )
            revised = await environment.client.query_workflow(
                execution.workflow_id,
                execution.run_id,
                STATUS_QUERY,
                result_type=WorkflowStatus,
            )
            self.assertEqual((revised.state, revised.revision), ("WAITING_FOR_REVIEW", 1))
            await environment.client.signal_workflow(
                execution.workflow_id,
                execution.run_id,
                EXPAND_SIGNAL,
                ExpandReportRequest(1, "Operational recovery"),
            )
            expanded = await environment.client.query_workflow(
                execution.workflow_id,
                execution.run_id,
                STATUS_QUERY,
                result_type=WorkflowStatus,
            )
            self.assertEqual((expanded.state, expanded.revision), ("WAITING_FOR_REVIEW", 2))
            await environment.client.signal_workflow(
                execution.workflow_id, execution.run_id, APPROVE_SIGNAL, 2
            )
            result = environment.get_workflow_result(
                RecipeResult, execution.workflow_id, execution.run_id
            )

        self.assertEqual(result.revision, 2)
        self.assertEqual(len(calls), 6)
        requests = [
            call[0][-1] if call[0] else call[1]["llm_request"] for call in calls
        ]
        prompts = [request.contents[-1].parts[0].text for request in requests]
        for marker in (
            "Research topic:",
            "Source brief 1:",
            "Research summaries:",
            "Edit kind: revise",
            "Question: Operational recovery",
            "Edit kind: expand",
        ):
            self.assertTrue(any(marker in prompt for prompt in prompts), marker)

    async def test_over_limit_specialist_output_fails_closed(self) -> None:
        scripted = [PLAN, "x" * (MAX_SUMMARY_LENGTH + 1)]
        calls = 0

        def model(*args: object, **kwargs: object) -> list[LlmResponse]:
            nonlocal calls
            value = scripted[calls]
            calls += 1
            return response(value)

        with TestWorkflowEnvironment(
            build_registry(), data_converter=PydanticDataConverter()
        ) as environment:
            environment.on_activity(MODEL_ACTIVITY, fn=model)
            execution = await environment.client.start_workflow(
                WORKFLOW,
                RecipeInput("Bounded output", "live"),
                workflow_id="live-over-limit",
                task_list="test-task-list",
            )
            error = environment.get_workflow_error(execution.workflow_id)
        self.assertIsNotNone(error)
        self.assertEqual(calls, 2)

    async def test_missing_specialist_output_fails_closed(self) -> None:
        scripted = [PLAN, ""]
        calls = 0

        def model(*args: object, **kwargs: object) -> list[LlmResponse]:
            nonlocal calls
            value = scripted[calls]
            calls += 1
            return response(value)

        with TestWorkflowEnvironment(
            build_registry(), data_converter=PydanticDataConverter()
        ) as environment:
            environment.on_activity(MODEL_ACTIVITY, fn=model)
            execution = await environment.client.start_workflow(
                WORKFLOW,
                RecipeInput("Missing output", "live"),
                workflow_id="live-missing-output",
                task_list="test-task-list",
            )
            error = environment.get_workflow_error(execution.workflow_id)
        self.assertIsNotNone(error)
        self.assertEqual(calls, 2)

    async def test_model_failure_fails_without_fallback(self) -> None:
        attempts = 0

        def model(*args: object, **kwargs: object) -> list[LlmResponse]:
            nonlocal attempts
            attempts += 1
            raise RuntimeError("temporary model request failure")

        with TestWorkflowEnvironment(
            build_registry(), data_converter=PydanticDataConverter()
        ) as environment:
            environment.on_activity(MODEL_ACTIVITY, fn=model)
            execution = await environment.client.start_workflow(
                WORKFLOW,
                RecipeInput("Retry exhaustion", "live"),
                workflow_id="live-exhaustion",
                task_list="test-task-list",
            )
            error = environment.get_workflow_error(execution.workflow_id)
        self.assertIsNotNone(error)
        self.assertEqual(attempts, 1)

    def test_retryable_and_exhausted_failures_have_bounded_policy(self) -> None:
        policy = AI_ACTIVITY_OPTIONS["retry_policy"]
        self.assertEqual(policy["maximum_attempts"], 3)
        self.assertEqual(policy["backoff_coefficient"], 2.0)
        self.assertEqual(
            policy["non_retryable_error_reasons"],
            [
                "LiveAuthenticationError",
                "LiveConfigurationError",
                "LiveSchemaError",
            ],
        )


if __name__ == "__main__":
    unittest.main()
