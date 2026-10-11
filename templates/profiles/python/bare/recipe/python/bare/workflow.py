"""Replace the starter data and decisions; keep external calls in Activities."""

from dataclasses import dataclass
from datetime import timedelta

from cadence import activity, workflow
from cadence.worker import Registry


WORKFLOW = "__RECIPE_CLASS__Workflow"
ASSESS_ACTIVITY = "__RECIPE_SLUG__.assess"
FOLLOW_UP_ACTIVITY = "__RECIPE_SLUG__.follow-up"
ACTIVITY_OPTIONS = {
    "schedule_to_close_timeout": timedelta(seconds=30),
    "retry_policy": {"initial_interval": timedelta(seconds=1),
                     "backoff_coefficient": 2.0, "maximum_attempts": 3,
                     "non_retryable_error_reasons": ["InvalidRequestError"]},
}


class InvalidRequestError(ValueError):
    """Input that no retry can fix."""


@dataclass(frozen=True)
class RecipeInput:
    text: str


@dataclass(frozen=True)
class Assessment:
    needs_follow_up: bool
    reason: str


@dataclass(frozen=True)
class RecipeResult:
    assessment: Assessment
    follow_up: str | None


@activity.defn(name=ASSESS_ACTIVITY)
def assess(text: str) -> Assessment:
    """Synthetic stand-in for a lookup, rules engine, or model call."""
    if not text.strip():
        raise InvalidRequestError("text must not be empty")
    needed = "investigate" in text.lower()
    return Assessment(needed, "keyword match" if needed else "no follow-up keyword")


@activity.defn(name=FOLLOW_UP_ACTIVITY)
def follow_up(text: str) -> str:
    """Synthetic stand-in for the side effect your Workflow needs."""
    return f"Follow-up recorded for: {text}"


class __RECIPE_CLASS__Workflow:
    @workflow.run
    async def run(self, recipe_input: RecipeInput) -> RecipeResult:
        assessment = await workflow.execute_activity(
            ASSESS_ACTIVITY, Assessment, recipe_input.text, **ACTIVITY_OPTIONS
        )
        if not assessment.needs_follow_up:
            return RecipeResult(assessment, None)
        output = await workflow.execute_activity(
            FOLLOW_UP_ACTIVITY, str, recipe_input.text, **ACTIVITY_OPTIONS
        )
        return RecipeResult(assessment, output)


def build_registry() -> Registry:
    registry = Registry()
    registry.workflow(__RECIPE_CLASS__Workflow)
    registry.register_activity(assess)
    registry.register_activity(follow_up)
    return registry
