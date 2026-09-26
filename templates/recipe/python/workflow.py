"""Replace the starter data and decisions; keep external calls in Activities."""

from dataclasses import dataclass
from datetime import timedelta

from cadence import activity, workflow
from cadence.worker import Registry


WORKFLOW = "__RECIPE_CLASS__Workflow"
CLASSIFY_ACTIVITY = "__RECIPE_SLUG__.classify"
MOCK_MODEL_ACTIVITY = "__RECIPE_SLUG__.mock-model"
AI_OPTIONS = {
    "schedule_to_close_timeout": timedelta(seconds=30),
    "retry_policy": {"initial_interval": timedelta(seconds=1),
                     "backoff_coefficient": 2.0, "maximum_attempts": 3,
                     "non_retryable_error_reasons": [
                         "LiveAuthenticationError", "LiveConfigurationError",
                         "LiveSchemaError",
                     ]},
}


@dataclass(frozen=True)
class RecipeInput:
    text: str
    mode: str = "mock"
    agent_framework: str = "google-adk"
    model_provider: str = "google"
    model_name: str | None = None


@dataclass(frozen=True)
class ClassificationDecision:
    warrants_model: bool
    reason: str


@dataclass(frozen=True)
class RecipeResult:
    decision: ClassificationDecision
    output: str | None


@activity.defn(name=CLASSIFY_ACTIVITY)
def mock_classify(text: str) -> ClassificationDecision:
    needed = "investigate" in text.lower()
    return ClassificationDecision(needed, "mock keyword decision")


@activity.defn(name=MOCK_MODEL_ACTIVITY)
def mock_model(text: str) -> str:
    return f"Mock analysis: {text}"


class __RECIPE_CLASS__Workflow:
    @workflow.run
    async def run(self, recipe_input: RecipeInput) -> RecipeResult:
        decision = await workflow.execute_activity(
            CLASSIFY_ACTIVITY, ClassificationDecision, recipe_input.text, **AI_OPTIONS
        )
        if not decision.warrants_model:
            return RecipeResult(decision, None)
        if recipe_input.mode == "mock":
            output = await workflow.execute_activity(
                MOCK_MODEL_ACTIVITY, str, recipe_input.text, **AI_OPTIONS
            )
        else:
            from inference import generate_live_output

            output = await generate_live_output(
                recipe_input.text, recipe_input.model_name,
                recipe_input.agent_framework, recipe_input.model_provider,
            )
        return RecipeResult(decision, output)


def build_registry(classifier=mock_classify, model_activities=None) -> Registry:
    registry = Registry()
    registry.workflow(__RECIPE_CLASS__Workflow)
    registry.register_activity(classifier)
    if model_activities is None:
        registry.register_activity(mock_model)
    else:
        registry.register_activities(model_activities)
    return registry
