"""Deterministic research tools, mock roles, progress, and publication Activities."""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

from cadence import activity


MOCK_PLAN_ACTIVITY = "auto-research.mock-plan"
MOCK_SPECIALIST_ACTIVITY = "auto-research.mock-specialist"
MOCK_SYNTHESIS_ACTIVITY = "auto-research.mock-synthesis"
MOCK_EDIT_ACTIVITY = "auto-research.mock-edit"
SEARCH_ACTIVITY = "auto-research.search-sources"
READ_ACTIVITY = "auto-research.read-source"
PROGRESS_ACTIVITY = "auto-research.log-progress"
PUBLISH_ACTIVITY = "auto-research.publish-report"
LIVE_AGENT_ACTIVITY = "auto-research.openai-agents-agent"

MAX_TOPIC_LENGTH = 500
MAX_QUESTIONS = 4
MAX_FEEDBACK_LENGTH = 1000
MAX_EXPANSION_TOPIC_LENGTH = 300
MAX_SOURCE_BRIEF_LENGTH = 2000
MAX_SUMMARY_LENGTH = 5000
MAX_REPORT_LENGTH = 20_000


@dataclass(frozen=True)
class ResearchPlan:
    questions: tuple[str, ...]


@dataclass(frozen=True)
class SourceResults:
    urls: tuple[str, ...]


@dataclass(frozen=True)
class AgentRequest:
    role: str
    prompt: str


@dataclass(frozen=True)
class PublicationRequest:
    publication_id: str
    topic: str
    revision: int
    report: str


@dataclass(frozen=True)
class PublicationReceipt:
    publication_id: str
    filename: str
    revision: int
    content_hash: str


def _clean(value: str, *, name: str, maximum: int) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"{name} must not be empty")
    if len(cleaned) > maximum:
        raise ValueError(f"{name} must not exceed {maximum} characters")
    return cleaned


@activity.defn(name=MOCK_PLAN_ACTIVITY)
def mock_plan(topic: str) -> ResearchPlan:
    """Return four deterministic questions without reading catalogs or providers."""
    topic = _clean(topic, name="topic", maximum=MAX_TOPIC_LENGTH)
    return ResearchPlan(
        (
            f"What established evidence describes {topic}?",
            f"What practical applications are associated with {topic}?",
            f"What limitations and uncertainties affect {topic}?",
            f"What future developments are plausible for {topic}?",
        )
    )


@activity.defn(name=SEARCH_ACTIVITY)
def search_research_sources(question: str) -> SourceResults:
    """Return bounded synthetic source URLs; this Activity performs no network I/O."""
    question = _clean(question, name="question", maximum=MAX_TOPIC_LENGTH)
    encoded = quote(question, safe="")
    return SourceResults(
        (
            f"https://research.example/overview?topic={encoded}",
            f"https://research.example/challenges?topic={encoded}",
            f"https://research.example/outlook?topic={encoded}",
        )
    )


@activity.defn(name=READ_ACTIVITY)
def read_research_source(url: str) -> str:
    """Return a synthetic source brief for one recipe-generated URL."""
    parsed = urlparse(_clean(url, name="source URL", maximum=2000))
    if parsed.scheme != "https" or parsed.hostname != "research.example":
        raise ValueError("source URL must use the synthetic research.example host")
    topic = unquote(parse_qs(parsed.query).get("topic", [""])[0])
    topic = _clean(topic, name="source topic", maximum=MAX_TOPIC_LENGTH)
    angle = parsed.path.rsplit("/", 1)[-1].replace("-", " ")
    brief = (
        f"Synthetic source brief for {topic} ({angle}). "
        "This simulated source distinguishes established evidence, practical "
        "implications, limitations, uncertainty, and possible future work."
    )
    return brief[:MAX_SOURCE_BRIEF_LENGTH]


@activity.defn(name=MOCK_SPECIALIST_ACTIVITY)
def mock_specialist(question: str, source_briefs: tuple[str, ...]) -> str:
    """Create a deterministic specialist summary from simulated source briefs."""
    question = _clean(question, name="question", maximum=MAX_TOPIC_LENGTH)
    if len(source_briefs) != 2:
        raise ValueError("specialist requires exactly two source briefs")
    cleaned = [
        _clean(brief, name="source brief", maximum=MAX_SOURCE_BRIEF_LENGTH)
        for brief in source_briefs
    ]
    return (
        f"### {question}\n\n"
        f"Established evidence: {cleaned[0]}\n\n"
        f"Limitations and outlook: {cleaned[1]}\n\n"
        "_Sources are simulated recipe fixtures, not factual web research._"
    )[:MAX_SUMMARY_LENGTH]


@activity.defn(name=MOCK_SYNTHESIS_ACTIVITY)
def mock_synthesis(topic: str, summaries: tuple[str, ...]) -> str:
    """Build a deterministic bounded Markdown draft."""
    topic = _clean(topic, name="topic", maximum=MAX_TOPIC_LENGTH)
    if not summaries or len(summaries) > MAX_QUESTIONS:
        raise ValueError(f"synthesis requires 1 to {MAX_QUESTIONS} summaries")
    threads = "\n\n".join(
        f"## Research thread {index}\n\n"
        f"{_clean(summary, name='summary', maximum=MAX_SUMMARY_LENGTH)}"
        for index, summary in enumerate(summaries, start=1)
    )
    report = (
        f"# Research briefing: {topic}\n\n"
        "## Executive summary\n\n"
        "This deterministic briefing organizes simulated source material for "
        "human review. It is not a factual web-research product.\n\n"
        f"{threads}\n\n"
        "## Cross-cutting challenges\n\n"
        "The source briefs are synthetic, so claims require independent verification.\n\n"
        "## Outlook\n\n"
        "Future work should validate the briefing against authoritative sources."
    )
    return _clean(report, name="report", maximum=MAX_REPORT_LENGTH)


@activity.defn(name=MOCK_EDIT_ACTIVITY)
def mock_edit(
    current_report: str,
    edit_kind: str,
    detail: str,
    new_research: str,
) -> str:
    """Apply a deterministic revision or expansion to the complete report."""
    report = _clean(
        current_report, name="current report", maximum=MAX_REPORT_LENGTH
    )
    if edit_kind == "revise":
        detail = _clean(
            detail, name="feedback", maximum=MAX_FEEDBACK_LENGTH
        )
        addition = (
            "\n\n## Human-requested revision\n\n"
            f"Applied feedback: {detail}"
        )
    elif edit_kind == "expand":
        detail = _clean(
            detail, name="expansion topic", maximum=MAX_EXPANSION_TOPIC_LENGTH
        )
        research = _clean(
            new_research, name="new research", maximum=MAX_SUMMARY_LENGTH
        )
        addition = (
            f"\n\n## Expanded research: {detail}\n\n{research}"
        )
    else:
        raise ValueError("edit kind must be revise or expand")
    return _clean(
        report + addition, name="updated report", maximum=MAX_REPORT_LENGTH
    )


@activity.defn(name=PROGRESS_ACTIVITY)
def log_progress(message: str) -> str:
    """Emit replay-safe progress from an Activity."""
    message = _clean(message, name="progress message", maximum=1000)
    print(message, flush=True)
    return message


def _filename(publication_id: str, topic: str) -> str:
    if not re.fullmatch(r"[a-f0-9]{24}", publication_id):
        raise ValueError("publication ID must be a 24-character lowercase hex value")
    slug = re.sub(r"[^a-z0-9]+", "-", topic.lower()).strip("-")[:60]
    return f"{slug or 'research-report'}-{publication_id}.md"


@activity.defn(name=PUBLISH_ACTIVITY)
def publish_report(request: PublicationRequest) -> PublicationReceipt:
    """Atomically publish one stable local file and return a portable receipt."""
    report = _clean(
        request.report, name="report", maximum=MAX_REPORT_LENGTH
    )
    topic = _clean(request.topic, name="topic", maximum=MAX_TOPIC_LENGTH)
    if request.revision < 0:
        raise ValueError("revision must not be negative")
    contents = report + "\n"
    digest = hashlib.sha256(contents.encode("utf-8")).hexdigest()
    filename = _filename(request.publication_id, topic)
    output_dir = Path(
        os.environ.get("AUTO_RESEARCH_OUTPUT_DIR", "research_reports")
    ).expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)
    destination = output_dir / filename

    if destination.exists() and destination.read_text(encoding="utf-8") == contents:
        return PublicationReceipt(
            request.publication_id, filename, request.revision, digest
        )

    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=output_dir,
            prefix=f".{filename}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            handle.write(contents)
            handle.flush()
            os.fsync(handle.fileno())
            temporary = Path(handle.name)
        os.replace(temporary, destination)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()

    return PublicationReceipt(
        request.publication_id, filename, request.revision, digest
    )
