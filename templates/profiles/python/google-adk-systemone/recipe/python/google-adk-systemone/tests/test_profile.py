import io
import inspect
import json
import os
import tempfile
import threading
import unittest
from dataclasses import fields
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from cadence.api.v1 import workflow_pb2
from cadence.contrib.pydantic import PydanticDataConverter
from cadence.error import WorkflowExecutionAlreadyStartedError
from cadence.testing import TestWorkflowEnvironment
from google.genai import errors as genai_errors
from grpc import StatusCode

import main
from classifier import LiveConfigurationError, SystemOneActivities, validate_classifier
from config import ClassifierConfig, ModelConfig, load_classifier, load_model
from inference import (
    GoogleADKAgentActivities,
    LiveAuthenticationError,
    LiveConfigurationError as ModelConfigurationError,
    LiveSchemaError,
    _raise_model_error,
    build_model_activities,
)
from workflow import WORKFLOW, RecipeInput, RecipeResult, build_registry


CLASSIFIERS = """classifiers:
  - id: jev-default
    provider: typesafe
    model: jev-latest
    endpoint: https://classifier.example/v1/systemone
  - id: laya-local
    provider: laya
    model: local-laya
    endpoint: http://localhost:8008/v1/systemone
"""
MODELS = """models:
  - id: gemini-flash-lite
    provider: google
    model: gemini-test
    endpoint: https://model.example
  - id: llama3.2-local
    provider: ollama
    model: llama-test
    endpoint: http://localhost:11434
  - id: openai-nano
    provider: openai
    model: openai-test
    endpoint: https://openai-gateway.example/v1
"""


class CatalogDirectory:
    def __enter__(self):
        self.temp = tempfile.TemporaryDirectory()
        directory = Path(self.temp.name)
        (directory / "classifiers.yaml").write_text(CLASSIFIERS, encoding="utf-8")
        (directory / "models.yaml").write_text(MODELS, encoding="utf-8")
        return directory

    def __exit__(self, *_args):
        self.temp.cleanup()


class StubHandler(BaseHTTPRequestHandler):
    path_seen = ""

    def do_POST(self):
        type(self).path_seen = self.path
        length = int(self.headers["Content-Length"])
        payload = json.loads(self.rfile.read(length))
        body = json.dumps({
            "model": payload["model"],
            "answers": {"route": {
                "type": "choice", "choice": "priority", "confidence": 0.9,
                "probabilities": {"routine": 0.1, "priority": 0.9},
            }},
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


def fake_client(**start_workflow):
    return SimpleNamespace(
        start_workflow=AsyncMock(**start_workflow), close=AsyncMock()
    )


class ProfileTests(unittest.IsolatedAsyncioTestCase):
    async def test_mock_workflow_is_offline_and_deterministic(self):
        with TestWorkflowEnvironment(
            build_registry(), data_converter=PydanticDataConverter()
        ) as environment:
            execution = await environment.client.start_workflow(
                WORKFLOW,
                RecipeInput("urgent security request"),
                workflow_id="mock-combined",
                task_list="test-task-list",
            )
            result = environment.get_workflow_result(
                RecipeResult, execution.workflow_id, execution.run_id
            )
        self.assertEqual(result.classification.label, "priority")
        self.assertEqual(result.answer, "Mock agent reviewed urgent security request")

    def test_catalogs_are_strict_and_endpoint_validation_is_host_agnostic(self):
        with CatalogDirectory() as directory:
            classifier = load_classifier(directory, "laya-local")
            model = load_model(directory, "llama3.2-local")
        self.assertEqual(model.provider, "ollama")
        validate_classifier(classifier)
        with self.assertRaisesRegex(LiveConfigurationError, "loopback"):
            validate_classifier(ClassifierConfig(
                "laya-local", "laya", "m", "https://remote.example/systemone"
            ))
        with self.assertRaisesRegex(LiveConfigurationError, "without credentials"):
            validate_classifier(ClassifierConfig(
                "laya-local", "laya", "m", "http://user:pass@localhost/systemone"
            ))

    async def test_mutated_classifier_endpoint_reaches_loopback_stub(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), StubHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            selection = ClassifierConfig(
                "laya-local", "laya", "changed-model",
                f"http://127.0.0.1:{server.server_port}/changed-systemone",
            )
            result = await SystemOneActivities(selection, "").classify("urgent")
        finally:
            server.shutdown()
            thread.join()
            server.server_close()
        self.assertEqual(StubHandler.path_seen, "/changed-systemone")
        self.assertEqual(result.model, "changed-model")

    async def test_mock_commands_read_neither_catalogs_nor_credentials(self):
        with (
            patch.dict(os.environ, {}, clear=True),
            patch("main.load_classifier", side_effect=AssertionError("classifier read")),
            patch("main.load_model", side_effect=AssertionError("model read")),
            patch("main.run_worker", new=AsyncMock()) as worker,
            patch("main.start", new=AsyncMock()) as start,
        ):
            await main.main(["worker", "--mode", "mock"])
            await main.main(["start", "--mode", "mock"])
        worker.assert_awaited_once_with(unittest.mock.ANY)
        start.assert_awaited_once_with(unittest.mock.ANY)

    def test_worker_and_start_require_explicit_mode(self):
        for command in ("worker", "start"):
            with self.subTest(command=command), patch(
                "sys.stderr"
            ), self.assertRaises(SystemExit):
                main.parser().parse_args([command])
        for flag, value in (
            ("--catalog-dir", "/tmp/catalogs"),
            ("--classifier-id", "laya-local"),
            ("--model-id", "llama3.2-local"),
        ):
            with self.subTest(flag=flag), patch(
                "sys.stderr"
            ), self.assertRaises(SystemExit):
                main.parser().parse_args(
                    ["start", "--mode", "mock", flag, value]
                )

    async def test_live_starter_reads_neither_catalog(self):
        with (
            patch("main.load_classifier", side_effect=AssertionError("classifier read")),
            patch("main.load_model", side_effect=AssertionError("model read")),
            patch("main.start", new=AsyncMock()) as start,
        ):
            await main.main(["start", "--mode", "live"])
        start.assert_awaited_once()

    def test_workflow_input_schema_is_business_data_and_mode_only(self):
        names = {field.name for field in fields(RecipeInput)}
        self.assertEqual(names, {"text", "mode"})
        self.assertEqual(
            tuple(inspect.signature(GoogleADKAgentActivities.run_agent).parameters),
            ("self", "prompt"),
        )

    async def test_allow_duplicate_and_open_duplicate_error(self):
        success = fake_client(return_value=SimpleNamespace(
            workflow_id=main.DEFAULT_WORKFLOW_ID, run_id="run-1"
        ))
        args = main.parser().parse_args(["start", "--mode", "mock"])
        with patch("main.client", return_value=success), patch("builtins.print"):
            await main.start(args)
        self.assertEqual(
            success.start_workflow.call_args.kwargs["workflow_id_reuse_policy"],
            workflow_pb2.WORKFLOW_ID_REUSE_POLICY_ALLOW_DUPLICATE,
        )
        duplicate = WorkflowExecutionAlreadyStartedError(
            "already started", StatusCode.ALREADY_EXISTS, "request", "open"
        )
        failed = fake_client(side_effect=duplicate)
        with (
            patch("main.client", return_value=failed),
            patch("sys.stderr", new_callable=io.StringIO) as stderr,
            self.assertRaises(SystemExit),
        ):
            await main.main(["start", "--mode", "mock"])
        self.assertIn("already has an open Run", stderr.getvalue())

    def test_runtime_has_no_provider_host_constants(self):
        source = Path(__file__).resolve().parents[1]
        runtime = "\n".join(
            path.read_text(encoding="utf-8")
            for path in source.glob("*.py")
        )
        for host in (
            "api.openai.com", "generativelanguage.googleapis.com",
            "api.typesafe.ai",
        ):
            self.assertNotIn(host, runtime)

    def test_mutated_model_endpoint_reaches_worker_transport(self):
        with CatalogDirectory() as directory:
            model = load_model(directory, "llama3.2-local")
        environment = {}
        build_model_activities(model, environment)
        self.assertEqual(environment["OLLAMA_API_BASE"], model.endpoint)

    def test_live_worker_constructs_every_supported_provider_pair(self):
        with CatalogDirectory() as directory:
            for classifier_id in ("jev-default", "laya-local"):
                for model_id in (
                    "gemini-flash-lite", "llama3.2-local", "openai-nano"
                ):
                    with self.subTest(
                        classifier_id=classifier_id, model_id=model_id
                    ):
                        args = main.parser().parse_args([
                            "worker", "--mode", "live", "--confirm-live",
                            "--catalog-dir", str(directory),
                            "--classifier-id", classifier_id,
                            "--model-id", model_id,
                        ])
                        registry = main.worker_registry(args, {
                            "CLASSIFIER_AI_KEY": "classifier-key",
                            "MODEL_AI_KEY": "model-key",
                        })
                        self.assertIsNotNone(registry)

    def test_live_worker_allows_keyless_local_pair_and_rejects_missing_keys(self):
        with CatalogDirectory() as directory:
            local = main.parser().parse_args([
                "worker", "--mode", "live", "--confirm-live",
                "--catalog-dir", str(directory),
                "--classifier-id", "laya-local",
                "--model-id", "llama3.2-local",
            ])
            self.assertIsNotNone(main.worker_registry(local, {}))

            remote_model = main.parser().parse_args([
                "worker", "--mode", "live", "--confirm-live",
                "--catalog-dir", str(directory),
                "--classifier-id", "laya-local",
                "--model-id", "openai-nano",
            ])
            with self.assertRaisesRegex(ModelConfigurationError, "MODEL_AI_KEY"):
                main.worker_registry(remote_model, {})

            remote_classifier = main.parser().parse_args([
                "worker", "--mode", "live", "--confirm-live",
                "--catalog-dir", str(directory),
                "--classifier-id", "jev-default",
                "--model-id", "llama3.2-local",
            ])
            with self.assertRaisesRegex(LiveConfigurationError, "CLASSIFIER_AI_KEY"):
                main.worker_registry(remote_classifier, {})

    def test_model_endpoint_propagates_for_each_provider(self):
        selections = (
            ModelConfig("gemini-flash-lite", "google", "gemini-test",
                        "https://google-gateway.example"),
            ModelConfig("llama3.2-local", "ollama", "llama-test",
                        "http://127.0.0.1:22434"),
            ModelConfig("openai-nano", "openai", "openai-test",
                        "https://openai-gateway.example/v1"),
        )
        for selection in selections:
            with self.subTest(provider=selection.provider):
                environment = {"MODEL_AI_KEY": "test-key"}
                activities = build_model_activities(selection, environment)
                self.assertIsInstance(activities, GoogleADKAgentActivities)
                if selection.provider == "google":
                    with patch("inference.Gemini") as gemini:
                        activities._model()
                    self.assertEqual(
                        gemini.call_args.kwargs["base_url"], selection.endpoint
                    )
                elif selection.provider == "ollama":
                    self.assertEqual(
                        environment["OLLAMA_API_BASE"], selection.endpoint
                    )
                else:
                    self.assertEqual(
                        environment["OPENAI_BASE_URL"], selection.endpoint
                    )

    def test_model_safety_validation_precedes_activity_construction(self):
        unsafe = ModelConfig(
            "openai-nano", "openai", "openai-test",
            "http://remote.example/v1",
        )
        with patch("inference.GoogleADKAgentActivities") as activities:
            with self.assertRaisesRegex(ModelConfigurationError, "HTTPS"):
                build_model_activities(unsafe, {"MODEL_AI_KEY": "test-key"})
            activities.assert_not_called()

    def test_google_api_errors_are_sanitized_and_classified(self):
        cases = (
            (
                "unauthorized",
                genai_errors.APIError(
                    401, {"error": {"message": "secret provider body"}}
                ),
                LiveAuthenticationError,
                "MODEL_AI_KEY",
            ),
            (
                "forbidden",
                genai_errors.APIError(
                    403, {"error": {"message": "secret provider body"}}
                ),
                LiveAuthenticationError,
                "MODEL_AI_KEY",
            ),
            (
                "invalid key detail",
                genai_errors.APIError(
                    400,
                    {
                        "error": {
                            "message": "secret provider body",
                            "details": [{"reason": "API_KEY_INVALID"}],
                        }
                    },
                ),
                LiveAuthenticationError,
                "MODEL_AI_KEY",
            ),
            (
                "retryable",
                genai_errors.APIError(
                    429, {"error": {"message": "secret provider body"}}
                ),
                RuntimeError,
                "HTTP 429",
            ),
            (
                "nonretryable",
                genai_errors.APIError(
                    400, {"error": {"message": "secret provider body"}}
                ),
                LiveSchemaError,
                "HTTP 400",
            ),
        )
        for name, error, expected_type, expected_message in cases:
            with self.subTest(name=name):
                with self.assertRaises(expected_type) as raised:
                    _raise_model_error(error)
                self.assertIn(expected_message, str(raised.exception))
                self.assertNotIn("secret provider body", str(raised.exception))



if __name__ == "__main__":
    unittest.main()
