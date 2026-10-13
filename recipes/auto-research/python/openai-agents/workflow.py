"""Durable bounded research, review, and publication orchestration."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import timedelta
from textwrap import dedent

from cadence import workflow
from cadence.worker import Registry

from activities import (
    LIVE_AGENT_ACTIVITY,
    MAX_EXPANSION_TOPIC_LENGTH,
    MAX_FEEDBACK_LENGTH,
    MAX_QUESTIONS,
    MAX_REPORT_LENGTH,
    MAX_SUMMARY_LENGTH,
    MAX_TOPIC_LENGTH,
    MOCK_EDIT_ACTIVITY,
    MOCK_PLAN_ACTIVITY,
    MOCK_SPECIALIST_ACTIVITY,
    MOCK_SYNTHESIS_ACTIVITY,
    PROGRESS_ACTIVITY,
    PUBLISH_ACTIVITY,
    READ_ACTIVITY,
    SEARCH_ACTIVITY,
    AgentRequest,
    PublicationReceipt,
    PublicationRequest,
    ResearchPlan,
    SourceResults,
    log_progress,
    mock_edit,
    mock_plan,
    mock_specialist,
    mock_synthesis,
    publish_report,
    read_research_source,
    search_research_sources,
)


WORKFLOW = "AutoResearchWorkflow"
STATUS_QUERY = "status"
REPORT_QUERY = "get_report_draft"
APPROVE_SIGNAL = "approve_report"
REVISE_SIGNAL = "revise_report"
EXPAND_SIGNAL = "expand_report"
MAX_EDIT_ROUNDS = 3
MAX_QUERY_DRAFT_LENGTH = MAX_REPORT_LENGTH

FATAL_AI_ERRORS = {
    "LiveAuthenticationError",
    "LiveConfigurationError",
    "LiveSchemaError",
}
AI_ACTIVITY_OPTIONS = {
    "schedule_to_close_timeout": timedelta(minutes=3),
    "retry_policy": {
        "initial_interval": timedelta(seconds=1),
        "backoff_coefficient": 2.0,
        "maximum_attempts": 3,
        "non_retryable_error_reasons": sorted(FATAL_AI_ERRORS),
    },
}
TOOL_ACTIVITY_OPTIONS = {
    "schedule_to_close_timeout": timedelta(seconds=30),
    "retry_policy": {
        "initial_interval": timedelta(seconds=1),
        "backoff_coefficient": 2.0,
        "maximum_attempts": 3,
    },
}


@dataclass(frozen=True)
class RecipeInput:
    text: str
    mode: str = "mock"


@dataclass(frozen=True)
class ReviewAction:
    kind: str
    detail: str = ""


@dataclass(frozen=True)
class WorkflowStatus:
    state: str
    mode: str
    topic: str
    revision: int
    edit_rounds: int
    question_count: int
    draft: str
    draft_truncated: bool
    publication: PublicationReceipt | None


@dataclass(frozen=True)
class RecipeResult:
    outcome: str
    mode: str
    topic: str
    revision: int
    report: str
    publication: PublicationReceipt


@dataclass(frozen=True)
class MarkdownQueryResponse:
    cadenceResponseType: str
    format: str
    data: str


def _bounded(value: str, *, name: str, maximum: int) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"{name} must not be empty")
    if len(cleaned) > maximum:
        raise ValueError(f"{name} must not exceed {maximum} characters")
    return cleaned


def _parse_questions(value: str) -> ResearchPlan:
    questions: list[str] = []
    for line in value.splitlines():
        match = re.match(r"^\s*(?:\d+[.)]|[-*])\s*(\S.*)$", line)
        if match:
            question = match.group(1).strip()
            if question and question not in questions:
                questions.append(question[:MAX_TOPIC_LENGTH])
        if len(questions) == MAX_QUESTIONS:
            break
    if not questions:
        raise ValueError("planner returned no bounded research questions")
    return ResearchPlan(tuple(questions))


class AutoResearchWorkflow:
    def __init__(self) -> None:
        self._state = "STARTING"
        self._mode = "mock"
        self._topic = ""
        self._revision = 0
        self._edit_rounds = 0
        self._question_count = 0
        self._draft = ""
        self._review_action: ReviewAction | None = None
        self._publication: PublicationReceipt | None = None

    @workflow.query(name=STATUS_QUERY)
    def status(self) -> WorkflowStatus:
        draft = self._draft[:MAX_QUERY_DRAFT_LENGTH]
        return WorkflowStatus(
            state=self._state,
            mode=self._mode,
            topic=self._topic,
            revision=self._revision,
            edit_rounds=self._edit_rounds,
            question_count=self._question_count,
            draft=draft,
            draft_truncated=len(self._draft) > len(draft),
            publication=self._publication,
        )

    @workflow.query(name=REPORT_QUERY)
    def report(self) -> MarkdownQueryResponse:
        info = workflow.WorkflowContext.get().info()
        return MarkdownQueryResponse(
            cadenceResponseType="formattedData",
            format="text/markdown",
            data=_render_report(
                self.status(),
                domain=info.workflow_domain,
                workflow_id=info.workflow_id,
                run_id=info.workflow_run_id,
            ),
        )

    @workflow.signal(name=APPROVE_SIGNAL)
    def approve(self, expected_revision: int) -> None:
        self._record_action(expected_revision, ReviewAction("approve"))

    @workflow.signal(name=REVISE_SIGNAL)
    def revise(self, expected_revision: int, feedback: str) -> None:
        if not isinstance(feedback, str):
            return
        detail = feedback.strip()
        if not detail or len(detail) > MAX_FEEDBACK_LENGTH:
            return
        self._record_action(
            expected_revision, ReviewAction("revise", detail)
        )

    @workflow.signal(name=EXPAND_SIGNAL)
    def expand(self, expected_revision: int, topic: str) -> None:
        if not isinstance(topic, str):
            return
        detail = topic.strip()
        if not detail or len(detail) > MAX_EXPANSION_TOPIC_LENGTH:
            return
        self._record_action(
            expected_revision, ReviewAction("expand", detail)
        )

    def _record_action(
        self, expected_revision: int, action: ReviewAction
    ) -> None:
        if (
            self._state != "WAITING_FOR_REVIEW"
            or type(expected_revision) is not int
            or expected_revision != self._revision
            or self._review_action is not None
            or (
                action.kind in {"revise", "expand"}
                and self._edit_rounds >= MAX_EDIT_ROUNDS
            )
        ):
            return
        self._review_action = action

    async def _progress(self, message: str) -> None:
        await workflow.execute_activity(
            PROGRESS_ACTIVITY, str, message, **TOOL_ACTIVITY_OPTIONS
        )

    async def _agent(self, role: str, prompt: str) -> str:
        result = await workflow.execute_activity(
            LIVE_AGENT_ACTIVITY,
            str,
            AgentRequest(role, prompt),
            **AI_ACTIVITY_OPTIONS,
        )
        return _bounded(result, name=f"{role} result", maximum=MAX_REPORT_LENGTH)

    async def _plan(self) -> ResearchPlan:
        if self._mode == "mock":
            return await workflow.execute_activity(
                MOCK_PLAN_ACTIVITY,
                ResearchPlan,
                self._topic,
                **AI_ACTIVITY_OPTIONS,
            )
        output = await self._agent(
            "planner",
            "Return 1 to 4 focused research questions as a numbered list. "
            "Output only the list.\n\n"
            f"Research topic: {self._topic}",
        )
        return _parse_questions(output)

    async def _research(self, question: str) -> str:
        results = await workflow.execute_activity(
            SEARCH_ACTIVITY,
            SourceResults,
            question,
            **TOOL_ACTIVITY_OPTIONS,
        )
        if len(results.urls) < 2:
            raise ValueError("simulated search returned fewer than two sources")
        briefs = tuple(
            [
                await workflow.execute_activity(
                    READ_ACTIVITY,
                    str,
                    url,
                    **TOOL_ACTIVITY_OPTIONS,
                )
                for url in results.urls[:2]
            ]
        )
        if self._mode == "mock":
            return await workflow.execute_activity(
                MOCK_SPECIALIST_ACTIVITY,
                str,
                question,
                briefs,
                **AI_ACTIVITY_OPTIONS,
            )
        return await self._agent(
            "specialist",
            "Write a concise Markdown research summary for the exact question. "
            "Use both simulated source briefs, label uncertainty, and state that "
            "the sources are simulated.\n\n"
            f"Question: {question}\n\n"
            f"Source brief 1: {briefs[0]}\n\nSource brief 2: {briefs[1]}",
        )

    async def _synthesize(self, summaries: tuple[str, ...]) -> str:
        if self._mode == "mock":
            return await workflow.execute_activity(
                MOCK_SYNTHESIS_ACTIVITY,
                str,
                self._topic,
                summaries,
                **AI_ACTIVITY_OPTIONS,
            )
        joined = "\n\n---\n\n".join(summaries)
        return await self._agent(
            "synthesizer",
            "Write a complete bounded Markdown report with a title, Executive "
            "summary, one Key findings subsection per supplied thread, "
            "Cross-cutting challenges, and Outlook. Preserve the simulated-source "
            "disclaimer.\n\n"
            f"Topic: {self._topic}\n\nResearch summaries:\n{joined}",
        )

    async def _edit(
        self, action: ReviewAction, new_research: str = ""
    ) -> str:
        if self._mode == "mock":
            return await workflow.execute_activity(
                MOCK_EDIT_ACTIVITY,
                str,
                self._draft,
                action.kind,
                action.detail,
                new_research,
                **AI_ACTIVITY_OPTIONS,
            )
        prompt = (
            "Return the complete updated Markdown report only. Preserve existing "
            "material and the simulated-source disclaimer.\n\n"
            f"Edit kind: {action.kind}\nDetail: {action.detail}\n\n"
            f"Current report:\n{self._draft}"
        )
        if new_research:
            prompt += f"\n\nNew research to incorporate:\n{new_research}"
        return await self._agent("editor", prompt)

    @workflow.run
    async def run(self, recipe_input: RecipeInput) -> RecipeResult:
        if recipe_input.mode not in {"mock", "live"}:
            raise ValueError("mode must be 'mock' or 'live'")
        self._topic = _bounded(
            recipe_input.text, name="topic", maximum=MAX_TOPIC_LENGTH
        )
        self._mode = recipe_input.mode
        self._state = "PLANNING"
        await self._progress(f"Planning research for: {self._topic}")
        plan = await self._plan()
        if not plan.questions or len(plan.questions) > MAX_QUESTIONS:
            raise ValueError(f"planner must return 1 to {MAX_QUESTIONS} questions")
        self._question_count = len(plan.questions)

        summaries: list[str] = []
        for index, question in enumerate(plan.questions, start=1):
            self._state = "RESEARCHING"
            await self._progress(
                f"Researching question {index} of {len(plan.questions)}"
            )
            summary = await self._research(question)
            summaries.append(
                _bounded(
                    summary,
                    name="specialist summary",
                    maximum=MAX_SUMMARY_LENGTH,
                )
            )

        self._state = "SYNTHESIZING"
        await self._progress(
            f"Synthesizing {len(summaries)} research threads"
        )
        self._draft = await self._synthesize(tuple(summaries))

        while True:
            self._state = "WAITING_FOR_REVIEW"
            self._review_action = None
            await self._progress(
                f"Revision {self._revision} is ready for human review"
            )
            await workflow.wait_condition(
                lambda: self._review_action is not None
            )
            action = self._review_action
            if action is None:
                raise RuntimeError("review wait completed without an action")
            if action.kind == "approve":
                break

            self._state = (
                "REVISING" if action.kind == "revise" else "EXPANDING"
            )
            new_research = ""
            if action.kind == "expand":
                await self._progress(
                    f"Researching expansion for revision {self._revision}"
                )
                new_research = await self._research(action.detail)
                new_research = _bounded(
                    new_research,
                    name="expansion summary",
                    maximum=MAX_SUMMARY_LENGTH,
                )
            updated = await self._edit(action, new_research)
            self._draft = _bounded(
                updated, name="updated report", maximum=MAX_REPORT_LENGTH
            )
            self._edit_rounds += 1
            self._revision += 1

        self._state = "PUBLISHING"
        info = workflow.WorkflowContext.get().info()
        publication_id = hashlib.sha256(
            (
                f"{info.workflow_id}:{info.workflow_run_id}:"
                f"{self._revision}"
            ).encode("utf-8")
        ).hexdigest()[:24]
        self._publication = await workflow.execute_activity(
            PUBLISH_ACTIVITY,
            PublicationReceipt,
            PublicationRequest(
                publication_id=publication_id,
                topic=self._topic,
                revision=self._revision,
                report=self._draft,
            ),
            **TOOL_ACTIVITY_OPTIONS,
        )
        self._state = "COMPLETED"
        await self._progress(
            f"Published report {self._publication.publication_id}"
        )
        return RecipeResult(
            outcome="APPROVED_PUBLISHED",
            mode=self._mode,
            topic=self._topic,
            revision=self._revision,
            report=self._draft,
            publication=self._publication,
        )


def _render_report(
    status: WorkflowStatus,
    *,
    domain: str,
    workflow_id: str,
    run_id: str,
) -> str:
    if not status.draft:
        return (
            "# Auto Research\n\n"
            f"**State:** {status.state}\n\n"
            "The first report draft is not ready."
        )
    return dedent(
        f"""\
        # Auto Research review

        **State:** {status.state}  
        **Revision:** {status.revision}  
        **Accepted edit rounds:** {status.edit_rounds} of {MAX_EDIT_ROUNDS}  
        **Workflow ID:** `{workflow_id}`  
        **Run ID:** `{run_id}`  
        **Domain:** `{domain}`

        Use the recipe CLI or Local Samples Explorer controls with exactly this
        revision and Run.

        ---

        {status.draft}
        """
    )


def build_registry(model_activities: object | None = None) -> Registry:
    registry = Registry()
    registry.workflow(AutoResearchWorkflow)
    for fn in (
        mock_plan,
        mock_specialist,
        mock_synthesis,
        mock_edit,
        search_research_sources,
        read_research_source,
        log_progress,
        publish_report,
    ):
        registry.register_activity(fn)
    if model_activities is not None:
        registry.register_activities(model_activities)
    return registry
