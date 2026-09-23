"""Small, opt-in Cadence agent/model compatibility spike.

No cloud call occurs merely by importing this module, starting a worker, or
running tests. The ``start`` command requires an explicit confirmation for a
cloud-backed case. Credentials stay in the worker environment and are never
workflow inputs or command output.
"""

from __future__ import annotations

import argparse
import asyncio
import os
from collections import Counter
from dataclasses import dataclass, field
from datetime import timedelta
from enum import StrEnum
from typing import Any, Iterable, Mapping
from urllib.parse import urlparse

from agents import (
    AgentOutputSchemaBase,
    Handoff,
    ModelResponse,
    ModelSettings,
    ModelTracing,
    Tool,
)
from agents.items import TResponseInputItem, TResponseOutputItem
from agents.usage import deserialize_usage, serialize_usage
from cadence import activity as cadence_activity
from cadence.contrib.openai.cadence_model import CadenceModel
from openai.types.responses import ResponsePromptParam
from pydantic import TypeAdapter


OPENAI_WORKFLOW = "OpenAIAgentCompatibilityWorkflow"
ADK_WORKFLOW = "GoogleADKAgentCompatibilityWorkflow"
RESUME_SIGNAL = "resume-after-model"
OPENAI_ACTIVITY = "OpenAIActivities.invoke_model"
OPENAI_COMPAT_ACTIVITY = "OpenAICompatibleChatCompletions.invoke_model"
ADK_ACTIVITY = "GoogleADKActivities.generate_content_async"
ECHO_ACTIVITY = "echo_token"
TOOL_TOKEN = "compatibility-check"
RESPONSE_OUTPUT_ITEM_ADAPTER = TypeAdapter(TResponseOutputItem)


class SpikeCase(StrEnum):
    OPENAI_OPENAI = "openai-openai"
    OPENAI_GEMINI = "openai-gemini"
    OPENAI_OLLAMA = "openai-ollama"
    ADK_GEMINI = "adk-gemini"
    OPENAI_CLAUDE = "openai-claude"
    ADK_OLLAMA = "adk-ollama"


@dataclass(frozen=True)
class ActivityObservation:
    """Sanitized history data; it never includes activity input or result payloads."""

    lifecycle_event: str
    activity_type: str


@dataclass(frozen=True)
class HistorySummary:
    scheduled: dict[str, int]
    completed: dict[str, int]
    failed: dict[str, int]
    timed_out: dict[str, int]


@dataclass(frozen=True)
class HistoryEvidence:
    """Payload-free evidence needed for replay and tool-call verification."""

    activities: HistorySummary
    signals: tuple[str, ...]
    terminal_status: str | None


@dataclass(frozen=True)
class OpenAICompatibleProviderConfig:
    """Worker-only configuration; the credential is omitted from representations."""

    base_url: str
    api_key: str = field(repr=False)


def openai_compatible_provider_config(
    case: SpikeCase,
    environ: Mapping[str, str] = os.environ,
) -> OpenAICompatibleProviderConfig:
    """Read alternative-provider settings only while configuring a worker."""

    if case is SpikeCase.OPENAI_GEMINI:
        api_key = environ.get("GEMINI_API_KEY", "").strip()
        if not api_key:
            raise ValueError("openai-gemini worker requires GEMINI_API_KEY")
        base_url = environ.get(
            "GEMINI_OPENAI_BASE_URL",
            "https://generativelanguage.googleapis.com/v1beta/openai/",
        ).strip()
    elif case is SpikeCase.OPENAI_OLLAMA:
        api_key = environ.get("OLLAMA_OPENAI_API_KEY", "ollama").strip() or "ollama"
        base_url = environ.get(
            "OLLAMA_OPENAI_BASE_URL",
            "http://localhost:11434/v1/",
        ).strip()
    else:
        raise ValueError(f"{case.value} is not an OpenAI-compatible provider case")

    parsed = urlparse(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("the OpenAI-compatible base URL must be an absolute HTTP URL")
    if case is SpikeCase.OPENAI_OLLAMA and parsed.hostname not in {
        "localhost",
        "127.0.0.1",
        "::1",
    }:
        raise ValueError("openai-ollama requires a loopback Ollama endpoint")
    return OpenAICompatibleProviderConfig(base_url=base_url, api_key=api_key)


class OpenAICompatibleChatCompletionsActivities:
    """App-local Activity for OpenAI-compatible Chat Completions backends."""

    def __init__(self, provider: Any | None = None) -> None:
        self._provider = provider

    @cadence_activity.method(name=OPENAI_COMPAT_ACTIVITY)
    async def invoke_model(
        self,
        model_name: str,
        system_instructions: str | None,
        input: Any,
        model_settings: dict[str, Any],
        tracing: int,
        previous_response_id: str | None,
        conversation_id: str | None,
    ) -> dict[str, Any]:
        """Call the provider outside workflow code using JSON-safe arguments."""

        if self._provider is None:
            raise RuntimeError("the worker did not configure a model provider")
        if not isinstance(model_settings, dict):
            raise ValueError("model settings must decode to an object")
        model = self._provider.get_model(model_name)
        response = await model.get_response(
            system_instructions=system_instructions,
            input=input,
            model_settings=ModelSettings(**model_settings),
            tools=[],
            output_schema=None,
            handoffs=[],
            tracing=ModelTracing(tracing),
            previous_response_id=previous_response_id,
            conversation_id=conversation_id,
            prompt=None,
        )
        return {
            "output": [item.model_dump(mode="json") for item in response.output],
            "usage": serialize_usage(response.usage),
            "response_id": response.response_id,
            "request_id": response.request_id,
        }


class OpenAICompatibleCadenceModel(CadenceModel):
    """Released Cadence model redirected to the app-local Activity name."""

    def __init__(
        self,
        model_name: str,
        activities: Any | None = None,
    ) -> None:
        self._model_name = model_name
        self._openai_activities = (
            activities or OpenAICompatibleChatCompletionsActivities()
        )

    async def get_response(
        self,
        system_instructions: str | None,
        input: str | list[TResponseInputItem],
        model_settings: ModelSettings,
        tools: list[Tool],
        output_schema: AgentOutputSchemaBase | None,
        handoffs: list[Handoff],
        tracing: ModelTracing,
        *,
        previous_response_id: str | None,
        conversation_id: str | None,
        prompt: ResponsePromptParam | None,
    ) -> ModelResponse:
        if tools or handoffs or output_schema is not None or prompt is not None:
            raise ValueError("this compatibility path supports plain no-tool requests only")
        response = await self._openai_activities.invoke_model(
            model_name=self._model_name,
            system_instructions=system_instructions,
            input=input,
            model_settings=model_settings.to_json_dict(),
            tracing=tracing.value,
            previous_response_id=previous_response_id,
            conversation_id=conversation_id,
        )
        return ModelResponse(
            output=[
                RESPONSE_OUTPUT_ITEM_ADAPTER.validate_python(item)
                for item in response["output"]
            ],
            usage=deserialize_usage(response["usage"]),
            response_id=response.get("response_id"),
            request_id=response.get("request_id"),
        )


def build_openai_compatible_activities(
    case: SpikeCase,
) -> OpenAICompatibleChatCompletionsActivities:
    """Build the provider client for an Activity worker, never for Workflow code."""

    from agents import OpenAIProvider
    from openai import AsyncOpenAI

    config = openai_compatible_provider_config(case)
    client = AsyncOpenAI(
        api_key=config.api_key,
        base_url=config.base_url,
        max_retries=0,
    )
    return OpenAICompatibleChatCompletionsActivities(
        OpenAIProvider(openai_client=client, use_responses=False)
    )


def validate_start_request(
    case: SpikeCase,
    model: str,
    confirm_live: bool,
    use_tool: bool = False,
) -> None:
    """Reject unsupported or accidental cloud starts before opening a client."""

    if not model.strip():
        raise ValueError("--model is required")
    if case is SpikeCase.OPENAI_CLAUDE:
        raise ValueError(
            "openai-claude is blocked by cadence-python-client v0.4.0: "
            "the integration hardcodes OpenAIProvider inside its model Activity"
        )
    if case in {
        SpikeCase.OPENAI_OPENAI,
        SpikeCase.OPENAI_GEMINI,
        SpikeCase.ADK_GEMINI,
    } and not confirm_live:
        raise ValueError("cloud-backed cases require --confirm-live")
    if case in {SpikeCase.OPENAI_GEMINI, SpikeCase.OPENAI_OLLAMA} and use_tool:
        raise ValueError(
            f"{case.value} does not support --with-tool until tool calls are validated"
        )
    if case is SpikeCase.OPENAI_OLLAMA and model != "llama3.2:latest":
        raise ValueError("openai-ollama requires model llama3.2:latest")
    if case is SpikeCase.ADK_OLLAMA and not model.startswith("ollama_chat/"):
        raise ValueError("adk-ollama requires an ollama_chat/<model> string")


def workflow_type_for(case: SpikeCase) -> str:
    if case in {
        SpikeCase.OPENAI_OPENAI,
        SpikeCase.OPENAI_GEMINI,
        SpikeCase.OPENAI_OLLAMA,
    }:
        return OPENAI_WORKFLOW
    if case in {SpikeCase.ADK_GEMINI, SpikeCase.ADK_OLLAMA}:
        return ADK_WORKFLOW
    raise ValueError(f"{case.value} has no runnable workflow type")


def expected_model_activity(case: SpikeCase) -> str:
    if case is SpikeCase.OPENAI_OPENAI:
        return OPENAI_ACTIVITY
    if case in {SpikeCase.OPENAI_GEMINI, SpikeCase.OPENAI_OLLAMA}:
        return OPENAI_COMPAT_ACTIVITY
    if case in {SpikeCase.ADK_GEMINI, SpikeCase.ADK_OLLAMA}:
        return ADK_ACTIVITY
    raise ValueError(f"{case.value} has no supported model Activity")


def summarize_observations(observations: Iterable[ActivityObservation]) -> HistorySummary:
    """Count Activity lifecycle events without exposing any workflow payloads."""

    counters = {
        "scheduled": Counter(),
        "completed": Counter(),
        "failed": Counter(),
        "timed_out": Counter(),
    }
    for observation in observations:
        if observation.lifecycle_event in counters:
            counters[observation.lifecycle_event][observation.activity_type] += 1
    return HistorySummary(**{name: dict(value) for name, value in counters.items()})


def analyze_history_events(events: Iterable[Any]) -> HistoryEvidence:
    """Extract only Activity names, Signal names, and terminal status."""

    event_list = list(events)
    signals: list[str] = []
    terminal_status = None
    terminal_by_attributes = {
        "workflow_execution_completed_event_attributes": "COMPLETED",
        "workflow_execution_failed_event_attributes": "FAILED",
        "workflow_execution_timed_out_event_attributes": "TIMED_OUT",
        "workflow_execution_canceled_event_attributes": "CANCELED",
        "workflow_execution_terminated_event_attributes": "TERMINATED",
        "workflow_execution_continued_as_new_event_attributes": "CONTINUED_AS_NEW",
    }
    for event in event_list:
        attributes_name = event.WhichOneof("attributes")
        if attributes_name == "workflow_execution_signaled_event_attributes":
            signals.append(getattr(event, attributes_name).signal_name)
        elif attributes_name in terminal_by_attributes:
            terminal_status = terminal_by_attributes[attributes_name]
    return HistoryEvidence(
        activities=summarize_observations(
            observations_from_history_events(event_list)
        ),
        signals=tuple(signals),
        terminal_status=terminal_status,
    )


def verification_errors(
    stage: str,
    evidence: HistoryEvidence,
    model_activity: str,
    expect_tool: bool,
    baseline_model_scheduled: int | None = None,
) -> list[str]:
    """Return sanitized replay-verification failures for one history snapshot."""

    errors: list[str] = []
    summary = evidence.activities
    scheduled = summary.scheduled.get(model_activity, 0)
    completed = summary.completed.get(model_activity, 0)
    failed = summary.failed.get(model_activity, 0)
    timed_out = summary.timed_out.get(model_activity, 0)

    if scheduled < 1 or completed < 1:
        errors.append("no completed model Activity is present")
    if completed != scheduled or failed or timed_out:
        errors.append("not every scheduled model Activity completed successfully")

    if expect_tool:
        tool_scheduled = summary.scheduled.get(ECHO_ACTIVITY, 0)
        tool_completed = summary.completed.get(ECHO_ACTIVITY, 0)
        tool_failed = summary.failed.get(ECHO_ACTIVITY, 0)
        tool_timed_out = summary.timed_out.get(ECHO_ACTIVITY, 0)
        if (
            tool_scheduled < 1
            or tool_completed != tool_scheduled
            or tool_failed
            or tool_timed_out
        ):
            errors.append("the requested echo_token Activity did not complete")

    if stage == "before-restart":
        if RESUME_SIGNAL in evidence.signals:
            errors.append("the resume Signal arrived before the worker restart")
        if evidence.terminal_status is not None:
            errors.append("the Workflow is not waiting after its final model call")
    elif stage == "after-resume":
        if baseline_model_scheduled is None or baseline_model_scheduled < 1:
            errors.append("a positive --baseline-model-scheduled value is required")
        elif scheduled > baseline_model_scheduled:
            errors.append("an additional model Activity was scheduled after restart")
        elif scheduled < baseline_model_scheduled:
            errors.append("the recorded model-Activity baseline does not match history")
        if RESUME_SIGNAL not in evidence.signals:
            errors.append(f"Signal {RESUME_SIGNAL!r} is absent from history")
        if evidence.terminal_status != "COMPLETED":
            errors.append("the Workflow did not complete successfully after the Signal")
    return errors


def observations_from_history_events(events: Iterable[Any]) -> list[ActivityObservation]:
    """Extract activity names from Cadence protobuf history without decoding payloads."""

    scheduled_types: dict[int, str] = {}
    observations: list[ActivityObservation] = []
    lifecycle_by_attributes = {
        "activity_task_completed_event_attributes": "completed",
        "activity_task_failed_event_attributes": "failed",
        "activity_task_timed_out_event_attributes": "timed_out",
    }
    for event in events:
        attributes_name = event.WhichOneof("attributes")
        if attributes_name == "activity_task_scheduled_event_attributes":
            attributes = getattr(event, attributes_name)
            activity_type = attributes.activity_type.name
            scheduled_types[event.event_id] = activity_type
            observations.append(ActivityObservation("scheduled", activity_type))
            continue
        lifecycle_event = lifecycle_by_attributes.get(attributes_name)
        if lifecycle_event is None:
            continue
        attributes = getattr(event, attributes_name)
        activity_type = scheduled_types.get(attributes.scheduled_event_id, "unknown")
        observations.append(ActivityObservation(lifecycle_event, activity_type))
    return observations


def agent_instruction(use_tool: bool) -> str:
    if use_tool:
        return (
            f"Call echo_token exactly once with token {TOOL_TOKEN!r}. "
            "Do not answer before using the tool. Then answer with READY."
        )
    return "Answer the user with a short plain-text response."


def build_registry(case: SpikeCase):
    """Load SDK integrations only for a worker process, never for offline tests."""

    import cadence

    registry = cadence.Registry()

    @registry.activity(name=ECHO_ACTIVITY)
    async def echo_token(token: str) -> str:
        """Harmless optional tool for observing a separate Activity in history."""

        return f"echo:{token}"

    if case is SpikeCase.OPENAI_OPENAI:
        from agents import Agent, Runner, RunConfig, function_tool
        from cadence.contrib.openai import OpenAIActivities

        registry.register_activities(OpenAIActivities())

        @registry.workflow(name=OPENAI_WORKFLOW)
        class OpenAIAgentCompatibilityWorkflow:
            def __init__(self) -> None:
                self._resumed = False

            @cadence.workflow.run
            async def run(
                self,
                model_name: str,
                prompt: str,
                pause_after_model: bool,
                use_tool: bool,
            ) -> str:
                tools = [function_tool(echo_token)] if use_tool else []
                agent = Agent(
                    name="compatibility-spike",
                    model=model_name,
                    instructions=agent_instruction(use_tool),
                    tools=tools,
                )
                result = await Runner.run(
                    agent,
                    prompt,
                    run_config=RunConfig(tracing_disabled=True),
                )
                if pause_after_model:
                    await cadence.workflow.wait_condition(lambda: self._resumed)
                return str(result.final_output)

            @cadence.workflow.signal(name=RESUME_SIGNAL)
            def resume_after_model(self) -> None:
                self._resumed = True

    elif case in {SpikeCase.OPENAI_GEMINI, SpikeCase.OPENAI_OLLAMA}:
        from agents import Agent, Runner, RunConfig

        registry.register_activities(build_openai_compatible_activities(case))

        @registry.workflow(name=OPENAI_WORKFLOW)
        class OpenAICompatibleAgentWorkflow:
            def __init__(self) -> None:
                self._resumed = False

            @cadence.workflow.run
            async def run(
                self,
                model_name: str,
                prompt: str,
                pause_after_model: bool,
                use_tool: bool,
            ) -> str:
                if use_tool:
                    raise ValueError("tools are not enabled for this compatibility path")
                agent = Agent(
                    name="compatibility-spike",
                    model=OpenAICompatibleCadenceModel(model_name),
                    instructions=agent_instruction(False),
                )
                result = await Runner.run(
                    agent,
                    prompt,
                    run_config=RunConfig(tracing_disabled=True),
                )
                if pause_after_model:
                    await cadence.workflow.wait_condition(lambda: self._resumed)
                return str(result.final_output)

            @cadence.workflow.signal(name=RESUME_SIGNAL)
            def resume_after_model(self) -> None:
                self._resumed = True

    elif case in {SpikeCase.ADK_GEMINI, SpikeCase.ADK_OLLAMA}:
        from cadence.contrib.google_adk import CadenceAgentRunner, GoogleADKActivities
        from google.adk.agents import LlmAgent
        from google.adk.sessions import InMemorySessionService
        from google.genai import types

        registry.register_activities(GoogleADKActivities())

        @registry.workflow(name=ADK_WORKFLOW)
        class GoogleADKAgentCompatibilityWorkflow:
            def __init__(self) -> None:
                self._resumed = False

            @cadence.workflow.run
            async def run(
                self,
                model_name: str,
                prompt: str,
                pause_after_model: bool,
                use_tool: bool,
            ) -> str:
                agent = LlmAgent(
                    name="compatibility_spike",
                    model=model_name,
                    instruction=agent_instruction(use_tool),
                    tools=[echo_token] if use_tool else [],
                )
                session_service = InMemorySessionService()
                runner = CadenceAgentRunner(
                    app_name="cadence-python-agent-compat",
                    agent=agent,
                    session_service=session_service,
                )
                workflow_id = cadence.workflow.WorkflowContext.get().info().workflow_id
                await session_service.create_session(
                    app_name=runner.app_name,
                    user_id="spike-user",
                    session_id=workflow_id,
                )
                final_text = ""
                async for event in runner.run_async(
                    user_id="spike-user",
                    session_id=workflow_id,
                    new_message=types.Content(
                        role="user",
                        parts=[types.Part.from_text(text=prompt)],
                    ),
                ):
                    if event.is_final_response() and event.content and event.content.parts:
                        final_text = "".join(part.text or "" for part in event.content.parts)
                if pause_after_model:
                    await cadence.workflow.wait_condition(lambda: self._resumed)
                return final_text

            @cadence.workflow.signal(name=RESUME_SIGNAL)
            def resume_after_model(self) -> None:
                self._resumed = True
    else:
        raise ValueError(f"{case.value} has no runnable worker configuration")

    return registry


def build_worker_client(target: str, domain: str):
    """Build the worker client with the converter required by agent payloads."""

    import cadence
    from cadence.contrib.pydantic import PydanticDataConverter

    return cadence.Client(
        domain=domain,
        target=target,
        data_converter=PydanticDataConverter(),
    )


async def run_worker(
    target: str, domain: str, task_list: str, case: SpikeCase
) -> None:
    import cadence

    worker = cadence.worker.Worker(
        build_worker_client(target, domain),
        task_list,
        build_registry(case),
    )
    async with worker:
        print(f"worker polling case={case.value} domain={domain} task-list={task_list}")
        await asyncio.Event().wait()


async def start_workflow(args: argparse.Namespace) -> None:
    import cadence

    case = SpikeCase(args.case)
    validate_start_request(case, args.model, args.confirm_live, args.with_tool)
    client = cadence.Client(domain=args.domain, target=args.target)
    execution = await client.start_workflow(
        workflow_type_for(case),
        args.model,
        args.prompt,
        args.pause_after_model,
        args.with_tool,
        task_list=args.task_list,
        workflow_id=args.workflow_id,
        execution_start_to_close_timeout=timedelta(minutes=60),
        task_start_to_close_timeout=timedelta(seconds=30),
    )
    print(
        f"started case={case.value} workflow-id={execution.workflow_id} run-id={execution.run_id}"
    )
    print("No credentials, workflow inputs, or model output are printed by this command.")
    if args.pause_after_model:
        print(
            "After the model Activity completes, stop and restart the worker, then run "
            f"the resume command with signal name {RESUME_SIGNAL!r}."
        )
    if args.with_tool:
        print(
            f"The agent was instructed to call {ECHO_ACTIVITY} with a fixed test token; "
            "verify its completed Activity in history with --expect-tool."
        )


async def resume_workflow(args: argparse.Namespace) -> None:
    import cadence

    client = cadence.Client(domain=args.domain, target=args.target)
    await client.signal_workflow(args.workflow_id, args.run_id or "", RESUME_SIGNAL)
    print(f"sent {RESUME_SIGNAL} to workflow-id={args.workflow_id}")


async def fetch_complete_history_events(
    client: Any,
    domain: str,
    workflow_id: str,
    run_id: str = "",
) -> list[Any]:
    """Read every history page without decoding any event payload."""

    from cadence.api.v1.common_pb2 import WorkflowExecution
    from cadence.api.v1.service_workflow_pb2 import GetWorkflowExecutionHistoryRequest

    events: list[Any] = []
    next_page_token = b""
    while True:
        request = GetWorkflowExecutionHistoryRequest(
            domain=domain,
            workflow_execution=WorkflowExecution(
                workflow_id=workflow_id,
                run_id=run_id,
            ),
            page_size=1000,
            next_page_token=next_page_token,
        )
        response = await client.workflow_stub.GetWorkflowExecutionHistory(request)
        events.extend(response.history.events)
        if not response.next_page_token:
            break
        next_page_token = response.next_page_token
    return events


async def inspect_history(args: argparse.Namespace) -> None:
    from cadence import Client

    client = Client(domain=args.domain, target=args.target)
    events = await fetch_complete_history_events(
        client,
        args.domain,
        args.workflow_id,
        args.run_id or "",
    )

    evidence = analyze_history_events(events)
    summary = evidence.activities
    print("Activity history summary (payloads omitted):")
    for lifecycle_event in ("scheduled", "completed", "failed", "timed_out"):
        counts = getattr(summary, lifecycle_event)
        print(f"{lifecycle_event}: {dict(sorted(counts.items()))}")
    print(f"signals: {list(evidence.signals)}")
    print(f"terminal-status: {evidence.terminal_status or 'RUNNING'}")

    if args.stage == "snapshot":
        return
    if not args.case:
        raise ValueError("--case is required for staged history verification")
    model_activity = expected_model_activity(SpikeCase(args.case))
    errors = verification_errors(
        args.stage,
        evidence,
        model_activity,
        args.expect_tool,
        args.baseline_model_scheduled,
    )
    if errors:
        for error in errors:
            print(f"verification-error: {error}")
        raise ValueError(f"{args.stage} history verification failed")
    print(f"verification: PASS ({args.stage})")
    print(
        "baseline-model-scheduled: "
        f"{summary.scheduled.get(model_activity, 0)}"
    )


def parser() -> argparse.ArgumentParser:
    command_parser = argparse.ArgumentParser(description=__doc__)
    command_parser.add_argument("--target", default="localhost:7833")
    command_parser.add_argument("--domain", default="default")
    command_parser.add_argument("--task-list", default="agent-compat-spike")
    subcommands = command_parser.add_subparsers(dest="command", required=True)

    worker = subcommands.add_parser("worker")
    worker.add_argument(
        "--case",
        choices=[
            case.value for case in SpikeCase if case != SpikeCase.OPENAI_CLAUDE
        ],
        required=True,
    )

    start = subcommands.add_parser("start")
    start.add_argument("--case", choices=[case.value for case in SpikeCase], required=True)
    start.add_argument("--model", required=True)
    start.add_argument("--workflow-id", required=True)
    start.add_argument("--prompt", default="Return the word READY.")
    start.add_argument("--confirm-live", action="store_true")
    start.add_argument("--pause-after-model", action="store_true")
    start.add_argument("--with-tool", action="store_true")

    resume = subcommands.add_parser("resume")
    resume.add_argument("--workflow-id", required=True)
    resume.add_argument("--run-id", default="")

    history = subcommands.add_parser("history")
    history.add_argument("--workflow-id", required=True)
    history.add_argument("--run-id", default="")
    history.add_argument("--case", choices=[case.value for case in SpikeCase])
    history.add_argument(
        "--stage",
        choices=["snapshot", "before-restart", "after-resume"],
        default="snapshot",
    )
    history.add_argument("--baseline-model-scheduled", type=int)
    history.add_argument("--expect-tool", action="store_true")
    return command_parser


async def main() -> None:
    command_parser = parser()
    args = command_parser.parse_args()
    try:
        if args.command == "worker":
            await run_worker(
                args.target,
                args.domain,
                args.task_list,
                SpikeCase(args.case),
            )
        elif args.command == "start":
            await start_workflow(args)
        elif args.command == "resume":
            await resume_workflow(args)
        elif args.command == "history":
            await inspect_history(args)
    except ValueError as error:
        command_parser.error(str(error))


def run_cli() -> None:
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("worker stopped")


if __name__ == "__main__":
    run_cli()
