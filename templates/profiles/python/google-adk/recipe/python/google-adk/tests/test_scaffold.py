import asyncio
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cadence.api.v1 import workflow_pb2
from cadence.contrib.pydantic import PydanticDataConverter
from cadence.error import WorkflowExecutionAlreadyStartedError
from cadence.testing import TestWorkflowEnvironment
from grpc import StatusCode

import main
from config import CatalogError, ModelConfig, load_model
from inference import (
    GoogleADKAgentActivities,
    LiveConfigurationError,
    build_model_activities,
    runtime_model,
    validate_live,
)
from workflow import (
    AGENT_ACTIVITY_OPTIONS,
    LIVE_AGENT_ACTIVITY,
    MOCK_AGENT_ACTIVITY,
    WORKFLOW,
    AgentInput,
    AgentResult,
    build_registry,
    mock_agent,
)


MODELS = """models:
  - id: gemini-flash-lite
    provider: google
    model: gemini-3.5-flash-lite
    endpoint: https://generativelanguage.googleapis.com
  - id: llama3.2-local
    provider: ollama
    model: llama3.2:latest
    endpoint: http://localhost:11434
  - id: openai-nano
    provider: openai
    model: gpt-5-nano
    endpoint: https://api.openai.com/v1
"""


class CatalogDirectory:
    def __init__(self, contents: str = MODELS) -> None:
        self.contents = contents
        self.temporary: tempfile.TemporaryDirectory[str] | None = None

    def __enter__(self) -> Path:
        self.temporary = tempfile.TemporaryDirectory()
        directory = Path(self.temporary.name)
        (directory / "models.yaml").write_text(self.contents, encoding="utf-8")
        return directory

    def __exit__(self, *args: object) -> None:
        assert self.temporary is not None
        self.temporary.cleanup()


def environment() -> TestWorkflowEnvironment:
    return TestWorkflowEnvironment(
        build_registry(), data_converter=PydanticDataConverter()
    )


def fake_client(**start_workflow: object) -> SimpleNamespace:
    return SimpleNamespace(
        start_workflow=AsyncMock(**start_workflow),
        close=AsyncMock(),
    )


class WorkflowAndActivityTests(unittest.IsolatedAsyncioTestCase):
    async def test_mock_workflow_is_fully_offline(self) -> None:
        with environment() as env:
            execution = await env.client.start_workflow(
                WORKFLOW,
                AgentInput("  Review   this request.  "),
                workflow_id="mock",
                task_list="test-task-list",
            )
            result = env.get_workflow_result(
                AgentResult, execution.workflow_id, execution.run_id
            )
        self.assertEqual(result.mode, "mock")
        self.assertEqual(
            result.response, "Mock agent received: Review this request."
        )

    async def test_activity_failure_fails_workflow(self) -> None:
        def unavailable(_prompt: str) -> str:
            raise RuntimeError("agent unavailable")

        with environment() as env:
            env.on_activity(MOCK_AGENT_ACTIVITY, fn=unavailable)
            execution = await env.client.start_workflow(
                WORKFLOW,
                AgentInput("Review this."),
                workflow_id="failure",
                task_list="test-task-list",
            )
            self.assertIsNotNone(env.get_workflow_error(execution.workflow_id))

    async def test_live_workflow_schedules_google_adk_model_activity(self) -> None:
        calls: list[str] = []

        def model_response(prompt: str) -> str:
            calls.append("model")
            self.assertEqual(prompt, "Review this.")
            return "Live response."

        with environment() as env:
            env.on_activity(
                LIVE_AGENT_ACTIVITY,
                fn=model_response,
            )
            execution = await env.client.start_workflow(
                WORKFLOW,
                AgentInput("Review this.", mode="live"),
                workflow_id="live",
                task_list="test-task-list",
            )
            result = env.get_workflow_result(
                AgentResult, execution.workflow_id, execution.run_id
            )
        self.assertEqual(calls, ["model"])
        self.assertEqual(result.response, "Live response.")

    def test_mock_activity_and_retry_policy(self) -> None:
        self.assertEqual(mock_agent("  hello  world "), "Mock agent received: hello world")
        with self.assertRaisesRegex(ValueError, "must not be empty"):
            mock_agent(" ")
        policy = AGENT_ACTIVITY_OPTIONS["retry_policy"]
        self.assertEqual(policy["maximum_attempts"], 3)
        self.assertIn(
            "LiveAuthenticationError", policy["non_retryable_error_reasons"]
        )

    async def test_changed_google_catalog_endpoint_reaches_gemini_transport(self) -> None:
        changed_endpoint = "https://google-gateway.example/v1beta"
        models = MODELS.replace(
            "https://generativelanguage.googleapis.com", changed_endpoint
        )
        with CatalogDirectory(models) as directory:
            selection = load_model(directory, "gemini-flash-lite")
        activities = build_model_activities(
            selection, {"MODEL_AI_KEY": "test-key"}
        )
        captured: dict[str, str] = {}

        class FakeGemini:
            def __init__(self, *, model: str, base_url: str) -> None:
                captured.update(model=model, base_url=base_url)

        with patch("inference.Gemini", FakeGemini):
            activities._model()
        self.assertEqual(captured["model"], selection.model)
        self.assertEqual(captured["base_url"], changed_endpoint)


class CatalogAndLiveValidationTests(unittest.TestCase):
    def test_loads_every_supported_root_model_without_classifier_catalog(self) -> None:
        directory = next(
            (
                parent
                for parent in Path(__file__).resolve().parents
                if (parent / "models.yaml").is_file()
            ),
            None,
        )
        self.assertIsNotNone(directory)
        selected = {
            model_id: load_model(directory, model_id)
            for model_id in (
                "gemini-flash-lite",
                "llama3.2-local",
                "openai-nano",
            )
        }
        for model in selected.values():
            validate_live(model)
        self.assertEqual(selected["openai-nano"].provider, "openai")

    def test_rejects_unknown_duplicate_and_unsupported_selections(self) -> None:
        with CatalogDirectory() as directory:
            with self.assertRaisesRegex(CatalogError, "unknown models ID"):
                load_model(directory, "missing")
        duplicate = MODELS + """  - id: openai-nano
    provider: openai
    model: duplicate
    endpoint: https://api.openai.com/v1
"""
        with CatalogDirectory(duplicate) as directory:
            with self.assertRaisesRegex(CatalogError, "duplicate models ID"):
                load_model(directory, "openai-nano")
        with self.assertRaisesRegex(LiveConfigurationError, "supported model"):
            validate_live(
                ModelConfig(
                    "gemini-flash-lite",
                    "openai",
                    "gpt-5-nano",
                    "https://api.openai.com/v1",
                )
            )

    def test_validates_provider_endpoints_without_network_calls(self) -> None:
        validate_live(
            ModelConfig(
                "gemini-flash-lite",
                "google",
                "gemini-3.5-flash-lite",
                "https://gateway.example/v1beta",
            )
        )
        validate_live(
            ModelConfig(
                "openai-nano",
                "openai",
                "gpt-5-nano",
                "https://openai-gateway.example/v1",
            )
        )
        with self.assertRaisesRegex(LiveConfigurationError, "HTTPS"):
            validate_live(
                ModelConfig(
                    "openai-nano",
                    "openai",
                    "gpt-5-nano",
                    "http://example.com/v1",
                )
            )
        with self.assertRaisesRegex(LiveConfigurationError, "loopback"):
            validate_live(
                ModelConfig(
                    "llama3.2-local",
                    "ollama",
                    "llama3.2:latest",
                    "https://ollama.example",
                )
            )

    def test_changed_catalog_endpoints_configure_litellm_transports(self) -> None:
        changed_openai = "https://openai-gateway.example/v1"
        changed_ollama = "http://127.0.0.1:22434"
        models = MODELS.replace(
            "https://api.openai.com/v1", changed_openai
        ).replace("http://localhost:11434", changed_ollama)
        with CatalogDirectory(models) as directory:
            openai = load_model(directory, "openai-nano")
            openai_environment = {"MODEL_AI_KEY": "test-key"}
            build_model_activities(openai, openai_environment)
            ollama = load_model(directory, "llama3.2-local")
            ollama_environment: dict[str, str] = {}
            build_model_activities(ollama, ollama_environment)
        self.assertEqual(openai_environment["OPENAI_BASE_URL"], changed_openai)
        self.assertEqual(ollama_environment["OLLAMA_API_BASE"], changed_ollama)

    def test_worker_configuration_maps_models_and_credentials(self) -> None:
        expected = {
            "gemini-flash-lite": ("gemini-3.5-flash-lite", "GOOGLE_API_KEY"),
            "llama3.2-local": ("ollama_chat/llama3.2:latest", "OLLAMA_API_BASE"),
            "openai-nano": ("openai/gpt-5-nano", "OPENAI_API_KEY"),
        }
        with CatalogDirectory() as directory:
            for model_id, (runtime_name, environment_key) in expected.items():
                model = load_model(directory, model_id)
                environ = {"MODEL_AI_KEY": "test-key"}
                activities = build_model_activities(model, environ)
                self.assertIsInstance(activities, GoogleADKAgentActivities)
                self.assertEqual(
                    runtime_model(model.provider, model.model), runtime_name
                )
                self.assertIn(environment_key, environ)

    def test_nonlocal_models_require_key_but_ollama_does_not(self) -> None:
        with CatalogDirectory() as directory:
            with self.assertRaisesRegex(LiveConfigurationError, "MODEL_AI_KEY"):
                build_model_activities(
                    load_model(directory, "gemini-flash-lite"), {}
                )
            build_model_activities(
                load_model(directory, "llama3.2-local"), {}
            )


class CommandTests(unittest.IsolatedAsyncioTestCase):
    def test_literal_worker_and_start_commands_share_defaults(self) -> None:
        worker = main.parser().parse_args(["worker", "--mode", "mock"])
        start = main.parser().parse_args(["start", "--mode", "live"])
        for args in (worker, start):
            self.assertEqual(args.domain, "cadence-ai-samples")
            self.assertEqual(args.task_list, "__RECIPE_SLUG__-google-adk")
            self.assertEqual(
                args.workflow_id, "__RECIPE_SLUG__-google-adk-demo"
            )
        self.assertEqual(worker.mode, "mock")
        self.assertEqual(start.mode, "live")
        self.assertEqual(start.prompt, main.DEFAULT_PROMPT)
        for argv in (
            [],
            ["status"],
            ["worker"],
            ["start"],
            ["worker", "--classifier-id", "anything"],
            ["start", "--mode", "live", "--model-id", "openai-nano"],
            ["start", "--mode", "live", "--confirm-live"],
        ):
            with self.subTest(argv=argv), self.assertRaises(SystemExit), patch(
                "sys.stderr"
            ):
                main.parser().parse_args(argv)

    async def test_mock_worker_and_both_starters_do_not_read_catalog(self) -> None:
        with (
            patch.dict(os.environ, {}, clear=True),
            patch("main.load_model", side_effect=AssertionError("catalog loaded")),
            patch("main.run_worker", new=AsyncMock()) as worker,
            patch("main.start", new=AsyncMock()) as start,
        ):
            await main.main(["worker", "--mode", "mock"])
            await main.main(["start", "--mode", "mock"])
            await main.main(["start", "--mode", "live"])
        worker.assert_awaited_once_with(unittest.mock.ANY)
        self.assertEqual(start.await_count, 2)

    async def test_live_worker_requires_confirmation_before_catalog_load(self) -> None:
        args = main.parser().parse_args(["worker", "--mode", "live"])
        with (
            patch("main.load_model", side_effect=AssertionError("catalog loaded")) as load,
            self.assertRaisesRegex(ValueError, "--confirm-live"),
        ):
            await main.run_worker(args)
        load.assert_not_called()

    async def test_live_worker_owns_catalog_and_provider_configuration(self) -> None:
        args = main.parser().parse_args(
            [
                "worker", "--mode", "live", "--confirm-live",
                "--model-id", "llama3.2-local",
            ]
        )
        model = ModelConfig(
            "llama3.2-local",
            "ollama",
            "llama3.2:latest",
            "http://localhost:11434",
        )
        event = MagicMock()
        event.wait = AsyncMock()
        with (
            patch("main.client"),
            patch("main.Worker", return_value=MagicMock()),
            patch("main.asyncio.Event", return_value=event),
            patch("main.load_model", return_value=model) as load,
        ):
            await main.run_worker(args)
        load.assert_called_once_with(args.catalog_dir, "llama3.2-local")
        event.wait.assert_awaited_once()

    async def test_start_passes_only_request_and_mode_with_allow_duplicate(self) -> None:
        args = main.parser().parse_args(["start", "--mode", "live"])
        cadence_client = fake_client(
            return_value=SimpleNamespace(
                workflow_id="__RECIPE_SLUG__-google-adk-demo",
                run_id="run-1",
            )
        )
        with patch("main.client", return_value=cadence_client), patch(
            "builtins.print"
        ):
            await main.start(args)
        call = cadence_client.start_workflow.call_args
        agent_input = call.args[1]
        self.assertEqual(agent_input, AgentInput(main.DEFAULT_PROMPT, "live"))
        self.assertEqual(vars(agent_input), {"prompt": main.DEFAULT_PROMPT, "mode": "live"})
        self.assertEqual(
            call.kwargs["workflow_id_reuse_policy"],
            workflow_pb2.WORKFLOW_ID_REUSE_POLICY_ALLOW_DUPLICATE,
        )
        cadence_client.close.assert_awaited_once()

    async def test_duplicate_open_run_has_clear_error(self) -> None:
        duplicate = WorkflowExecutionAlreadyStartedError(
            "already started",
            StatusCode.ALREADY_EXISTS,
            "request",
            "open-run",
        )
        cadence_client = fake_client(side_effect=duplicate)
        with (
            patch("main.client", return_value=cadence_client),
            patch("sys.stderr", new_callable=io.StringIO) as stderr,
            self.assertRaises(SystemExit) as exit_info,
        ):
            await main.main(["start", "--mode", "mock"])
        self.assertEqual(exit_info.exception.code, 2)
        self.assertIn("already has an open Run", stderr.getvalue())
        cadence_client.close.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
