#!/usr/bin/env python3
"""Audit and enforce exactly one lifecycle status label on each open Issue.

`audit` is read-only. `guard` runs from the Issue-event workflow, re-fetches the
Issue, and repairs an invalid lifecycle state. Both use only the standard
library so the workflow needs no dependency installation.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable, Iterator, Protocol, Sequence


NEEDS_TRIAGE = "status:needs-triage"
READY = "status:ready"
IN_PROGRESS = "status:in-progress"
LIFECYCLE_LABELS = (NEEDS_TRIAGE, READY, IN_PROGRESS)
DEPRECATED_STATUS_LABELS = frozenset({"status:blocked"})

GUARD_ACTOR = "github-actions[bot]"
MAX_ATTEMPTS = 3
DEFAULT_GRACE_SECONDS = 30
DEFAULT_SETTLE_SECONDS = 5
CREATION_WINDOW = timedelta(seconds=10)
GUARD_ACTIONS = frozenset({"opened", "reopened", "labeled", "unlabeled"})
API_ROOT = "https://api.github.com"
PER_PAGE = 100


class GitHubError(RuntimeError):
    """Raised for a failed or malformed GitHub API response."""

    def __init__(self, status: int, message: str):
        super().__init__(f"GitHub API error {status}: {message}")
        self.status = status


class UnauthorizedTransition(PermissionError):
    """Raised when status:ready is targeted without maintainer authorization."""


class TransitionFailed(RuntimeError):
    """Raised when a transition does not converge within the retry bound."""


@dataclass(frozen=True)
class IssueState:
    number: int
    open: bool
    labels: tuple[str, ...]
    is_pull_request: bool = False
    author: str | None = None
    created_at: str = ""


@dataclass(frozen=True)
class IssueEvent:
    id: int
    event: str
    label: str | None
    actor: str | None
    created_at: str


@dataclass(frozen=True)
class Trigger:
    action: str
    label: str | None
    sender: str
    sender_type: str


@dataclass(frozen=True)
class Classification:
    lifecycle: tuple[str, ...]
    deprecated: tuple[str, ...]
    unknown: tuple[str, ...]

    @property
    def valid(self) -> bool:
        return len(self.lifecycle) == 1


@dataclass(frozen=True)
class Decision:
    target: str | None
    reason: str


@dataclass(frozen=True)
class Ordering:
    ordered: bool
    superseded: bool


_GRANT = object()


@dataclass(frozen=True)
class Authorization:
    login: str
    granted: bool
    reason: str
    _grant: object = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self.granted and self._grant is not _GRANT:
            raise UnauthorizedTransition("only authorize() may grant maintainer authorization")


class LifecycleClient(Protocol):
    def get_issue(self, number: int) -> IssueState: ...

    def list_open_issues(self) -> list[IssueState]: ...

    def list_issue_events(self, number: int) -> list[IssueEvent]: ...

    def get_permission(self, login: str) -> Any: ...

    def add_label(self, number: int, label: str) -> None: ...

    def remove_label(self, number: int, label: str) -> None: ...


def classify(labels: Sequence[str]) -> Classification:
    present = set(labels)
    lifecycle = tuple(label for label in LIFECYCLE_LABELS if label in present)
    other_status = sorted(
        label for label in present if label.startswith("status:") and label not in LIFECYCLE_LABELS
    )
    return Classification(
        lifecycle=lifecycle,
        deprecated=tuple(label for label in other_status if label in DEPRECATED_STATUS_LABELS),
        unknown=tuple(label for label in other_status if label not in DEPRECATED_STATUS_LABELS),
    )


def is_readiness_bearing(trigger: Trigger, labels: Sequence[str]) -> bool:
    if READY not in labels:
        return False
    if trigger.action in {"opened", "reopened"}:
        return True
    return trigger.action == "labeled" and trigger.label == READY


def authorize(client: LifecycleClient, login: str, sender_type: str) -> Authorization:
    """Authorize a sender from GitHub's effective permission capabilities."""
    if sender_type != "User":
        return Authorization(login, False, f"sender type {sender_type!r} is not a human user")
    if not login:
        return Authorization(login, False, "sender login is missing")
    try:
        payload = client.get_permission(login)
    except GitHubError as error:
        return Authorization(login, False, f"permission lookup failed: {error}")
    user = payload.get("user") if isinstance(payload, dict) else None
    permissions = user.get("permissions") if isinstance(user, dict) else None
    role_name = payload.get("role_name") if isinstance(payload, dict) else None
    if not isinstance(permissions, dict) or not isinstance(permissions.get("admin"), bool):
        return Authorization(login, False, "permission payload is malformed")
    maintain = permissions.get("maintain", False)
    if not isinstance(maintain, bool):
        return Authorization(login, False, "permission payload is malformed")
    if permissions["admin"] or maintain:
        return Authorization(
            login, True, f"effective admin or maintain capability (role_name={role_name!r})", _GRANT
        )
    return Authorization(
        login, False, f"no effective admin or maintain capability (role_name={role_name!r})"
    )


def _toward(lifecycle: tuple[str, ...], target: str, reason: str) -> Decision:
    if lifecycle == (target,):
        return Decision(None, reason)
    return Decision(target, reason)


def decide_repair(
    state: IssueState,
    trigger: Trigger,
    authorization: Authorization | None,
    *,
    ordered: bool = True,
) -> Decision:
    """Return the single lifecycle label the Issue should have, or no change."""
    if not state.open or state.is_pull_request:
        return Decision(None, "closed Issue or pull request")
    lifecycle = classify(state.labels).lifecycle
    readiness = is_readiness_bearing(trigger, state.labels)
    authorized = authorization is not None and authorization.granted
    if readiness and not authorized:
        return _toward(lifecycle, NEEDS_TRIAGE, "status:ready was not set by an authorized maintainer")
    if len(lifecycle) == 1:
        return Decision(None, "exactly one lifecycle label")
    if not lifecycle:
        return Decision(NEEDS_TRIAGE, "no lifecycle label")
    if ordered:
        if readiness:
            return Decision(READY, "an authorized maintainer set status:ready")
        if trigger.action == "labeled" and trigger.label in lifecycle:
            return Decision(trigger.label, f"the newest lifecycle label is {trigger.label}")
    return Decision(NEEDS_TRIAGE, "ambiguous lifecycle labels")


def plan_transition(labels: Sequence[str], target: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Return the labels to add and remove so `target` is the only lifecycle label."""
    if target not in LIFECYCLE_LABELS:
        raise ValueError(f"{target!r} is not a lifecycle label")
    add = () if target in labels else (target,)
    remove = tuple(label for label in LIFECYCLE_LABELS if label != target and label in labels)
    return add, remove


def _require_authorization(target: str, authorization: Authorization | None) -> None:
    if target == READY and not (authorization is not None and authorization.granted):
        raise UnauthorizedTransition("a transition to status:ready requires maintainer authorization")


def _apply_plan(client: LifecycleClient, number: int, labels: Sequence[str], target: str) -> bool:
    add, remove = plan_transition(labels, target)
    for label in add:
        client.add_label(number, label)
    for label in remove:
        client.remove_label(number, label)
    return bool(add or remove)


def apply_transition(
    client: LifecycleClient,
    number: int,
    target: str,
    *,
    authorization: Authorization | None = None,
    max_attempts: int = MAX_ATTEMPTS,
) -> int:
    """Leave `target` as the only lifecycle label and return the attempts used.

    A transition to status:ready requires an Authorization granted by
    `authorize()`. Every attempt re-fetches the Issue before mutating it.
    """
    _require_authorization(target, authorization)
    for attempt in range(1, max_attempts + 1):
        _apply_plan(client, number, client.get_issue(number).labels, target)
        if classify(client.get_issue(number).labels).lifecycle == (target,):
            return attempt
    raise TransitionFailed(f"Issue #{number} did not settle on {target} after {max_attempts} attempts")


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def is_ordering_event(event: IssueEvent) -> bool:
    """Return whether an event is a human lifecycle event that orders guard runs."""
    if event.actor == GUARD_ACTOR:
        return False
    if event.event == "reopened":
        return True
    return event.event in {"labeled", "unlabeled"} and event.label in LIFECYCLE_LABELS


def find_anchor(state: IssueState, events: Sequence[IssueEvent], trigger: Trigger) -> int | None:
    """Return the event id that represents this delivery, or None when it is missing."""
    if trigger.action == "opened":
        if not state.created_at:
            return None
        created = _parse_time(state.created_at)
        anchor = 0
        for event in events:
            if (
                event.event == "labeled"
                and event.actor == trigger.sender
                and _parse_time(event.created_at) - created <= CREATION_WINDOW
            ):
                anchor = max(anchor, event.id)
        return anchor
    if trigger.action == "reopened":
        matches = [e.id for e in events if e.event == "reopened" and e.actor == trigger.sender]
    elif trigger.action in {"labeled", "unlabeled"}:
        matches = [
            e.id
            for e in events
            if e.event == trigger.action and e.label == trigger.label and e.actor == trigger.sender
        ]
    else:
        return None
    return max(matches) if matches else None


def check_ordering(client: LifecycleClient, state: IssueState, trigger: Trigger) -> Ordering:
    try:
        events = client.list_issue_events(state.number)
    except GitHubError:
        return Ordering(ordered=False, superseded=False)
    anchor = find_anchor(state, events, trigger)
    if anchor is None:
        return Ordering(ordered=False, superseded=False)
    superseded = any(is_ordering_event(event) and event.id > anchor for event in events)
    return Ordering(ordered=True, superseded=superseded)


@dataclass
class GuardResult:
    outcome: str
    reason: str
    attempts: int = 0
    final_labels: tuple[str, ...] = ()


class GuardRun:
    """One guard delivery, split into steps that yield before each GitHub exchange.

    `run_guard` drains the steps in order. Tests interleave several runs
    step by step against one shared Issue to reproduce races exactly.
    A `transition` step re-fetches, re-checks ordering, re-decides, and
    mutates as one step.
    """

    def __init__(
        self,
        client: LifecycleClient,
        number: int,
        trigger: Trigger,
        *,
        max_attempts: int = MAX_ATTEMPTS,
    ):
        self.client = client
        self.number = number
        self.trigger = trigger
        self.max_attempts = max_attempts
        self.authorization: Authorization | None = None
        self.attempts = 0
        self.mutated = False
        self.result: GuardResult | None = None

    def _finish(self, outcome: str, reason: str, state: IssueState) -> GuardResult:
        self.result = GuardResult(outcome, reason, self.attempts, state.labels)
        return self.result

    def _decide(self, state: IssueState, ordering: Ordering) -> Decision:
        if is_readiness_bearing(self.trigger, state.labels) and self.authorization is None:
            self.authorization = authorize(self.client, self.trigger.sender, self.trigger.sender_type)
        return decide_repair(state, self.trigger, self.authorization, ordered=ordering.ordered)

    def _apply(self, labels: Sequence[str], target: str) -> None:
        if target == READY:
            _require_authorization(target, self.authorization)
        add, _ = plan_transition(labels, target)
        if READY in add:
            raise UnauthorizedTransition("the guard never adds status:ready")
        if _apply_plan(self.client, self.number, labels, target):
            self.mutated = True

    def steps(self) -> Iterator[str]:
        yield "fetch"
        state = self.client.get_issue(self.number)
        if not state.open or state.is_pull_request:
            self._finish("unchanged", "closed Issue or pull request", state)
            return
        yield "order"
        ordering = check_ordering(self.client, state, self.trigger)
        if ordering.superseded:
            self._finish("superseded", "a newer lifecycle event owns the decision", state)
            return
        if is_readiness_bearing(self.trigger, state.labels):
            yield "permission"
        decision = self._decide(state, ordering)
        if decision.target is None:
            self._finish("unchanged", decision.reason, state)
            return
        while self.attempts < self.max_attempts:
            yield "transition"
            state = self.client.get_issue(self.number)
            ordering = check_ordering(self.client, state, self.trigger)
            if ordering.superseded:
                self._finish("superseded", "a newer lifecycle event owns the decision", state)
                return
            decision = self._decide(state, ordering)
            if decision.target is None:
                self._finish("repaired" if self.mutated else "unchanged", decision.reason, state)
                return
            self.attempts += 1
            self._apply(state.labels, decision.target)
            yield "settle"
            state = self.client.get_issue(self.number)
            ordering = check_ordering(self.client, state, self.trigger)
            if ordering.superseded:
                self._finish("superseded", "a newer lifecycle event owns the decision", state)
                return
            decision = self._decide(state, ordering)
            if decision.target is None:
                self._finish("repaired" if self.mutated else "unchanged", decision.reason, state)
                return
        self._finish("failed", f"state did not settle after {self.max_attempts} attempts", state)


def run_guard(
    client: LifecycleClient,
    number: int,
    trigger: Trigger,
    *,
    grace_seconds: float = DEFAULT_GRACE_SECONDS,
    settle_seconds: float = DEFAULT_SETTLE_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
) -> GuardResult:
    sleep(grace_seconds)
    run = GuardRun(client, number, trigger)
    for step in run.steps():
        if step == "settle":
            sleep(settle_seconds)
    assert run.result is not None
    return run.result


@dataclass(frozen=True)
class AuditFinding:
    number: int
    problem: str
    labels: tuple[str, ...]


@dataclass(frozen=True)
class AuditReport:
    audited: int
    invalid: tuple[AuditFinding, ...]
    warnings: tuple[AuditFinding, ...]


def audit(client: LifecycleClient) -> AuditReport:
    invalid: list[AuditFinding] = []
    warnings: list[AuditFinding] = []
    issues = [issue for issue in client.list_open_issues() if not issue.is_pull_request]
    for issue in sorted(issues, key=lambda item: item.number):
        found = classify(issue.labels)
        if not found.lifecycle:
            invalid.append(AuditFinding(issue.number, "no lifecycle label", issue.labels))
        elif len(found.lifecycle) > 1:
            names = ", ".join(found.lifecycle)
            invalid.append(AuditFinding(issue.number, f"several lifecycle labels: {names}", issue.labels))
        for label in found.deprecated:
            warnings.append(AuditFinding(issue.number, f"deprecated status label {label}", issue.labels))
        for label in found.unknown:
            warnings.append(AuditFinding(issue.number, f"unknown status label {label}", issue.labels))
    return AuditReport(len(issues), tuple(invalid), tuple(warnings))


Transport = Callable[[str, str, Any], tuple[int, Any]]


def urllib_transport(token: str | None) -> Transport:
    def send(method: str, url: str, body: Any) -> tuple[int, Any]:
        data = None if body is None else json.dumps(body).encode("utf-8")
        request = urllib.request.Request(url, data=data, method=method)
        request.add_header("Accept", "application/vnd.github+json")
        request.add_header("X-GitHub-Api-Version", "2022-11-28")
        request.add_header("User-Agent", "cadence-ai-samples-issue-lifecycle")
        if token:
            request.add_header("Authorization", f"Bearer {token}")
        if data is not None:
            request.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                raw = response.read()
                return response.status, json.loads(raw) if raw else None
        except urllib.error.HTTPError as error:
            return error.code, None
        except (urllib.error.URLError, TimeoutError, ValueError) as error:
            raise GitHubError(0, f"{method} {url}: {error}") from error

    return send


def _issue_state(payload: Any) -> IssueState:
    try:
        return IssueState(
            number=int(payload["number"]),
            open=payload["state"] == "open",
            labels=tuple(str(label["name"]) for label in payload["labels"]),
            is_pull_request="pull_request" in payload,
            author=(payload.get("user") or {}).get("login"),
            created_at=str(payload.get("created_at") or ""),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise GitHubError(200, f"malformed Issue payload: {error}") from error


def _issue_event(payload: Any) -> IssueEvent:
    try:
        return IssueEvent(
            id=int(payload["id"]),
            event=str(payload["event"]),
            label=(payload.get("label") or {}).get("name"),
            actor=(payload.get("actor") or {}).get("login"),
            created_at=str(payload["created_at"]),
        )
    except (KeyError, TypeError, ValueError, AttributeError) as error:
        raise GitHubError(200, f"malformed Issue event payload: {error}") from error


class GitHubClient:
    """Minimal REST client for one repository."""

    def __init__(self, repo: str, transport: Transport):
        owner, separator, name = repo.partition("/")
        if not separator or not owner or not name or "/" in name:
            raise ValueError(f"repository must be OWNER/NAME, not {repo!r}")
        self._base = f"{API_ROOT}/repos/{urllib.parse.quote(owner)}/{urllib.parse.quote(name)}"
        self._transport = transport

    def _call(self, method: str, path: str, body: Any = None, ok: tuple[int, ...] = (200,)) -> Any:
        status, payload = self._transport(method, self._base + path, body)
        if status not in ok:
            raise GitHubError(status, f"{method} {path}")
        return payload

    def _paginate(self, path: str) -> list[Any]:
        items: list[Any] = []
        separator = "&" if "?" in path else "?"
        page = 1
        while True:
            payload = self._call("GET", f"{path}{separator}per_page={PER_PAGE}&page={page}")
            if not isinstance(payload, list):
                raise GitHubError(200, f"expected a list from {path}")
            items.extend(payload)
            if len(payload) < PER_PAGE:
                return items
            page += 1

    def get_issue(self, number: int) -> IssueState:
        return _issue_state(self._call("GET", f"/issues/{number}"))

    def list_open_issues(self) -> list[IssueState]:
        return [_issue_state(item) for item in self._paginate("/issues?state=open")]

    def list_issue_events(self, number: int) -> list[IssueEvent]:
        return [_issue_event(item) for item in self._paginate(f"/issues/{number}/events")]

    def get_permission(self, login: str) -> Any:
        return self._call("GET", f"/collaborators/{urllib.parse.quote(login, safe='')}/permission")

    def add_label(self, number: int, label: str) -> None:
        self._call("POST", f"/issues/{number}/labels", {"labels": [label]})

    def remove_label(self, number: int, label: str) -> None:
        quoted = urllib.parse.quote(label, safe="")
        self._call("DELETE", f"/issues/{number}/labels/{quoted}", ok=(200, 204, 404))


def _token() -> str | None:
    return os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or None


def _write_summary(lines: Sequence[str]) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as summary:
        summary.write("\n".join(lines) + "\n")


def _run_audit(args: argparse.Namespace) -> int:
    report = audit(GitHubClient(args.repo, urllib_transport(_token())))
    print(f"Audited {report.audited} open Issues in {args.repo} (pull requests excluded).")
    for finding in report.invalid:
        print(f"INVALID #{finding.number}: {finding.problem}")
    for finding in report.warnings:
        print(f"WARNING #{finding.number}: {finding.problem}")
    if report.invalid:
        print(f"{len(report.invalid)} open Issue(s) do not have exactly one lifecycle label.")
        return 1
    print("Every open Issue has exactly one lifecycle label.")
    return 0


def _run_guard(args: argparse.Namespace) -> int:
    if args.action not in GUARD_ACTIONS:
        print(f"Unsupported Issue action {args.action!r}.", file=sys.stderr)
        return 2
    trigger = Trigger(
        action=args.action,
        label=args.label or None,
        sender=args.sender or "",
        sender_type=args.sender_type or "",
    )
    client = GitHubClient(args.repo, urllib_transport(_token()))
    result = run_guard(
        client,
        args.issue,
        trigger,
        grace_seconds=args.grace_seconds,
        settle_seconds=args.settle_seconds,
    )
    labels = ", ".join(classify(result.final_labels).lifecycle) or "none"
    message = (
        f"Issue #{args.issue} ({trigger.action} {trigger.label or ''} by {trigger.sender}): "
        f"{result.outcome}; {result.reason}; attempts={result.attempts}; lifecycle={labels}"
    )
    print(message)
    _write_summary(["## Issue lifecycle guard", "", message])
    return 1 if result.outcome == "failed" else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)

    audit_parser = commands.add_parser("audit", help="read-only audit of open Issues")
    audit_parser.add_argument("--repo", required=True, help="OWNER/NAME")
    audit_parser.set_defaults(handler=_run_audit)

    guard_parser = commands.add_parser("guard", help="repair one Issue after an Issue event")
    guard_parser.add_argument("--repo", required=True, help="OWNER/NAME")
    guard_parser.add_argument("--issue", required=True, type=int)
    guard_parser.add_argument("--action", default=os.environ.get("EVENT_ACTION"))
    guard_parser.add_argument("--label", default=os.environ.get("EVENT_LABEL"))
    guard_parser.add_argument("--sender", default=os.environ.get("EVENT_SENDER"))
    guard_parser.add_argument("--sender-type", default=os.environ.get("EVENT_SENDER_TYPE"))
    guard_parser.add_argument("--grace-seconds", type=float, default=DEFAULT_GRACE_SECONDS)
    guard_parser.add_argument("--settle-seconds", type=float, default=DEFAULT_SETTLE_SECONDS)
    guard_parser.set_defaults(handler=_run_guard)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.handler(args)
    except (GitHubError, TransitionFailed, UnauthorizedTransition) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
