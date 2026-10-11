"""Durable orchestration with offline mock and Activity-backed agent modes."""

from dataclasses import dataclass
from datetime import timedelta

from cadence import activity, workflow
from cadence.worker import Registry


WORKFLOW = "__RECIPE_CLASS__Workflow"
MOCK_AGENT_ACTIVITY = "__RECIPE_SLUG__.mock-agent"
LIVE_AGENT_ACTIVITY = "__RECIPE_SLUG__.openai-agents-agent"
FATAL_AI_ERRORS = {
    "LiveAuthenticationError",
    "LiveConfigurationError",
    "LiveSchemaError",
}
AI_ACTIVITY_OPTIONS = {
    "schedule_to_close_timeout": timedelta(seconds=30),
    "retry_policy": {
        "initial_interval": timedelta(seconds=1),
        "backoff_coefficient": 2.0,
        "maximum_attempts": 3,
        "non_retryable_error_reasons": sorted(FATAL_AI_ERRORS),
    },
}


@dataclass(frozen=True)
class RecipeInput:
    text: str
    mode: str = "mock"


@dataclass(frozen=True)
class RecipeResult:
    answer: str
    mode: str


@activity.defn(name=MOCK_AGENT_ACTIVITY)
def mock_agent(text: str) -> str:
    """Deterministic offline stand-in for the OpenAI Agents path."""
    cleaned = text.strip()
    if not cleaned:
        raise ValueError("text must not be empty")
    return f"Mock agent reviewed: {cleaned}"


class __RECIPE_CLASS__Workflow:
    @workflow.run
    async def run(self, recipe_input: RecipeInput) -> RecipeResult:
        if recipe_input.mode not in {"mock", "live"}:
            raise ValueError("mode must be 'mock' or 'live'")
        if recipe_input.mode == "live":
            answer = await workflow.execute_activity(
                LIVE_AGENT_ACTIVITY,
                str,
                recipe_input.text,
                **AI_ACTIVITY_OPTIONS,
            )
        else:
            answer = await workflow.execute_activity(
                MOCK_AGENT_ACTIVITY,
                str,
                recipe_input.text,
                **AI_ACTIVITY_OPTIONS,
            )
        return RecipeResult(answer=answer, mode=recipe_input.mode)


def build_registry(model_activities: object | None = None) -> Registry:
    registry = Registry()
    registry.workflow(__RECIPE_CLASS__Workflow)
    registry.register_activity(mock_agent)
    if model_activities is not None:
        registry.register_activities(model_activities)
    return registry
