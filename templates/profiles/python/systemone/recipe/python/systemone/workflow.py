"""Durable SystemOne orchestration with an offline default."""

from dataclasses import dataclass
from datetime import timedelta

from cadence import activity, workflow
from cadence.worker import Registry


WORKFLOW = "__RECIPE_CLASS__Workflow"
MOCK_CLASSIFY_ACTIVITY = "__RECIPE_SLUG__.mock-classify"
CLASSIFY_ACTIVITY = "__RECIPE_SLUG__.systemone-classify"
ACTIVITY_OPTIONS = {
    "schedule_to_close_timeout": timedelta(seconds=30),
    "retry_policy": {
        "initial_interval": timedelta(seconds=1),
        "backoff_coefficient": 2.0,
        "maximum_attempts": 3,
        "non_retryable_error_reasons": [
            "LiveAuthenticationError", "LiveConfigurationError", "LiveSchemaError"
        ],
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


class __RECIPE_CLASS__Workflow:
    @workflow.run
    async def run(self, recipe_input: RecipeInput) -> Classification:
        if recipe_input.mode not in {"mock", "live"}:
            raise ValueError("mode must be 'mock' or 'live'")
        activity_name = (
            MOCK_CLASSIFY_ACTIVITY if recipe_input.mode == "mock" else CLASSIFY_ACTIVITY
        )
        return await workflow.execute_activity(
            activity_name, Classification, recipe_input.text, **ACTIVITY_OPTIONS
        )


def build_registry(live_activities: object | None = None) -> Registry:
    registry = Registry()
    registry.workflow(__RECIPE_CLASS__Workflow)
    registry.register_activity(mock_classify)
    if live_activities is not None:
        registry.register_activities(live_activities)
    return registry
