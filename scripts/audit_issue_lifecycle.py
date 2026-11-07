#!/usr/bin/env python3
"""Read-only audit for the repository's open-Issue lifecycle-label invariant."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


LIFECYCLE_LABELS = (
    "status:needs-triage",
    "status:ready",
    "status:in-progress",
)
VIOLATION_EXIT = 1
OPERATIONAL_EXIT = 2


class AuditError(ValueError):
    """Raised when an audit input or API response cannot be interpreted."""


@dataclass(frozen=True)
class IssueFinding:
    number: int
    title: str
    url: str
    lifecycle_labels: tuple[str, ...]
    warning_labels: tuple[str, ...]


def parse_repository(value: str) -> tuple[str, str]:
    owner, separator, name = value.partition("/")
    if not separator or not owner or not name or "/" in name:
        raise AuditError("--repo must use OWNER/REPO format")
    return owner, name


def _issue_finding(record: object) -> IssueFinding | None:
    if not isinstance(record, Mapping):
        raise AuditError("Issue response contains a non-object record")
    if "pull_request" in record:
        return None

    number = record.get("number")
    title = record.get("title")
    url = record.get("html_url", "")
    labels = record.get("labels")
    if not isinstance(number, int) or not isinstance(title, str) or not isinstance(url, str):
        raise AuditError("Issue response record is missing number, title, or html_url")
    if not isinstance(labels, list):
        raise AuditError(f"Issue #{number} has malformed labels")

    names: list[str] = []
    for label in labels:
        if not isinstance(label, Mapping) or not isinstance(label.get("name"), str):
            raise AuditError(f"Issue #{number} has a malformed label")
        names.append(label["name"])

    lifecycle = tuple(label for label in names if label in LIFECYCLE_LABELS)
    warnings = tuple(
        label for label in names if label.startswith("status:") and label not in LIFECYCLE_LABELS
    )
    return IssueFinding(number, title, url, lifecycle, warnings)


def audit_pages(pages: Iterable[Sequence[object]]) -> tuple[list[IssueFinding], int]:
    findings: list[IssueFinding] = []
    skipped_pull_requests = 0
    for page in pages:
        if not isinstance(page, Sequence) or isinstance(page, (str, bytes, bytearray)):
            raise AuditError("Issue response page must be a JSON array")
        for record in page:
            finding = _issue_finding(record)
            if finding is None:
                skipped_pull_requests += 1
            else:
                findings.append(finding)
    return findings, skipped_pull_requests


def fixture_pages(path: Path) -> list[Sequence[object]]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise AuditError(f"Cannot read fixture {path}: {error}") from error
    except json.JSONDecodeError as error:
        raise AuditError(f"Fixture {path} is not valid JSON: {error}") from error

    if not isinstance(document, Mapping) or set(document) != {"pages"}:
        raise AuditError("Fixture must be an object containing only a pages array")
    pages = document["pages"]
    if not isinstance(pages, list):
        raise AuditError("Fixture pages must be an array")
    return pages


def fetch_open_issue_pages(repo: str) -> Iterable[list[object]]:
    owner, name = parse_repository(repo)
    token = os.environ.get("GITHUB_TOKEN")
    page = 1
    while True:
        query = urlencode({"state": "open", "per_page": 100, "page": page})
        url = f"https://api.github.com/repos/{owner}/{name}/issues?{query}"
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "cadence-ai-samples-lifecycle-audit",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        try:
            with urlopen(Request(url, headers=headers), timeout=30) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            raise AuditError(f"GitHub API request failed with HTTP {error.code}: {url}") from error
        except URLError as error:
            raise AuditError(f"GitHub API request failed: {error.reason}") from error
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise AuditError(f"GitHub API response was not valid JSON: {error}") from error
        if not isinstance(payload, list):
            raise AuditError("GitHub API response must be a JSON array")
        yield payload
        if not payload:
            return
        page += 1


def report(findings: Sequence[IssueFinding], skipped_pull_requests: int) -> int:
    warnings = [finding for finding in findings if finding.warning_labels]
    violations = [finding for finding in findings if len(finding.lifecycle_labels) != 1]

    for finding in warnings:
        print(
            f"WARNING #{finding.number}: unknown/deprecated status labels: "
            f"{', '.join(finding.warning_labels)}"
        )
    for finding in violations:
        observed = ", ".join(finding.lifecycle_labels) or "none"
        location = f" ({finding.url})" if finding.url else ""
        print(
            f"VIOLATION #{finding.number}{location}: expected exactly one lifecycle label; "
            f"found {observed}. {finding.title}"
        )

    summary = (
        f"Audited {len(findings)} open Issues; skipped {skipped_pull_requests} pull requests; "
        f"{len(violations)} lifecycle violations; {len(warnings)} status warnings."
    )
    print(summary)
    return VIOLATION_EXIT if violations else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    audit = commands.add_parser("audit", help="audit open Issues without modifying GitHub")
    audit.add_argument("--repo", required=True, help="GitHub repository in OWNER/REPO format")
    audit.add_argument(
        "--fixture",
        type=Path,
        help="offline JSON fixture containing a pages array instead of GitHub API results",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        parse_repository(args.repo)
        pages = fixture_pages(args.fixture) if args.fixture else fetch_open_issue_pages(args.repo)
        findings, skipped_pull_requests = audit_pages(pages)
        return report(findings, skipped_pull_requests)
    except AuditError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return OPERATIONAL_EXIT


if __name__ == "__main__":
    raise SystemExit(main())
