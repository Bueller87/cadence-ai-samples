"""Explicit opt-in local Cadence E2E using the upstream scripted test definitions."""

import ast
import asyncio
import importlib.metadata as metadata
import json
import os
from pathlib import Path
import httpx
import threading
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from unittest.mock import patch
from uuid import uuid4

import cadence
from agents import Agent, RunConfig, Runner, function_tool
from cadence.api.v1.service_domain_pb2 import RegisterDomainRequest, DeprecateDomainRequest
from cadence.api.v1.service_workflow_pb2 import GetWorkflowExecutionHistoryRequest
from cadence.contrib.openai import OpenAIActivities, PydanticDataConverter
from cadence.worker import Worker
from google.protobuf.duration import from_timedelta


def upstream_definitions():
    # Reuse the exact upstream workflow, tool, and response generator without
    # importing its Docker fixtures or running pytest collection.
    source = Path(__file__).parent / '.venv/source/tests/integration_tests/openai/test_model_tool_model.py'
    names = {'_WORKFLOW', '_FINAL_ANSWER', '_registry', 'greet', '_ModelToolModelWorkflow', '_response'}
    tree = ast.parse(source.read_text())
    selected = [node for node in tree.body if
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name in names
                or isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id in names for t in node.targets)]
    namespace = globals().copy()
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(source), 'exec'), namespace)
    return namespace


async def run():
    print('Loading upstream test definitions', flush=True)
    upstream = upstream_definitions()
    requests = []
    server_errors = []

    class MockEndpoint(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            if self.path != '/v1/responses':
                server_errors.append('unexpected endpoint path')
                self.send_error(400)
                return
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            requests.append(body)
            response = upstream['_response'](body, len(requests))
            encoded = json.dumps(response).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

    server = ThreadingHTTPServer(('127.0.0.1', 0), MockEndpoint)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    original_send = httpx.AsyncClient.send

    async def local_only(client, request, **kwargs):
        if request.url.host != '127.0.0.1' or request.url.port != port:
            raise RuntimeError('Non-test network destination blocked')
        return await original_send(client, request, **kwargs)

    suffix = uuid4().hex[:12]
    domain = 'pr176-e2e-' + suffix
    task_list = domain
    converter = PydanticDataConverter()
    # Override provider credentials and bypass proxies for the loopback endpoint.
    environment = {'OPENAI_API_KEY': 'local-test-placeholder',
                   'OPENAI_BASE_URL': f'http://127.0.0.1:{port}/v1/',
                   'OPENAI_AGENTS_DISABLE_TRACING': '1', 'NO_PROXY': '*', 'no_proxy': '*',
                   'OPENAI_ORG_ID': '', 'OPENAI_PROJECT_ID': ''}
    try:
        with patch.dict(os.environ, environment), patch.object(httpx.AsyncClient, 'send', local_only):
            registry = cadence.Registry.of(upstream['_registry'])
            activities = OpenAIActivities()
            registry.register_activities(activities)
            async with cadence.Client(domain=domain, target='127.0.0.1:7833', data_converter=converter) as client:
                print('Connecting to local Cadence', flush=True)
                await asyncio.wait_for(client.ready(), 10)
                await client.domain_stub.RegisterDomain(RegisterDomainRequest(
                    name=domain, workflow_execution_retention_period=from_timedelta(timedelta(days=1))))
                print('Registered test domain: ' + domain, flush=True)
                try:
                    async with Worker(client, task_list, registry):
                        execution = await client.start_workflow(
                            upstream['_WORKFLOW'], 'Call the greeting tool.', task_list=task_list,
                            workflow_id=domain, execution_start_to_close_timeout=timedelta(seconds=30))
                        async with asyncio.timeout(50):
                            while True:
                                events, token = [], b''
                                while True:
                                    page = await client.workflow_stub.GetWorkflowExecutionHistory(
                                        GetWorkflowExecutionHistoryRequest(domain=domain,
                                            workflow_execution=execution, next_page_token=token, skip_archival=True))
                                    events.extend(page.history.events)
                                    token = page.next_page_token
                                    if not token:
                                        break
                                terminal = [e for e in events if e.WhichOneof('attributes') in {
                                    'workflow_execution_completed_event_attributes',
                                    'workflow_execution_failed_event_attributes',
                                    'workflow_execution_timed_out_event_attributes'}]
                                if terminal:
                                    break
                                await asyncio.sleep(0.25)
                    last = terminal[-1]
                    status = last.WhichOneof('attributes')
                    result = None
                    if last.HasField('workflow_execution_completed_event_attributes'):
                        result = converter.from_data(last.workflow_execution_completed_event_attributes.result, [str])[0]
                    sequence = [e.activity_task_scheduled_event_attributes.activity_type.name
                                for e in events if e.HasField('activity_task_scheduled_event_attributes')]
                    completed = sum(e.HasField('activity_task_completed_event_attributes') for e in events)
                    failed = sum(e.HasField('activity_task_failed_event_attributes') for e in events)
                    report = dict(sdk=metadata.version('cadence-python-client'), domain=domain,
                        workflow_id=execution.workflow_id, run_id=execution.run_id, status=status,
                        result=result, scheduled=sequence, completed_activities=completed,
                        failed_activities=failed, mock_requests=len(requests), mock_errors=server_errors)
                    print(json.dumps(report, indent=2))
                    assert result == upstream['_FINAL_ANSWER']
                    assert sequence == ['OpenAIActivities.invoke_model', 'greet', 'OpenAIActivities.invoke_model']
                    assert completed == 3 and failed == 0 and len(requests) == 2 and not server_errors
                    assert any(item.get('type') == 'function_call_output' for item in requests[1]['input'])
                finally:
                    await activities._openai_provider._get_client().close()
                    await client.domain_stub.DeprecateDomain(DeprecateDomainRequest(name=domain))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-local', action='store_true', required=True)
    parser.parse_args()
    try:
        asyncio.run(run())
    except Exception:
        import traceback
        traceback.print_exc()
        raise SystemExit(1)
