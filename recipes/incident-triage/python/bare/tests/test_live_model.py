import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cadence.contrib.pydantic import PydanticDataConverter
from cadence.testing import TestWorkflowEnvironment

import live_model
import main
import story
from workflow import STATUS_QUERY, WORKFLOW, RecipeInput, build_registry


ROOT = next(
    parent
    for parent in Path(__file__).resolve().parents
    if (parent / "models.yaml").is_file()
)
DRAFT = {
    "tool": "draft",
    "args": {},
    "hypothesis": "payments v2.14 exhausted the ledger pool",
    "conclusion": {
        "call_path": ["api-gateway", "checkout", "payments", "ledger"],
        "root_cause": "payments v2.14 exhausted its ledger connection pool (50 of 50)",
        "confidence": "high",
        "summary": "The live call drafted from the supplied evidence.",
    },
}


def ollama_response(content: dict, prompt_tokens: int, completion_tokens: int) -> dict:
    return {
        "choices": [{"message": {"content": json.dumps(content)}}],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
        },
    }


class LiveModelTests(unittest.TestCase):
    def test_repository_catalog_selects_local_llama(self) -> None:
        selection = live_model.load_selection(ROOT / "models.yaml", "llama3.2-local")
        self.assertEqual(selection.provider, "ollama")
        self.assertEqual(selection.model, "llama3.2:latest")
        self.assertEqual(selection.endpoint, "http://localhost:11434")

    def test_remote_ollama_endpoint_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "models.yaml"
            path.write_text(
                "models:\n"
                "  - id: llama3.2-local\n"
                "    provider: ollama\n"
                "    model: llama3.2:latest\n"
                "    endpoint: https://ollama.example\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(live_model.LiveConfigurationError, "loopback"):
                live_model.load_selection(path, "llama3.2-local")

    def test_paid_model_requires_a_key_before_any_request(self) -> None:
        with patch.dict(os.environ, {"MODEL_AI_KEY": ""}, clear=False):
            os.environ.pop("MODEL_AI_KEY", None)
            with self.assertRaisesRegex(
                live_model.LiveConfigurationError, "MODEL_AI_KEY"
            ):
                live_model.load_selection(ROOT / "models.yaml", "openai-nano")
                live_model._api_key(
                    live_model.load_selection(ROOT / "models.yaml", "openai-nano")
                )

    def test_spend_accumulates_reported_tokens_and_zero_external_charge(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ, {"INCIDENT_TRIAGE_STATE": directory}
        ):
            selection = live_model.load_selection(
                ROOT / "models.yaml", "llama3.2-local"
            )
            calls = {"count": 0}

            def transport(url, payload, headers):
                calls["count"] += 1
                self.assertTrue(url.endswith("/v1/chat/completions"))
                self.assertNotIn("authorization", {key.lower() for key in headers})
                tokens = 10 * calls["count"]
                return ollama_response(DRAFT, tokens, 4)

            first = live_model.plan(
                {"start": "checkout", "hops": [], "guidance": [], "force_draft": False},
                transport,
            )
            second = live_model.plan(
                {"start": "checkout", "hops": [], "guidance": [], "force_draft": False},
                transport,
            )
            text = live_model.spend_text()
        self.assertEqual(first["tool"], "draft")
        self.assertEqual(second["conclusion"]["call_path"][0], "api-gateway")
        self.assertIn("running_input_tokens=30", text)
        self.assertIn("running_output_tokens=8", text)
        self.assertIn("running_external_usd=0", text)
        self.assertIn("local compute unpriced", text)
        self.assertNotIn("authorization", text)

    def test_missing_usage_stays_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ, {"INCIDENT_TRIAGE_STATE": directory}
        ):
            def transport(url, payload, headers):
                return {"choices": [{"message": {"content": json.dumps(DRAFT)}}]}

            live_model.plan(
                {"start": "checkout", "hops": [], "guidance": []}, transport
            )
            line = live_model.spend_text()
        self.assertIn("input_tokens=unavailable", line)
        self.assertIn("running_input_tokens=unavailable", line)
        self.assertNotIn("input_tokens=0", line)

    def test_unknown_tool_is_rejected_after_the_call_is_counted(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ, {"INCIDENT_TRIAGE_STATE": directory}
        ):
            def transport(url, payload, headers):
                return ollama_response(
                    {"tool": "delete_database", "args": {}, "hypothesis": "no"},
                    3,
                    1,
                )

            with self.assertRaises(live_model.ModelDecisionError):
                live_model.plan({"start": "checkout", "hops": []}, transport)
            self.assertIn("input_tokens=3", live_model.spend_text())

    def test_ollama_requests_are_constrained_to_known_services(self) -> None:
        requests: list[dict] = []

        def transport(url, payload, headers):
            requests.append(payload)
            revising = payload["messages"][0]["content"].startswith("Revise")
            return ollama_response(DRAFT["conclusion"] if revising else DRAFT, 3, 1)

        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ, {"INCIDENT_TRIAGE_STATE": directory}
        ):
            checkout = story.logs_for("checkout", "4bf92f3577b34da6")
            live_model.plan(
                {"start": "checkout", "hops": [], "force_draft": False}, transport
            )
            payments = story.logs_for("payments", "4bf92f3577b34da6")
            ledger = story.logs_for("ledger", "4bf92f3577b34da6")
            live_model.plan(
                {"start": "checkout", "hops": [checkout], "force_draft": False},
                transport,
            )
            live_model.plan(
                {
                    "start": "checkout",
                    "hops": [checkout, payments, ledger],
                    "force_draft": False,
                },
                transport,
            )
            live_model.plan(
                {"start": "checkout", "hops": [], "force_draft": True}, transport
            )
            live_model.revise(DRAFT["conclusion"], "Name the pool size.", [], transport)

        schemas = [
            request["response_format"]["json_schema"]["schema"]
            for request in requests
        ]

        def services(lookup: dict) -> list:
            return lookup["properties"]["args"]["properties"]["service"]["enum"]

        self.assertEqual(
            schemas[0]["properties"]["tool"]["enum"], ["get_logs_by_trace_id"]
        )
        self.assertEqual(services(schemas[0]), ["checkout"])
        self.assertNotIn("anyOf", schemas[1])
        self.assertEqual(
            services(schemas[1]),
            [name for name in live_model.ALLOWED_SERVICES if name != "checkout"],
        )
        lookup, draft = schemas[2]["anyOf"]
        self.assertEqual(
            services(lookup), ["api-gateway", "inventory"]
        )
        self.assertEqual(
            draft["properties"]["conclusion"]["properties"]["call_path"]["items"][
                "enum"
            ],
            list(live_model.ALLOWED_SERVICES),
        )
        self.assertEqual(schemas[3]["properties"]["tool"]["enum"], ["draft"])
        self.assertIn("errors_per_hop", schemas[4]["properties"])
        for request in requests:
            self.assertEqual(request["max_tokens"], live_model.MAX_OUTPUT_TOKENS)

    def test_plan_lists_checked_services_and_unchecked_error_leads(self) -> None:
        requests: list[dict] = []

        def transport(url, payload, headers):
            requests.append(json.loads(payload["messages"][1]["content"]))
            return ollama_response(DRAFT, 3, 1)

        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ, {"INCIDENT_TRIAGE_STATE": directory}
        ):
            checkout = story.logs_for("checkout", "4bf92f3577b34da6")
            payments = story.logs_for("payments", "4bf92f3577b34da6")
            live_model.plan(
                {"start": "checkout", "hops": [checkout, payments]}, transport
            )

        self.assertEqual(requests[0]["looked_up"], ["checkout", "payments"])
        self.assertEqual(requests[0]["unchecked_error_leads"], ["ledger"])

    def test_remote_providers_are_not_sent_a_schema(self) -> None:
        requests: list[dict] = []

        def transport(url, payload, headers):
            requests.append(payload)
            return ollama_response(DRAFT, 3, 1)

        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {
                "INCIDENT_TRIAGE_STATE": directory,
                "INCIDENT_TRIAGE_MODEL_ID": "openai-nano",
                "MODEL_AI_KEY": "test-key",
            },
        ):
            live_model.plan({"start": "checkout", "hops": []}, transport)

        self.assertNotIn("response_format", requests[0])
        self.assertNotIn("max_tokens", requests[0])

    def test_scripted_mode_keeps_the_inventory_wrong_turn(self) -> None:
        with patch.dict(os.environ, {"INCIDENT_TRIAGE_MODEL": "scripted"}):
            self.assertFalse(live_model.enabled())
        checkout = story.logs_for("checkout", "4bf92f3577b34da6")
        decision = story.plan_next(
            {"start": "checkout", "hops": [checkout], "guidance": []}
        )
        self.assertEqual(decision["args"]["service"], "inventory")
        self.assertEqual(decision["hypothesis"], story.WRONG_ROOT_CAUSE)


class LiveWorkflowTests(unittest.IsolatedAsyncioTestCase):
    async def test_workflow_calls_a_local_model_and_prints_spend(self) -> None:
        seen: list[str] = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                length = int(self.headers.get("content-length", "0"))
                self.rfile.read(length)
                seen.append(self.path)
                body = json.dumps(ollama_response(DRAFT, 80, 20)).encode("utf-8")
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format: str, *args: object) -> None:
                return

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as directory:
                catalog = Path(directory) / "models.yaml"
                catalog.write_text(
                    "models:\n"
                    "  - id: llama3.2-local\n"
                    "    provider: ollama\n"
                    "    model: llama3.2:latest\n"
                    f"    endpoint: http://127.0.0.1:{port}\n",
                    encoding="utf-8",
                )
                state = Path(directory) / "state"
                with patch.dict(
                    os.environ,
                    {
                        "INCIDENT_TRIAGE_MODEL": "live",
                        "INCIDENT_TRIAGE_CATALOG": str(catalog),
                        "INCIDENT_TRIAGE_STATE": str(state),
                    },
                ):
                    notice = main.configure_mode("live")
                    self.assertIn("llama3.2:latest", notice)
                    self.assertIn(str(port), notice)
                    with TestWorkflowEnvironment(
                        build_registry(), data_converter=PydanticDataConverter()
                    ) as env:
                        execution = await env.client.start_workflow(
                            WORKFLOW,
                            RecipeInput(
                                story.trace_text(),
                                {
                                    "approval_window_seconds": 1000,
                                    "scan_seconds": 0,
                                    "rescan_seconds": 0,
                                },
                            ),
                            workflow_id="live-incident",
                            task_list="test-task-list",
                        )
                        status = await env.client.query_workflow(
                            "live-incident",
                            execution.run_id,
                            STATUS_QUERY,
                            result_type=dict,
                        )
                    spend = live_model.spend_text()
        finally:
            server.shutdown()
            thread.join(timeout=5)
        self.assertEqual(seen, ["/v1/chat/completions"])
        self.assertEqual(status["phase"], "AWAITING_GUIDANCE")
        self.assertIn("payments v2.14", status["conclusion"]["root_cause"])
        self.assertIn("input_tokens=80", spend)
        self.assertIn("output_tokens=20", spend)
        self.assertIn("this_external_usd=0", spend)
        self.assertIn("running_input_tokens=80", spend)
