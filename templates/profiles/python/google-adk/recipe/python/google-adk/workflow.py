"""Durable mock-first Google ADK starter Workflow."""

from dataclasses import dataclass
from datetime import timedelta

from cadence import activity, workflow
from cadence.worker import Registry


WORKFLOW = "__RECIPE_CLASS__Workflow"
MOCK_AGENT_ACTIVITY = "__RECIPE_SLUG__.mock-agent"
LIVE_AGENT_ACTIVITY = "__RECIPE_SLUG__.google-adk-agent"
FATAL_AGENT_ERRORS = {
    "LiveAuthenticationError",
    "LiveConfigurationError",
    "LiveSchemaError",
}
AGENT_ACTIVITY_OPTIONS = {
    "schedule_to_close_timeout": timedelta(seconds=30),
    "retry_policy": {
        "initial_interval": timedelta(seconds=1),
        "backoff_coefficient": 2.0,
        "maximum_interval": timedelta(seconds=4),
        "maximum_attempts": 3,
        "non_retryable_error_reasons": sorted(FATAL_AGENT_ERRORS),
    },
}


@dataclass(frozen=True)
class AgentInput:
    prompt: str
    mode: str = "mock"


@dataclass(frozen=True)
class AgentResult:
    response: str
    mode: str


@activity.defn(name=MOCK_AGENT_ACTIVITY)
def mock_agent(prompt: str) -> str:
    """Deterministic offline substitute for the live agent boundary."""
    normalized = " ".join(prompt.split())
    if not normalized:
        raise ValueError("prompt must not be empty")
    return f"Mock agent received: {normalized}"


class __RECIPE_CLASS__Workflow:
    @workflow.run
    async def run(self, agent_input: AgentInput) -> AgentResult:
        self._validate(agent_input)
        if agent_input.mode == "mock":
            response = await workflow.execute_activity(
                MOCK_AGENT_ACTIVITY,
                str,
                agent_input.prompt,
                **AGENT_ACTIVITY_OPTIONS,
            )
        else:
            response = await workflow.execute_activity(
                LIVE_AGENT_ACTIVITY,
                str,
                agent_input.prompt,
                **AGENT_ACTIVITY_OPTIONS,
            )
        return AgentResult(response=response, mode=agent_input.mode)

    @staticmethod
    def _validate(agent_input: AgentInput) -> None:
        if not agent_input.prompt.strip():
            raise ValueError("prompt must not be empty")
        if agent_input.mode not in {"mock", "live"}:
            raise ValueError("mode must be 'mock' or 'live'")


def build_registry(model_activities: object | None = None) -> Registry:
    registry = Registry()
    registry.workflow(__RECIPE_CLASS__Workflow)
    registry.register_activity(mock_agent)
    if model_activities is not None:
        registry.register_activities(model_activities)
    return registry
