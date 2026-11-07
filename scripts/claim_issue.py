#!/usr/bin/env python3
"""Deterministically process exact `/claim` Issue-comment requests."""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


LIFECYCLE_LABELS = ("status:needs-triage", "status:ready", "status:in-progress")
RESPONSE_PREFIX = "<!-- issue-claim-response:"
RESPONSE_SUFFIX = " -->"


class ClaimError(ValueError):
    """Raised when authoritative GitHub data cannot be safely interpreted."""


class ApiError(ClaimError):
    """Raised when a GitHub API operation fails."""


@dataclass(frozen=True)
class ClaimComment:
    identifier: int
    created_at: datetime
    login: str
    body: str


@dataclass(frozen=True)
class ClaimPlan:
    winner: ClaimComment | None
    label_names: tuple[str, ...] | None
    feedback: tuple[ClaimComment, ...]
    manual_reason: str | None = None


def _mapping(value: object, description: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ClaimError(f"{description} must be an object")
    return value


def _string(value: object, description: str) -> str:
    if not isinstance(value, str) or not value:
        raise ClaimError(f"{description} must be a non-empty string")
    return value


def _integer(value: object, description: str) -> int:
    if not isinstance(value, int):
        raise ClaimError(f"{description} must be an integer")
    return value


def _timestamp(value: object, description: str) -> datetime:
    text = _string(value, description)
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as error:
        raise ClaimError(f"{description} must be an ISO-8601 timestamp") from error


def is_claim_command(body: object) -> bool:
    return isinstance(body, str) and body.strip() == "/claim"


def response_marker(comment_id: int) -> str:
    return f"{RESPONSE_PREFIX}{comment_id}{RESPONSE_SUFFIX}"


def comment_permalink(owner: str, repo: str, issue_number: int, comment_id: int) -> str:
    return f"https://github.com/{owner}/{repo}/issues/{issue_number}#issuecomment-{comment_id}"


def existing_response_ids(comments: Iterable[object]) -> set[int]:
    identifiers: set[int] = set()
    for item in comments:
        record = _mapping(item, "Issue comment")
        user = _mapping(record.get("user"), "Issue comment user")
        if user.get("login") != "github-actions[bot]":
            continue
        body = record.get("body")
        if not isinstance(body, str):
            raise ClaimError("Issue comment body must be a string")
        start = 0
        while True:
            start = body.find(RESPONSE_PREFIX, start)
            if start < 0:
                break
            end = body.find(RESPONSE_SUFFIX, start)
            if end < 0:
                raise ClaimError("Issue comment contains a malformed claim response marker")
            identifier = body[start + len(RESPONSE_PREFIX) : end]
            if not identifier.isdigit():
                raise ClaimError("Issue comment contains a malformed claim response marker")
            identifiers.add(int(identifier))
            start = end + len(RESPONSE_SUFFIX)
    return identifiers


def claim_comments(comments: Iterable[object]) -> list[ClaimComment]:
    parsed: list[ClaimComment] = []
    for item in comments:
        record = _mapping(item, "Issue comment")
        body = record.get("body")
        user = _mapping(record.get("user"), "Issue comment user")
        if user.get("type") != "User" or not is_claim_command(body):
            continue
        created_at = _timestamp(record.get("created_at"), "Issue comment created_at")
        updated_at = _timestamp(record.get("updated_at"), "Issue comment updated_at")
        if created_at != updated_at:
            continue
        parsed.append(
            ClaimComment(
                _integer(record.get("id"), "Issue comment id"),
                created_at,
                _string(user.get("login"), "Issue comment user login"),
                body.strip(),
            )
        )
    return parsed


def _label_names(issue: Mapping[str, object]) -> tuple[str, ...]:
    labels = issue.get("labels")
    if not isinstance(labels, list):
        raise ClaimError("Issue labels must be an array")
    names: list[str] = []
    for label in labels:
        names.append(_string(_mapping(label, "Issue label").get("name"), "Issue label name"))
    return tuple(names)


def _ready_window_start(timeline: Iterable[object]) -> datetime:
    return _latest_lifecycle_event(timeline, "status:ready", "claim window")


def _latest_lifecycle_event(
    timeline: Iterable[object], label_name: str, description: str
) -> datetime:
    latest: datetime | None = None
    for item in timeline:
        event = _mapping(item, "Timeline event")
        if event.get("event") != "labeled":
            continue
        label = _mapping(event.get("label"), "Timeline label")
        if label.get("name") == label_name:
            candidate = _timestamp(event.get("created_at"), "Timeline event created_at")
            if latest is None or candidate > latest:
                latest = candidate
    if latest is None:
        raise ClaimError(f"No {label_name} timeline event establishes the {description}")
    return latest


def _active_source_issue(event: Mapping[str, object], owner: str, repo: str) -> bool:
    if event.get("event") != "cross-referenced":
        return False
    source = _mapping(event.get("source"), "Timeline reference source")
    issue = _mapping(source.get("issue"), "Timeline reference source issue")
    source_repo = _mapping(issue.get("repository"), "Timeline reference source repository")
    if source_repo.get("full_name") != f"{owner}/{repo}":
        return False
    if issue.get("state") != "open":
        return False
    return isinstance(issue.get("pull_request"), Mapping)


def has_active_implementation_pr(timeline: Iterable[object], owner: str, repo: str) -> bool:
    for item in timeline:
        event = _mapping(item, "Timeline event")
        if event.get("event") == "connected":
            raise ClaimError("Opaque connected timeline event requires manual claim handling")
        if _active_source_issue(event, owner, repo):
            return True
    return False


def _manual_plan(
    candidates: Sequence[ClaimComment],
    responded: set[int],
    reason: str,
    *,
    winner_candidates: Sequence[ClaimComment] | None = None,
) -> ClaimPlan:
    selection_pool = candidates if winner_candidates is None else winner_candidates
    return ClaimPlan(
        winner=(
            min(selection_pool, key=lambda candidate: (candidate.created_at, candidate.identifier))
            if selection_pool
            else None
        ),
        label_names=None,
        feedback=tuple(candidate for candidate in candidates if candidate.identifier not in responded),
        manual_reason=reason,
    )


def plan_claim(
    issue: Mapping[str, object],
    comments: Sequence[object],
    timeline: Sequence[object],
    owner: str,
    repo: str,
) -> ClaimPlan:
    candidates = claim_comments(comments)
    responded = existing_response_ids(comments)
    try:
        window_start = _ready_window_start(timeline)
    except ClaimError as error:
        return _manual_plan(candidates, responded, str(error))
    candidates = [candidate for candidate in candidates if candidate.created_at >= window_start]
    if not candidates:
        return _manual_plan(candidates, responded, "No eligible /claim comment exists in this claim window.")
    if issue.get("state") != "open" or "pull_request" in issue:
        return _manual_plan(candidates, responded, "This command applies only to an open Issue.")
    try:
        active_implementation_pr = has_active_implementation_pr(timeline, owner, repo)
    except ClaimError as error:
        return _manual_plan(candidates, responded, str(error))
    if active_implementation_pr:
        return _manual_plan(candidates, responded, "An active linked implementation pull request requires manual coordination.")

    labels = _label_names(issue)
    lifecycle = tuple(name for name in labels if name in LIFECYCLE_LABELS)
    assignees = issue.get("assignees")
    if not isinstance(assignees, list):
        raise ClaimError("Issue assignees must be an array")
    if labels.count("status:in-progress") == 1 and len(lifecycle) == 1:
        try:
            transition = _latest_lifecycle_event(
                timeline, "status:in-progress", "established-ownership transition"
            )
        except ClaimError as error:
            return _manual_plan(candidates, responded, str(error))
        # GitHub timestamps have one-second precision, so a winning claim and its
        # transition may share a timestamp. Later-second claims are never owners.
        established = [candidate for candidate in candidates if candidate.created_at <= transition]
        if assignees:
            return _manual_plan(
                candidates,
                responded,
                "An assigned in-progress Issue requires manual ownership recovery.",
                winner_candidates=established,
            )
        if not established:
            return _manual_plan(
                candidates,
                responded,
                "No unedited /claim comment predates the in-progress transition.",
                winner_candidates=established,
            )
        winner = min(established, key=lambda candidate: (candidate.created_at, candidate.identifier))
        feedback = tuple(candidate for candidate in candidates if candidate.identifier not in responded)
        return ClaimPlan(winner=winner, label_names=None, feedback=feedback)
    if (
        labels.count("status:ready") != 1
        or len(lifecycle) != 1
        or assignees
    ):
        return _manual_plan(
            candidates,
            responded,
            "The Issue is no longer an unassigned, claimable status:ready Issue.",
        )
    winner = min(candidates, key=lambda candidate: (candidate.created_at, candidate.identifier))
    feedback = tuple(candidate for candidate in candidates if candidate.identifier not in responded)
    replacement = tuple(name for name in labels if name not in LIFECYCLE_LABELS) + (
        "status:in-progress",
    )
    return ClaimPlan(winner=winner, label_names=replacement, feedback=feedback)


def feedback_body(
    candidate: ClaimComment,
    winner: ClaimComment | None,
    manual_reason: str | None,
    owner: str,
    repo: str,
    issue_number: int,
) -> str:
    marker = response_marker(candidate.identifier)
    if manual_reason:
        selected = (
            f" The earliest eligible request is @{winner.login} in the "
            f"[winning /claim comment]({comment_permalink(owner, repo, issue_number, winner.identifier)})."
            if winner is not None
            else ""
        )
        return (
            f"{marker}\n"
            "This `/claim` request could not be processed automatically. "
            f"{manual_reason}{selected} Use the documented manual maintainer path."
        )
    if candidate == winner:
        return (
            f"{marker}\n"
            f"Claim confirmed for @{candidate.login} from the "
            f"[winning /claim comment]({comment_permalink(owner, repo, issue_number, winner.identifier)}). "
            "The Issue is `status:in-progress`; "
            "you may create the documented `issue-N-short-slug` branch."
        )
    assert winner is not None
    return (
        f"{marker}\n"
        f"This Issue was claimed by @{winner.login} in the "
        f"[winning /claim comment]({comment_permalink(owner, repo, issue_number, winner.identifier)}). "
        "Do not create a competing branch."
    )


class GitHubClient:
    def __init__(self, owner: str, repo: str, token: str, api_base: str = "https://api.github.com"):
        self.owner = owner
        self.repo = repo
        self.token = token
        self.api_base = api_base.rstrip("/")

    def _request(self, method: str, path: str, payload: object | None = None) -> object:
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self.token}",
            "User-Agent": "cadence-ai-samples-issue-claim",
        }
        if data is not None:
            headers["Content-Type"] = "application/json"
        request = Request(f"{self.api_base}{path}", data=data, headers=headers, method=method)
        try:
            with urlopen(request, timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ApiError(f"GitHub API {method} {path} failed: {error}") from error

    def issue(self, number: int) -> Mapping[str, object]:
        return _mapping(self._request("GET", f"/repos/{self.owner}/{self.repo}/issues/{number}"), "Issue")

    def paginated(self, path: str) -> list[object]:
        records: list[object] = []
        page = 1
        while True:
            separator = "&" if "?" in path else "?"
            payload = self._request("GET", f"{path}{separator}{urlencode({'per_page': 100, 'page': page})}")
            if not isinstance(payload, list):
                raise ApiError(f"GitHub API GET {path} returned a non-array page")
            records.extend(payload)
            if not payload:
                return records
            page += 1

    def replace_labels(self, number: int, labels: Sequence[str]) -> None:
        self._request(
            "PUT",
            f"/repos/{self.owner}/{self.repo}/issues/{number}/labels",
            {"labels": list(labels)},
        )

    def comment(self, number: int, body: str) -> None:
        self._request(
            "POST",
            f"/repos/{self.owner}/{self.repo}/issues/{number}/comments",
            {"body": body},
        )


def _event(path: Path) -> tuple[str, str, int, bool]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ClaimError(f"Cannot read GITHUB_EVENT_PATH: {error}") from error
    event = _mapping(document, "GitHub event")
    repository = _mapping(event.get("repository"), "GitHub event repository")
    owner = _mapping(repository.get("owner"), "GitHub event repository owner")
    issue = _mapping(event.get("issue"), "GitHub event issue")
    comment = _mapping(event.get("comment"), "GitHub event comment")
    user = _mapping(comment.get("user"), "GitHub event comment user")
    return (
        _string(owner.get("login"), "GitHub event repository owner login"),
        _string(repository.get("name"), "GitHub event repository name"),
        _integer(issue.get("number"), "GitHub event Issue number"),
        "pull_request" in issue
        or user.get("type") != "User"
        or not is_claim_command(comment.get("body")),
    )


def run(event_path: Path, client: GitHubClient) -> int:
    owner, repo, number, ignored = _event(event_path)
    if ignored:
        return 0
    issue = client.issue(number)
    comments = client.paginated(f"/repos/{owner}/{repo}/issues/{number}/comments")
    timeline = client.paginated(f"/repos/{owner}/{repo}/issues/{number}/timeline")
    plan = plan_claim(issue, comments, timeline, owner, repo)
    if plan.label_names is not None:
        final_issue = client.issue(number)
        final_comments = client.paginated(f"/repos/{owner}/{repo}/issues/{number}/comments")
        final_timeline = client.paginated(f"/repos/{owner}/{repo}/issues/{number}/timeline")
        final_plan = plan_claim(final_issue, final_comments, final_timeline, owner, repo)
        if (
            final_plan.label_names is None
            or final_plan.winner is None
            or plan.winner is None
            or final_plan.winner.identifier != plan.winner.identifier
        ):
            raise ClaimError("Claim eligibility changed before label mutation; no claim was recorded")
        plan = final_plan
        client.replace_labels(number, final_plan.label_names)
    failures = 0
    for candidate in plan.feedback:
        try:
            client.comment(
                number,
                feedback_body(candidate, plan.winner, plan.manual_reason, owner, repo, number),
            )
        except ApiError:
            failures += 1
    if failures:
        raise ApiError("Claim state is valid when applicable, but one or more feedback comments failed")
    return 0


def main() -> int:
    event_path = os.environ.get("GITHUB_EVENT_PATH")
    token = os.environ.get("GITHUB_TOKEN")
    if not event_path or not token:
        print("ERROR: GITHUB_EVENT_PATH and GITHUB_TOKEN are required", file=sys.stderr)
        return 2
    try:
        owner, repo, _, _ = _event(Path(event_path))
        return run(Path(event_path), GitHubClient(owner, repo, token))
    except ClaimError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
