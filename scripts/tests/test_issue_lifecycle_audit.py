"""Offline tests for the read-only Issue lifecycle audit."""

from __future__ import annotations

import io
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import audit_issue_lifecycle as audit  # noqa: E402


FIXTURE = Path(__file__).with_name("fixtures") / "lifecycle-audit-pages.json"


def issue(number: int, *labels: str) -> dict[str, object]:
    return {
        "number": number,
        "title": f"Issue {number}",
        "html_url": f"https://example.test/issues/{number}",
        "labels": [{"name": label} for label in labels],
    }


class Response:
    def __init__(self, payload: bytes):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self):
        return self.payload


class IssueLifecycleAuditTests(unittest.TestCase):
    def invoke(self, *arguments: str) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            status = audit.main(arguments)
        return status, stdout.getvalue(), stderr.getvalue()

    def test_fixture_paginates_excludes_pull_requests_and_reports_each_violation(self):
        status, stdout, stderr = self.invoke(
            "audit", "--repo", "cadence-workflow/cadence-ai-samples", "--fixture", str(FIXTURE)
        )

        self.assertEqual(audit.VIOLATION_EXIT, status)
        self.assertEqual("", stderr)
        self.assertIn("WARNING #3: unknown/deprecated status labels: status:blocked", stdout)
        self.assertIn("WARNING #4: unknown/deprecated status labels: status:legacy", stdout)
        self.assertIn("VIOLATION #3", stdout)
        self.assertIn("found none", stdout)
        self.assertIn("VIOLATION #4", stdout)
        self.assertIn("status:needs-triage, status:ready", stdout)
        self.assertIn("Audited 3 open Issues; skipped 1 pull requests; 2 lifecycle violations", stdout)

    def test_clean_audit_with_unknown_status_warning_succeeds(self):
        findings, skipped = audit.audit_pages([[issue(1, "status:ready", "status:retired")]])
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            status = audit.report(findings, skipped)

        self.assertEqual(0, status)
        self.assertIn("WARNING #1", stdout.getvalue())
        self.assertIn("0 lifecycle violations; 1 status warnings.", stdout.getvalue())

    def test_malformed_issue_record_is_an_operational_failure(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as file:
            fixture = Path(file.name)
            file.write('{"pages": [[{"number": 1, "title": "bad", "html_url": "x"}]]}')
        self.addCleanup(fixture.unlink)

        status, stdout, stderr = self.invoke(
            "audit", "--repo", "cadence-workflow/cadence-ai-samples", "--fixture", str(fixture)
        )

        self.assertEqual(audit.OPERATIONAL_EXIT, status)
        self.assertEqual("", stdout)
        self.assertIn("ERROR: Issue #1 has malformed labels", stderr)

    def test_invalid_fixture_and_repository_are_operational_failures(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as file:
            fixture = Path(file.name)
            file.write("[]")
        self.addCleanup(fixture.unlink)

        status, _, stderr = self.invoke("audit", "--repo", "not-a-repository", "--fixture", str(fixture))
        self.assertEqual(audit.OPERATIONAL_EXIT, status)
        self.assertIn("OWNER/REPO", stderr)

        status, _, stderr = self.invoke(
            "audit", "--repo", "owner/repo", "--fixture", str(fixture)
        )
        self.assertEqual(audit.OPERATIONAL_EXIT, status)
        self.assertIn("Fixture must be an object", stderr)

    def test_live_fetch_paginates_until_empty_response(self):
        first_page = str([issue(1, "status:ready")]).replace("'", '"').encode()
        responses = [Response(first_page), Response(b"[]")]
        with patch.object(audit, "urlopen", side_effect=responses) as urlopen:
            pages = list(audit.fetch_open_issue_pages("owner/repo"))

        self.assertEqual([[issue(1, "status:ready")], []], pages)
        self.assertEqual(2, urlopen.call_count)
        urls = [call.args[0].full_url for call in urlopen.call_args_list]
        self.assertIn("page=1", urls[0])
        self.assertIn("page=2", urls[1])

    def test_non_array_live_response_is_an_operational_failure(self):
        with patch.object(audit, "urlopen", return_value=Response(b'{"message": "bad"}')):
            status, stdout, stderr = self.invoke("audit", "--repo", "owner/repo")

        self.assertEqual(audit.OPERATIONAL_EXIT, status)
        self.assertEqual("", stdout)
        self.assertIn("response must be a JSON array", stderr)


if __name__ == "__main__":
    unittest.main()
