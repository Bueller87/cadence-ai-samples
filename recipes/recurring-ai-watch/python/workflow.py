"""Mock-only durable Workflow for a fictional dependency release watch."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import timedelta

from cadence import activity, workflow
from cadence.error import ActivityFailure
from cadence.worker import Registry


WATCH_WORKFLOW = "RecurringAIWatchWorkflow"
CHECK_NOW_SIGNAL = "check-now"
STOP_WATCH_SIGNAL = "stop-watch"
WATCH_STATUS_QUERY = "watch-status"

GET_UPDATE_ACTIVITY = "recurring-watch.get-scripted-update"
CLASSIFY_UPDATE_ACTIVITY = "recurring-watch.classify-update"
GENERATE_REPORT_ACTIVITY = "recurring-watch.generate-mock-report"

ACTIVITY_OPTIONS = {"schedule_to_close_timeout": timedelta(seconds=10)}
PROCESSING_ACTIVITY_OPTIONS = {
    "schedule_to_close_timeout": timedelta(seconds=30),
    "retry_policy": {
        "initial_interval": timedelta(seconds=1),
        "backoff_coefficient": 2.0,
        "maximum_interval": timedelta(seconds=4),
        "maximum_attempts": 3,
        "non_retryable_error_reasons": [
            "LiveAuthenticationError",
            "LiveConfigurationError",
            "LiveSchemaError",
        ],
    },
}
CHECKS_PER_RUN = 20
LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class ReleaseNote:
    version: str
    notes: str


@dataclass(frozen=True)
class UpdateCheck:
    update: ReleaseNote
    is_new: bool


@dataclass(frozen=True)
class MockClassification:
    warrants_report: bool
    reason: str


@dataclass(frozen=True)
class WatchInput:
    interval: timedelta = timedelta(seconds=15)
    max_checks: int | None = None
    next_update_index: int = 0
    latest_update: ReleaseNote | None = None
    latest_report: str | None = None
    cumulative_check_count: int = 0
    wait_before_next_check: bool = False
    pending_update: ReleaseNote | None = None
    pending_stage: str | None = None
    mode: str = "mock"
    model_name: str | None = None


@dataclass(frozen=True)
class WatchStatus:
    check_count: int
    latest_update: ReleaseNote | None
    latest_report: str | None
    state: str


SCRIPTED_RELEASES = (
    ReleaseNote("2.4.0", "Documentation corrections and a changelog refresh."),
    ReleaseNote(
        "2.5.0", "Retry defaults changed for outbound HTTP calls in background jobs."
    ),
    ReleaseNote("2.5.1", "Corrected an example command in the installation guide."),
)


@activity.defn(name=GET_UPDATE_ACTIVITY)
def get_scripted_update(index: int) -> UpdateCheck:
    if index < len(SCRIPTED_RELEASES):
        update = SCRIPTED_RELEASES[index]
        LOGGER.info("watch update version=%s new=true", update.version)
        return UpdateCheck(update=update, is_new=True)

    update = SCRIPTED_RELEASES[-1]
    LOGGER.info("watch update version=%s new=false", update.version)
    return UpdateCheck(update=update, is_new=False)


@activity.defn(name=CLASSIFY_UPDATE_ACTIVITY)
def classify_update(update: ReleaseNote) -> MockClassification:
    relevant = "Retry defaults changed" in update.notes
    reason = (
        "Changes background-job retry behavior."
        if relevant
        else "Release notes do not affect the fictional application."
    )
    LOGGER.info("watch classification version=%s report=%s", update.version, relevant)
    return MockClassification(warrants_report=relevant, reason=reason)


@activity.defn(name=GENERATE_REPORT_ACTIVITY)
def generate_mock_report(update: ReleaseNote) -> str:
    report = (
        f"{update.version}: outbound HTTP retries changed. "
        "Review background-job retry limits and duplicate-request handling."
    )
    LOGGER.info("watch report version=%s %s", update.version, report)
    return report


class RecurringAIWatchWorkflow:
    def __init__(self) -> None:
        self._check_count = 0
        self._next_update_index = 0
        self._latest_update: ReleaseNote | None = None
        self._latest_report: str | None = None
        self._pending_update: ReleaseNote | None = None
        self._pending_stage: str | None = None
        self._state = "STARTING"
        self._check_requested = False
        self._stop_requested = False
        self._mode = "mock"
        self._model_name: str | None = None

    @workflow.run
    async def run(self, watch_input: WatchInput) -> WatchStatus:
        if watch_input.interval <= timedelta(0):
            raise ValueError("interval must be greater than zero")
        if watch_input.max_checks is not None and watch_input.max_checks <= 0:
            raise ValueError("max_checks must be greater than zero when provided")
        if watch_input.mode not in {"mock", "live"}:
            raise ValueError("mode must be 'mock' or 'live'")
        if watch_input.mode == "live" and not watch_input.model_name:
            raise ValueError("live mode requires a model name")

        self._next_update_index = watch_input.next_update_index
        self._latest_update = watch_input.latest_update
        self._latest_report = watch_input.latest_report
        self._check_count = watch_input.cumulative_check_count
        self._pending_update = watch_input.pending_update
        self._pending_stage = watch_input.pending_stage
        self._mode = watch_input.mode
        self._model_name = watch_input.model_name
        checks_this_run = 0
        wait_before_next_check = watch_input.wait_before_next_check

        while True:
            if wait_before_next_check:
                await self._wait_for_next_cycle(watch_input.interval)
                stopped = self._complete_if_stopped()
                if stopped is not None:
                    return stopped

            await self._perform_check()
            checks_this_run += 1

            stopped = self._complete_if_stopped()
            if stopped is not None:
                return stopped
            if (
                watch_input.max_checks is not None
                and self._check_count >= watch_input.max_checks
            ):
                self._state = "COMPLETED"
                return self.status()
            if checks_this_run >= CHECKS_PER_RUN:
                self._state = "CONTINUING_AS_NEW"
                workflow.continue_as_new(
                    self._continuation_input(watch_input),
                )

            wait_before_next_check = True

    async def _wait_for_next_cycle(self, interval: timedelta) -> None:
        self._state = "WAITING"
        if self._check_requested:
            self._check_requested = False
            return

        timer = asyncio.create_task(workflow.sleep(interval))
        await workflow.wait_condition(
            lambda: timer.done() or self._check_requested or self._stop_requested
        )
        if not timer.done():
            timer.cancel()
        self._check_requested = False

    async def _perform_check(self) -> None:
        self._state = "CHECKING"
        self._check_count += 1
        if self._pending_update is None:
            checked = await workflow.execute_activity(
                GET_UPDATE_ACTIVITY,
                UpdateCheck,
                self._next_update_index,
                **ACTIVITY_OPTIONS,
            )
            self._latest_update = checked.update
            if not checked.is_new:
                return
            self._pending_update = checked.update
            self._pending_stage = "CLASSIFICATION"

        if self._pending_stage == "CLASSIFICATION":
            try:
                decision = await workflow.execute_activity(
                    CLASSIFY_UPDATE_ACTIVITY,
                    MockClassification,
                    self._pending_update,
                    **PROCESSING_ACTIVITY_OPTIONS,
                )
            except Exception as error:
                if _is_non_retryable_activity_failure(error):
                    raise
                return
            if not decision.warrants_report:
                self._finish_pending_update()
                return
            self._pending_stage = "REPORT"

        if self._pending_stage == "REPORT":
            try:
                if self._mode == "live":
                    from live import generate_live_report

                    self._latest_report = await generate_live_report(
                        self._pending_update,
                        self._model_name,
                    )
                else:
                    self._latest_report = await workflow.execute_activity(
                        GENERATE_REPORT_ACTIVITY,
                        str,
                        self._pending_update,
                        **PROCESSING_ACTIVITY_OPTIONS,
                    )
            except Exception as error:
                if _is_non_retryable_activity_failure(error):
                    raise
                return
            self._finish_pending_update()

    def _finish_pending_update(self) -> None:
        self._next_update_index += 1
        self._pending_update = None
        self._pending_stage = None

    def _complete_if_stopped(self) -> WatchStatus | None:
        if not self._stop_requested:
            return None
        self._state = "STOPPED"
        return self.status()

    def _continuation_input(self, watch_input: WatchInput) -> WatchInput:
        return WatchInput(
            interval=watch_input.interval,
            max_checks=watch_input.max_checks,
            next_update_index=self._next_update_index,
            latest_update=self._latest_update,
            latest_report=self._latest_report,
            cumulative_check_count=self._check_count,
            wait_before_next_check=True,
            pending_update=self._pending_update,
            pending_stage=self._pending_stage,
            mode=self._mode,
            model_name=self._model_name,
        )

    @workflow.signal(name=CHECK_NOW_SIGNAL)
    def check_now(self) -> None:
        self._check_requested = True

    @workflow.signal(name=STOP_WATCH_SIGNAL)
    def stop_watch(self) -> None:
        self._stop_requested = True

    @workflow.query(name=WATCH_STATUS_QUERY)
    def status(self) -> WatchStatus:
        return WatchStatus(
            check_count=self._check_count,
            latest_update=self._latest_update,
            latest_report=self._latest_report,
            state=self._state,
        )


def _is_non_retryable_activity_failure(error: Exception) -> bool:
    reason = str(error) if isinstance(error, ActivityFailure) else type(error).__name__
    return reason in {
        "LiveAuthenticationError",
        "LiveConfigurationError",
        "LiveSchemaError",
    }


def build_registry(
    *,
    mode: str = "mock",
    live_classifier: object | None = None,
    live_model_activities: object | None = None,
) -> Registry:
    """Return registrations for the selected mock or live worker."""

    registry = Registry()
    registry.workflow(RecurringAIWatchWorkflow)
    registry.register_activity(get_scripted_update)
    if mode == "mock":
        registry.register_activity(classify_update)
        registry.register_activity(generate_mock_report)
    elif mode == "live" and live_classifier and live_model_activities:
        registry.register_activity(live_classifier.classify_update)
        registry.register_activities(live_model_activities)
    else:
        raise ValueError("live registry requires classifier and model Activities")
    return registry
