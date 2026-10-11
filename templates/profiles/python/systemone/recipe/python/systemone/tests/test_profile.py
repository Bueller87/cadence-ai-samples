import io
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
from grpc import StatusCode

import main
from classifier import LiveConfigurationError, SystemOneActivities, validate_classifier
from config import ClassifierConfig, load_classifier
from workflow import WORKFLOW, Classification, RecipeInput, build_registry


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
                workflow_id="mock-systemone",
                task_list="test-task-list",
            )
            result = environment.get_workflow_result(
                Classification, execution.workflow_id, execution.run_id
            )
        self.assertEqual(result.label, "priority")
        self.assertEqual(result.model, "mock-systemone-v1")

    def test_catalogs_are_strict_and_endpoint_validation_is_host_agnostic(self):
        with CatalogDirectory() as directory:
            classifier = load_classifier(directory, "laya-local")
        self.assertFalse((directory / "models.yaml").exists())
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
        ):
            with self.subTest(flag=flag), patch(
                "sys.stderr"
            ), self.assertRaises(SystemExit):
                main.parser().parse_args(
                    ["start", "--mode", "mock", flag, value]
                )

    async def test_live_starter_does_not_read_classifier_catalog(self):
        with (
            patch("main.load_classifier", side_effect=AssertionError("catalog read")),
            patch("main.start", new=AsyncMock()) as start,
        ):
            await main.main(["start", "--mode", "live"])
        start.assert_awaited_once()

    def test_live_worker_validates_both_classifier_provider_pairs(self):
        with CatalogDirectory() as directory:
            laya = main.parser().parse_args([
                "worker", "--mode", "live", "--confirm-live",
                "--catalog-dir", str(directory),
                "--classifier-id", "laya-local",
            ])
            main.worker_registry(laya, {})

            jev = main.parser().parse_args([
                "worker", "--mode", "live", "--confirm-live",
                "--catalog-dir", str(directory),
                "--classifier-id", "jev-default",
            ])
            with self.assertRaisesRegex(LiveConfigurationError, "CLASSIFIER_AI_KEY"):
                main.worker_registry(jev, {})
            main.worker_registry(jev, {"CLASSIFIER_AI_KEY": "test-key"})

    def test_workflow_input_schema_is_business_data_and_mode_only(self):
        names = {field.name for field in fields(RecipeInput)}
        self.assertEqual(names, {"text", "mode"})

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



if __name__ == "__main__":
    unittest.main()
