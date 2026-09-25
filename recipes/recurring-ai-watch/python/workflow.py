"""Durable recurring watch: release note -> Jev -> optional Gemini report."""

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
CLASSIFY_ACTIVITY = "recurring-watch.classify-update"
MOCK_REPORT_ACTIVITY = "recurring-watch.generate-mock-report"
CHECKS_PER_RUN = 20

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
        "maximum_interval": timedelta(seconds=4),
        "maximum_attempts": 3,
        "non_retryable_error_reasons": sorted(FATAL_AI_ERRORS),
    },
}
LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class ReleaseNote:
    version: str
    notes: str


@dataclass(frozen=True)
class MockClassification:
    warrants_report: bool
    reason: str


@dataclass(frozen=True)
class WatchInput:
    interval: timedelta = timedelta(seconds=15)
    mode: str = "mock"
    model_name: str | None = None
    next_update_index: int = 0
    latest_update: ReleaseNote | None = None
    latest_report: str | None = None
    check_count: int = 0
    wait_before_first_check: bool = False
    max_checks: int | None = None  # Test-only finite run support.


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


@activity.defn(name=CLASSIFY_ACTIVITY)
def mock_classify(update: ReleaseNote) -> MockClassification:
    relevant = "Retry defaults changed" in update.notes
    LOGGER.info("watch classification version=%s report=%s", update.version, relevant)
    return MockClassification(
        relevant,
        "Changes background-job retry behavior."
        if relevant
        else "No background-job impact.",
    )


@activity.defn(name=MOCK_REPORT_ACTIVITY)
def mock_report(update: ReleaseNote) -> str:
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
        self._state = "STARTING"
        self._check_requested = False
        self._stop_requested = False
        self._mode = "mock"
        self._model_name: str | None = None

    @workflow.run
    async def run(self, watch_input: WatchInput) -> WatchStatus:
        self._validate(watch_input)
        self._check_count = watch_input.check_count
        self._next_update_index = watch_input.next_update_index
        self._latest_update = watch_input.latest_update
        self._latest_report = watch_input.latest_report
        self._mode = watch_input.mode
        self._model_name = watch_input.model_name
        wait_before_check = watch_input.wait_before_first_check
        checks_this_run = 0

        while True:
            if wait_before_check:
                await self._wait(watch_input.interval)
                if self._stop_requested:
                    return self._finish("STOPPED")

            await self._check()
            checks_this_run += 1

            if self._stop_requested:
                return self._finish("STOPPED")
            if watch_input.max_checks and self._check_count >= watch_input.max_checks:
                return self._finish("COMPLETED")
            if checks_this_run == CHECKS_PER_RUN:
                self._state = "CONTINUING_AS_NEW"
                workflow.continue_as_new(self._next_run(watch_input))
            wait_before_check = True

    def _validate(self, watch_input: WatchInput) -> None:
        if watch_input.interval <= timedelta(0):
            raise ValueError("interval must be greater than zero")
        if watch_input.max_checks is not None and watch_input.max_checks <= 0:
            raise ValueError("max_checks must be greater than zero")
        if watch_input.mode not in {"mock", "live"}:
            raise ValueError("mode must be 'mock' or 'live'")
        if watch_input.mode == "live" and not watch_input.model_name:
            raise ValueError("live mode requires a model name")

    async def _check(self) -> None:
        self._state = "CHECKING"
        self._check_count += 1
        if self._next_update_index >= len(SCRIPTED_RELEASES):
            self._latest_update = SCRIPTED_RELEASES[-1]
            return

        update = SCRIPTED_RELEASES[self._next_update_index]
        self._latest_update = update
        try:
            decision = await workflow.execute_activity(
                CLASSIFY_ACTIVITY,
                MockClassification,
                update,
                **AI_ACTIVITY_OPTIONS,
            )
            if decision.warrants_report:
                self._latest_report = await self._report(update)
        except Exception as error:
            if _fatal(error):
                raise
            return  # Transient retries exhausted; try this release next cycle.

        self._next_update_index += 1

    async def _report(self, update: ReleaseNote) -> str:
        if self._mode == "live":
            from live import generate_live_report

            return await generate_live_report(update, self._model_name)
        return await workflow.execute_activity(
            MOCK_REPORT_ACTIVITY,
            str,
            update,
            **AI_ACTIVITY_OPTIONS,
        )

    async def _wait(self, interval: timedelta) -> None:
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

    def _next_run(self, watch_input: WatchInput) -> WatchInput:
        return WatchInput(
            interval=watch_input.interval,
            mode=self._mode,
            model_name=self._model_name,
            next_update_index=self._next_update_index,
            latest_update=self._latest_update,
            latest_report=self._latest_report,
            check_count=self._check_count,
            wait_before_first_check=True,
            max_checks=watch_input.max_checks,
        )

    def _finish(self, state: str) -> WatchStatus:
        self._state = state
        return self.status()

    @workflow.signal(name=CHECK_NOW_SIGNAL)
    def check_now(self) -> None:
        self._check_requested = True

    @workflow.signal(name=STOP_WATCH_SIGNAL)
    def stop_watch(self) -> None:
        self._stop_requested = True

    @workflow.query(name=WATCH_STATUS_QUERY)
    def status(self) -> WatchStatus:
        return WatchStatus(
            self._check_count,
            self._latest_update,
            self._latest_report,
            self._state,
        )


def _fatal(error: Exception) -> bool:
    reason = str(error) if isinstance(error, ActivityFailure) else type(error).__name__
    return reason in FATAL_AI_ERRORS


def build_registry(classifier=mock_classify, model_activities=None) -> Registry:
    registry = Registry()
    registry.workflow(RecurringAIWatchWorkflow)
    registry.register_activity(classifier)
    if model_activities is None:
        registry.register_activity(mock_report)
    else:
        registry.register_activities(model_activities)
    return registry
