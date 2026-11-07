"""Offline contracts for deterministic Issue `/claim` processing."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import claim_issue as claims  # noqa: E402


FIXTURE = Path(__file__).with_name("fixtures") / "issue-claim-pages.json"
REPO = "cadence-workflow/cadence-ai-samples"


def issue(*labels: str, assignees: list[object] | None = None) -> dict[str, object]:
    return {
        "number": 171,
        "state": "open",
        "labels": [{"name": label} for label in labels],
        "assignees": [] if assignees is None else assignees,
    }


def timeline_ready() -> list[object]:
    return [
        {
            "id": 1,
            "event": "labeled",
            "created_at": "2026-11-07T18:00:00Z",
            "label": {"name": "status:ready"},
        }
    ]


def comment(
    identifier: int,
    when: str,
    login: str,
    body: str = "/claim",
    *,
    updated_at: str | None = None,
    user_type: str = "User",
) -> dict[str, object]:
    return {
        "id": identifier,
        "created_at": when,
        "updated_at": when if updated_at is None else updated_at,
        "body": body,
        "user": {"login": login, "type": user_type},
    }


def event_file(
    body: str = "/claim", user_type: str = "User", *, pull_request: bool = False
) -> Path:
    handle = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(
        {
            "repository": {"name": "cadence-ai-samples", "owner": {"login": "cadence-workflow"}},
            "issue": {"number": 171, **({"pull_request": {}} if pull_request else {})},
            "comment": {"body": body, "user": {"type": user_type}},
        },
        handle,
    )
    handle.close()
    return Path(handle.name)


class FakeClient:
    def __init__(
        self,
        current_issue: dict[str, object],
        comments: list[object],
        timeline: list[object],
        *,
        fail_labels: bool = False,
        fail_comments: bool = False,
        refetched_issue: dict[str, object] | None = None,
        refetched_comments: list[object] | None = None,
        refetched_timeline: list[object] | None = None,
    ):
        self.current_issue = current_issue
        self.comments = comments
        self.timeline = timeline
        self.fail_labels = fail_labels
        self.fail_comments = fail_comments
        self.refetched_issue = refetched_issue
        self.refetched_comments = refetched_comments
        self.refetched_timeline = refetched_timeline
        self.issue_reads = 0
        self.page_reads: dict[str, int] = {}
        self.label_writes: list[tuple[int, tuple[str, ...]]] = []
        self.comment_writes: list[str] = []
        self.page_paths: list[str] = []
        self.next_comment_id = 1000

    def issue(self, _number: int):
        self.issue_reads += 1
        if self.issue_reads > 1 and self.refetched_issue is not None:
            return deepcopy(self.refetched_issue)
        return deepcopy(self.current_issue)

    def paginated(self, path: str):
        self.page_paths.append(path)
        reads = self.page_reads.get(path, 0)
        self.page_reads[path] = reads + 1
        if path.endswith("/comments"):
            if reads and self.refetched_comments is not None:
                return deepcopy(self.refetched_comments)
            return deepcopy(self.comments)
        if path.endswith("/timeline"):
            if reads and self.refetched_timeline is not None:
                return deepcopy(self.refetched_timeline)
            return deepcopy(self.timeline)
        raise AssertionError(path)

    def replace_labels(self, number: int, labels):
        if self.fail_labels:
            raise claims.ApiError("label write failed")
        self.label_writes.append((number, tuple(labels)))
        self.current_issue["labels"] = [{"name": label} for label in labels]
        self.timeline.append(
            {
                "id": 999,
                "event": "labeled",
                "created_at": "2026-11-07T18:10:00Z",
                "label": {"name": "status:in-progress"},
            }
        )

    def comment(self, _number: int, body: str):
        if self.fail_comments:
            raise claims.ApiError("comment write failed")
        self.comment_writes.append(body)
        self.comments.append(
            {
                "id": self.next_comment_id,
                "created_at": "2026-11-07T18:11:00Z",
                "updated_at": "2026-11-07T18:11:00Z",
                "body": body,
                "user": {"login": "github-actions[bot]", "type": "Bot"},
            }
        )
        self.next_comment_id += 1


class Response:
    def __init__(self, payload: object):
        self.payload = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self):
        return self.payload


class IssueClaimTests(unittest.TestCase):
    def test_github_client_paginates_all_comment_pages(self):
        fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
        responses = [Response(page) for page in fixture["comments"]]
        client = claims.GitHubClient("owner", "repo", "token")

        with patch.object(claims, "urlopen", side_effect=responses) as urlopen:
            comments = client.paginated("/repos/owner/repo/issues/171/comments")

        self.assertEqual([101, 102], [record["id"] for record in comments])
        self.assertEqual(3, urlopen.call_count)
        urls = [call.args[0].full_url for call in urlopen.call_args_list]
        self.assertIn("page=1", urls[0])
        self.assertIn("page=2", urls[1])
        self.assertIn("page=3", urls[2])

    def test_github_client_paginates_all_timeline_pages(self):
        fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
        responses = [Response(page) for page in fixture["timeline"]]
        client = claims.GitHubClient("owner", "repo", "token")

        with patch.object(claims, "urlopen", side_effect=responses) as urlopen:
            timeline = client.paginated("/repos/owner/repo/issues/171/timeline")

        self.assertEqual([1, 2], [record["id"] for record in timeline])
        self.assertEqual(2, urlopen.call_count)
        self.assertIn("page=1", urlopen.call_args_list[0].args[0].full_url)
        self.assertIn("page=2", urlopen.call_args_list[1].args[0].full_url)

    def test_fixture_pages_choose_earliest_candidate_and_ignore_closed_pr(self):
        fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
        comments = [item for page in fixture["comments"] for item in page]
        timeline = [item for page in fixture["timeline"] for item in page]

        plan = claims.plan_claim(issue("type:tooling", "status:ready"), comments, timeline, *REPO.split("/"))

        self.assertEqual(101, plan.winner.identifier)
        self.assertEqual(("type:tooling", "status:in-progress"), plan.label_names)
        self.assertEqual([101, 102], [candidate.identifier for candidate in plan.feedback])

    def test_exact_command_requires_unedited_human_comment(self):
        self.assertTrue(claims.is_claim_command(" \t/claim\n"))
        for body in ("/Claim", "/claim now", "hello /claim", ""):
            self.assertFalse(claims.is_claim_command(body))

        candidates = claims.claim_comments(
            [
                comment(1, "2026-11-07T18:01:00Z", "human"),
                comment(2, "2026-11-07T18:02:00Z", "bot", user_type="Bot"),
                comment(3, "2026-11-07T18:03:00Z", "app", user_type="Organization"),
                comment(
                    4,
                    "2026-11-07T18:04:00Z",
                    "edited",
                    updated_at="2026-11-07T18:04:01Z",
                ),
            ]
        )
        self.assertEqual([1], [candidate.identifier for candidate in candidates])

    def test_pre_ready_claims_are_stale_and_equal_timestamp_is_not(self):
        timeline = timeline_ready()
        comments = [
            comment(1, "2026-11-07T17:59:59Z", "stale"),
            comment(2, "2026-11-07T18:00:00Z", "current"),
        ]
        plan = claims.plan_claim(issue("status:ready"), comments, timeline, *REPO.split("/"))

        self.assertEqual(2, plan.winner.identifier)

    def test_zero_lifecycle_labels_use_manual_earliest_candidate_rule(self):
        comments = [
            comment(2, "2026-11-07T18:02:00Z", "second"),
            comment(1, "2026-11-07T18:01:00Z", "first"),
        ]
        plan = claims.plan_claim(issue("type:tooling"), comments, timeline_ready(), *REPO.split("/"))

        self.assertIsNone(plan.label_names)
        self.assertEqual(1, plan.winner.identifier)
        self.assertEqual([2, 1], [candidate.identifier for candidate in plan.feedback])

    def test_three_claimants_are_sorted_by_timestamp_then_immutable_id(self):
        comments = [
            comment(3, "2026-11-07T18:02:00Z", "third"),
            comment(2, "2026-11-07T18:01:00Z", "second"),
            comment(1, "2026-11-07T18:01:00Z", "first"),
        ]
        plan = claims.plan_claim(issue("status:ready"), comments, timeline_ready(), *REPO.split("/"))

        self.assertEqual(1, plan.winner.identifier)
        self.assertEqual({1, 2, 3}, {candidate.identifier for candidate in plan.feedback})

    def test_same_repository_open_pr_blocks_but_closed_ordinary_and_cross_repo_references_do_not(self):
        def reference(
            state: str, pull_request: object, repository: str, event: str = "cross-referenced"
        ) -> dict[str, object]:
            return {
                "event": event,
                "source": {
                    "issue": {
                        "state": state,
                        "pull_request": pull_request,
                        "repository": {"full_name": repository},
                    }
                },
            }

        self.assertTrue(
            claims.has_active_implementation_pr(
                [reference("open", {}, REPO)], *REPO.split("/")
            )
        )
        self.assertFalse(
            claims.has_active_implementation_pr(
                [
                    reference("closed", {}, REPO),
                    reference("open", None, REPO),
                    reference("open", {}, "other/repository"),
                ],
                *REPO.split("/"),
            )
        )

    def test_opaque_connected_event_uses_manual_fallback(self):
        comments = [comment(1, "2026-11-07T18:01:00Z", "claimant")]
        plan = claims.plan_claim(
            issue("status:ready"),
            comments,
            timeline_ready() + [{"event": "connected"}],
            *REPO.split("/"),
        )

        self.assertIsNone(plan.label_names)
        self.assertIn("Opaque connected", plan.manual_reason)
        self.assertEqual(1, plan.winner.identifier)

    def test_malformed_timeline_fails_closed(self):
        broken = [{"event": "cross-referenced", "source": {"issue": {"state": "open"}}}]
        with self.assertRaisesRegex(claims.ClaimError, "repository"):
            claims.has_active_implementation_pr(broken, *REPO.split("/"))

    def test_only_github_actions_markers_suppress_feedback(self):
        source = comment(1, "2026-11-07T18:01:00Z", "winner")
        spoofed = comment(
            2,
            "2026-11-07T18:02:00Z",
            "spoof",
            claims.response_marker(1),
        )
        malformed_spoof = comment(
            3,
            "2026-11-07T18:03:00Z",
            "spoof",
            "<!-- issue-claim-response:not-an-id -->",
        )
        plan = claims.plan_claim(
            issue("status:ready"), [source, spoofed, malformed_spoof], timeline_ready(), *REPO.split("/")
        )

        self.assertEqual([1], [candidate.identifier for candidate in plan.feedback])

    def test_winner_and_loser_feedback_link_the_winning_comment_permalink(self):
        winner = claims.ClaimComment(
            41, claims._timestamp("2026-11-07T18:01:00Z", "test"), "winner", "/claim"
        )
        loser = claims.ClaimComment(
            42, claims._timestamp("2026-11-07T18:02:00Z", "test"), "loser", "/claim"
        )
        permalink = "https://github.com/cadence-workflow/cadence-ai-samples/issues/171#issuecomment-41"

        winner_message = claims.feedback_body(
            winner, winner, None, "cadence-workflow", "cadence-ai-samples", 171
        )
        loser_message = claims.feedback_body(
            loser, winner, None, "cadence-workflow", "cadence-ai-samples", 171
        )

        self.assertIn(f"[winning /claim comment]({permalink})", winner_message)
        self.assertIn(f"[winning /claim comment]({permalink})", loser_message)
        self.assertNotIn("comment #41", winner_message)
        self.assertNotIn("comment #41", loser_message)

    def test_in_progress_recovery_keeps_pre_transition_winner(self):
        timeline = timeline_ready() + [
            {
                "event": "labeled",
                "created_at": "2026-11-07T18:02:00Z",
                "label": {"name": "status:in-progress"},
            }
        ]
        comments = [
            comment(1, "2026-11-07T18:01:00Z", "established"),
            comment(2, "2026-11-07T18:03:00Z", "late"),
        ]
        plan = claims.plan_claim(issue("status:in-progress"), comments, timeline, *REPO.split("/"))

        self.assertEqual(1, plan.winner.identifier)
        self.assertEqual([1, 2], [candidate.identifier for candidate in plan.feedback])

    def test_in_progress_recovery_with_assignee_uses_manual_earliest_candidate(self):
        timeline = timeline_ready() + [
            {
                "event": "labeled",
                "created_at": "2026-11-07T18:02:00Z",
                "label": {"name": "status:in-progress"},
            }
        ]
        comments = [
            comment(1, "2026-11-07T18:01:00Z", "first"),
            comment(2, "2026-11-07T18:03:00Z", "late"),
        ]
        plan = claims.plan_claim(
            issue("status:in-progress", assignees=[{"login": "owner"}]),
            comments,
            timeline,
            *REPO.split("/"),
        )

        self.assertEqual(1, plan.winner.identifier)
        self.assertIn("assigned", plan.manual_reason)

    def test_later_in_progress_claim_cannot_become_manual_fallback_winner(self):
        timeline = timeline_ready() + [
            {
                "event": "labeled",
                "created_at": "2026-11-07T18:02:00Z",
                "label": {"name": "status:in-progress"},
            }
        ]
        plan = claims.plan_claim(
            issue("status:in-progress"),
            [comment(2, "2026-11-07T18:03:00Z", "late")],
            timeline,
            *REPO.split("/"),
        )

        self.assertIsNone(plan.winner)
        self.assertIn("predates", plan.manual_reason)

    def test_invalid_lifecycle_and_assignee_use_manual_feedback_without_mutation(self):
        comments = [comment(1, "2026-11-07T18:01:00Z", "claimant")]
        for current_issue in (
            issue("status:ready", "status:in-progress"),
            issue("status:ready", assignees=[{"login": "owner"}]),
        ):
            with self.subTest(current_issue=current_issue):
                plan = claims.plan_claim(current_issue, comments, timeline_ready(), *REPO.split("/"))
                self.assertIsNone(plan.label_names)
                self.assertIsNotNone(plan.manual_reason)

    def test_label_failure_leaves_no_feedback_or_claim(self):
        path = event_file()
        self.addCleanup(path.unlink)
        client = FakeClient(
            issue("status:ready"),
            [comment(1, "2026-11-07T18:01:00Z", "claimant")],
            timeline_ready(),
            fail_labels=True,
        )

        with self.assertRaisesRegex(claims.ApiError, "label write failed"):
            claims.run(path, client)

        self.assertEqual([], client.label_writes)
        self.assertEqual([], client.comment_writes)
        self.assertEqual(["status:ready"], [label["name"] for label in client.current_issue["labels"]])

    def test_all_pending_claims_are_acknowledged_when_one_run_survives(self):
        path = event_file()
        self.addCleanup(path.unlink)
        comments = [
            comment(3, "2026-11-07T18:03:00Z", "third"),
            comment(1, "2026-11-07T18:01:00Z", "first"),
            comment(2, "2026-11-07T18:02:00Z", "second"),
        ]
        client = FakeClient(issue("status:ready"), comments, timeline_ready())

        self.assertEqual(0, claims.run(path, client))
        self.assertEqual(1, len(client.label_writes))
        self.assertEqual(3, len(client.comment_writes))
        self.assertTrue(all(claims.response_marker(identifier) in "".join(client.comment_writes) for identifier in (1, 2, 3)))

    def test_unclaimable_issue_acknowledges_all_pending_exact_claims(self):
        path = event_file()
        self.addCleanup(path.unlink)
        comments = [
            comment(3, "2026-11-07T18:03:00Z", "third"),
            comment(1, "2026-11-07T18:01:00Z", "first"),
            comment(2, "2026-11-07T18:02:00Z", "second"),
        ]
        client = FakeClient(issue("type:tooling"), comments, timeline_ready())

        self.assertEqual(0, claims.run(path, client))
        self.assertEqual([], client.label_writes)
        self.assertEqual(3, len(client.comment_writes))

    def test_same_second_transition_recovers_original_winner_without_relabeling(self):
        path = event_file()
        self.addCleanup(path.unlink)
        timeline = timeline_ready() + [
            {
                "event": "labeled",
                "created_at": "2026-11-07T18:01:00Z",
                "label": {"name": "status:in-progress"},
            }
        ]
        client = FakeClient(
            issue("status:in-progress"),
            [comment(1, "2026-11-07T18:01:00Z", "winner")],
            timeline,
        )

        self.assertEqual(0, claims.run(path, client))
        self.assertEqual([], client.label_writes)
        self.assertEqual(1, len(client.comment_writes))
        self.assertIn("Claim confirmed for @winner", client.comment_writes[0])
        self.assertIn("issuecomment-1", client.comment_writes[0])

    def test_later_second_claim_still_cannot_recover_as_winner(self):
        timeline = timeline_ready() + [
            {
                "event": "labeled",
                "created_at": "2026-11-07T18:01:00Z",
                "label": {"name": "status:in-progress"},
            }
        ]
        plan = claims.plan_claim(
            issue("status:in-progress"),
            [comment(2, "2026-11-07T18:01:01Z", "late")],
            timeline,
            *REPO.split("/"),
        )
        self.assertIsNone(plan.winner)

    def test_first_run_then_later_loser_run_keeps_original_winner(self):
        path = event_file()
        self.addCleanup(path.unlink)
        comments = [comment(1, "2026-11-07T18:01:00Z", "winner")]
        client = FakeClient(issue("status:ready"), comments, timeline_ready())

        self.assertEqual(0, claims.run(path, client))
        self.assertEqual(1, len(client.label_writes))
        self.assertEqual(1, len(client.comment_writes))

        comments.append(comment(2, "2026-11-07T18:11:00Z", "loser"))
        self.assertEqual(0, claims.run(path, client))
        self.assertEqual(1, len(client.label_writes))
        self.assertEqual(2, len(client.comment_writes))
        self.assertIn("claimed by @winner", client.comment_writes[1])
        self.assertIn("issuecomment-1", client.comment_writes[1])

        self.assertEqual(0, claims.run(path, client))
        self.assertEqual(1, len(client.label_writes))
        self.assertEqual(2, len(client.comment_writes))

    def test_later_delivery_running_first_handles_both_claims_then_delayed_run_is_noop(self):
        path = event_file()
        self.addCleanup(path.unlink)
        comments = [
            comment(1, "2026-11-07T18:01:00Z", "winner"),
            comment(2, "2026-11-07T18:02:00Z", "loser"),
        ]
        client = FakeClient(issue("status:ready"), comments, timeline_ready())

        # The later comment's delivery runs first and sees both comments.
        self.assertEqual(0, claims.run(path, client))
        self.assertEqual(1, len(client.label_writes))
        self.assertEqual(2, len(client.comment_writes))
        self.assertTrue(any("Claim confirmed for @winner" in body for body in client.comment_writes))
        self.assertTrue(any("claimed by @winner" in body for body in client.comment_writes))

        # The earlier comment's delayed delivery runs second and changes nothing.
        self.assertEqual(0, claims.run(path, client))
        self.assertEqual(1, len(client.label_writes))
        self.assertEqual(2, len(client.comment_writes))

    def test_final_rescan_blocks_changed_eligibility_or_winner(self):
        path = event_file()
        self.addCleanup(path.unlink)
        initial_comments = [comment(2, "2026-11-07T18:01:00Z", "initial")]
        active_pr = timeline_ready() + [
            {
                "event": "cross-referenced",
                "source": {
                    "issue": {
                        "state": "open",
                        "pull_request": {},
                        "repository": {"full_name": REPO},
                    }
                },
            }
        ]
        cases = (
            (
                "new-active-pr",
                issue("status:ready"),
                initial_comments,
                active_pr,
            ),
            (
                "new-readiness-window",
                issue("status:ready"),
                initial_comments,
                timeline_ready()
                + [
                    {
                        "event": "labeled",
                        "created_at": "2026-11-07T18:02:00Z",
                        "label": {"name": "status:ready"},
                    }
                ],
            ),
            (
                "edited-candidate",
                issue("status:ready"),
                [
                    comment(
                        2,
                        "2026-11-07T18:01:00Z",
                        "initial",
                        updated_at="2026-11-07T18:01:01Z",
                    )
                ],
                timeline_ready(),
            ),
            (
                "new-assignee",
                issue("status:ready", assignees=[{"login": "owner"}]),
                initial_comments,
                timeline_ready(),
            ),
            (
                "lifecycle-change",
                issue("status:in-progress"),
                initial_comments,
                timeline_ready(),
            ),
            (
                "different-earlier-winner",
                issue("status:ready"),
                [comment(1, "2026-11-07T18:00:30Z", "new"), *initial_comments],
                timeline_ready(),
            ),
        )
        for name, final_issue, final_comments, final_timeline in cases:
            with self.subTest(change=name):
                client = FakeClient(
                    issue("status:ready"),
                    initial_comments,
                    timeline_ready(),
                    refetched_issue=final_issue,
                    refetched_comments=final_comments,
                    refetched_timeline=final_timeline,
                )
                with self.assertRaisesRegex(claims.ClaimError, "eligibility changed"):
                    claims.run(path, client)
                self.assertEqual([], client.label_writes)
                self.assertEqual([], client.comment_writes)

    def test_confirmation_failure_keeps_claim_and_later_delivery_retries_feedback_only(self):
        path = event_file()
        self.addCleanup(path.unlink)
        comments = [
            comment(1, "2026-11-07T18:01:00Z", "winner"),
            comment(2, "2026-11-07T18:02:00Z", "loser"),
        ]
        client = FakeClient(issue("type:tooling", "status:ready"), comments, timeline_ready(), fail_comments=True)

        with self.assertRaisesRegex(claims.ApiError, "feedback comments failed"):
            claims.run(path, client)

        self.assertEqual(
            [(171, ("type:tooling", "status:in-progress"))],
            client.label_writes,
        )
        self.assertEqual([], client.comment_writes)

        client.fail_comments = False
        self.assertEqual(0, claims.run(path, client))
        self.assertEqual(1, len(client.label_writes))
        self.assertEqual(2, len(client.comment_writes))

    def test_manual_release_starts_a_fresh_claim_window(self):
        timeline = [
            *timeline_ready(),
            {
                "event": "labeled",
                "created_at": "2026-11-07T18:02:00Z",
                "label": {"name": "status:in-progress"},
            },
            {
                "event": "labeled",
                "created_at": "2026-11-07T18:03:00Z",
                "label": {"name": "status:ready"},
            },
        ]
        plan = claims.plan_claim(
            issue("status:ready"),
            [
                comment(1, "2026-11-07T18:01:00Z", "released"),
                comment(2, "2026-11-07T18:04:00Z", "new"),
            ],
            timeline,
            *REPO.split("/"),
        )

        self.assertEqual(2, plan.winner.identifier)

    def test_successful_replay_neither_relabels_nor_duplicates_feedback(self):
        path = event_file()
        self.addCleanup(path.unlink)
        source = comment(1, "2026-11-07T18:01:00Z", "winner")
        client = FakeClient(
            issue("status:ready"),
            [source],
            timeline_ready(),
        )

        self.assertEqual(0, claims.run(path, client))
        self.assertEqual(1, len(client.label_writes))
        self.assertEqual(1, len(client.comment_writes))
        self.assertEqual(0, claims.run(path, client))
        self.assertEqual(1, len(client.label_writes))
        self.assertEqual(1, len(client.comment_writes))

    def test_non_command_bot_and_pull_request_events_do_not_mutate(self):
        for body, user_type, is_pull_request in (
            ("/Claim", "User", False),
            ("/claim", "Bot", False),
            ("/claim", "User", True),
        ):
            path = event_file(body, user_type, pull_request=is_pull_request)
            self.addCleanup(path.unlink)
            client = FakeClient(issue("status:ready"), [], timeline_ready())
            self.assertEqual(0, claims.run(path, client))
            self.assertEqual([], client.page_paths)


if __name__ == "__main__":
    unittest.main()
