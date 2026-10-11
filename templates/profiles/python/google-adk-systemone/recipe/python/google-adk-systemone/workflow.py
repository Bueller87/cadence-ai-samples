"""SystemOne followed by an Activity-backed Google ADK agent."""

from dataclasses import dataclass
from datetime import timedelta

from cadence import activity, workflow
from cadence.worker import Registry


WORKFLOW = "__RECIPE_CLASS__Workflow"
MOCK_CLASSIFY_ACTIVITY = "__RECIPE_SLUG__.mock-classify"
CLASSIFY_ACTIVITY = "__RECIPE_SLUG__.systemone-classify"
MOCK_AGENT_ACTIVITY = "__RECIPE_SLUG__.mock-agent"
LIVE_AGENT_ACTIVITY = "__RECIPE_SLUG__.google-adk-agent"
FATAL_ERRORS = [
    "LiveAuthenticationError", "LiveConfigurationError", "LiveSchemaError"
]
ACTIVITY_OPTIONS = {
    "schedule_to_close_timeout": timedelta(seconds=30),
    "retry_policy": {
        "initial_interval": timedelta(seconds=1),
        "backoff_coefficient": 2.0,
        "maximum_attempts": 3,
        "non_retryable_error_reasons": FATAL_ERRORS,
    },
}


@dataclass(frozen=True)
class RecipeInput:
    text: str
    mode: str = "mock"


@dataclass(frozen=True)
class Classification:
    label: str
    confidence: float
    probabilities: dict[str, float]
    model: str


@dataclass(frozen=True)
class RecipeResult:
    classification: Classification
    answer: str
    mode: str


@activity.defn(name=MOCK_CLASSIFY_ACTIVITY)
def mock_classify(text: str) -> Classification:
    cleaned = " ".join(text.split())
    if not cleaned:
        raise ValueError("text must not be empty")
    priority = any(word in cleaned.lower() for word in (
        "urgent", "blocked", "outage", "security"
    ))
    label = "priority" if priority else "routine"
    confidence = 0.95 if priority else 0.90
    return Classification(label, confidence, {label: confidence}, "mock-systemone-v1")


@activity.defn(name=MOCK_AGENT_ACTIVITY)
def mock_agent(text: str) -> str:
    cleaned = " ".join(text.split())
    if not cleaned:
        raise ValueError("text must not be empty")
    return f"Mock agent reviewed {cleaned}"


class __RECIPE_CLASS__Workflow:
    @workflow.run
    async def run(self, recipe_input: RecipeInput) -> RecipeResult:
        if recipe_input.mode not in {"mock", "live"}:
            raise ValueError("mode must be 'mock' or 'live'")
        if recipe_input.mode == "mock":
            classification = await workflow.execute_activity(
                MOCK_CLASSIFY_ACTIVITY, Classification, recipe_input.text,
                **ACTIVITY_OPTIONS,
            )
            answer = await workflow.execute_activity(
                MOCK_AGENT_ACTIVITY, str, recipe_input.text, **ACTIVITY_OPTIONS
            )
        else:
            classification = await workflow.execute_activity(
                CLASSIFY_ACTIVITY, Classification, recipe_input.text,
                **ACTIVITY_OPTIONS,
            )
            answer = await workflow.execute_activity(
                LIVE_AGENT_ACTIVITY, str, recipe_input.text, **ACTIVITY_OPTIONS
            )
        return RecipeResult(classification, answer, recipe_input.mode)


def build_registry(
    classifier_activities: object | None = None,
    model_activities: object | None = None,
) -> Registry:
    registry = Registry()
    registry.workflow(__RECIPE_CLASS__Workflow)
    registry.register_activity(mock_classify)
    registry.register_activity(mock_agent)
    if classifier_activities is not None:
        registry.register_activities(classifier_activities)
    if model_activities is not None:
        registry.register_activities(model_activities)
    return registry
