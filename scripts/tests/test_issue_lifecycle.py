"""Offline contracts for the one-lifecycle-label policy, audit, and guard."""

from __future__ import annotations

import io
import math
import random
import sys
import tempfile
import unittest
import unittest.mock
import urllib.parse
from contextlib import redirect_stdout
from pathlib import Path
from typing import Callable

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import issue_lifecycle as lifecycle  # noqa: E402
from issue_lifecycle import (  # noqa: E402
    GUARD_ACTOR,
    IN_PROGRESS,
    NEEDS_TRIAGE,
    READY,
    Authorization,
    GitHubError,
    GuardRun,
    IssueEvent,
    IssueState,
    Trigger,
    UnauthorizedTransition,
)


BASE = "2026-11-05T12:00:00Z"
LATER = "2026-11-05T12:05:00Z"
N = 7


def permission(role_name, *, admin=False, maintain=False, push=False, triage=False):
    return {
        "permission": "admin" if admin else ("write" if push else "read"),
        "role_name": role_name,
        "user": {
            "login": role_name,
            "type": "User",
            "permissions": {
                "admin": admin,
                "maintain": maintain,
                "push": push,
                "triage": triage,
                "pull": True,
            },
        },
    }


PERMISSIONS = {
    "owner": permission("admin", admin=True, maintain=True, push=True, triage=True),
    "maint": permission("maintain", maintain=True, push=True, triage=True),
    "release-mgr": permission("release-manager", maintain=True, push=True, triage=True),
    "lookalike": permission("repo-admin", push=True, triage=True),
    "claimant": permission("write", push=True, triage=True),
    "triager": permission("triage", triage=True),
    "author": permission("read"),
}


class FakeGitHub:
    """In-memory Issue state that records GitHub calls and guard label changes."""

    def __init__(self, permissions=None):
        self.permissions = dict(PERMISSIONS if permissions is None else permissions)
        self.issues: dict[int, dict] = {}
        self.events: dict[int, list[IssueEvent]] = {}
        self.calls: list[tuple] = []
        self.guard_ops: list[tuple[str, str]] = []
        self.op_log: list[tuple[str, str, str]] = []
        self.op_owner = "direct"
        self.events_error = False
        self.after_guard_mutation: Callable[[FakeGitHub, int], None] | None = None
        self._next_id = 100

    def create_issue(self, number, labels=(), *, author="author", is_open=True, is_pull_request=False):
        self.issues[number] = {
            "labels": list(labels),
            "open": is_open,
            "pr": is_pull_request,
            "author": author,
        }
        self.events[number] = []
        for label in labels:
            self._event(number, "labeled", label, author, BASE)

    def _event(self, number, kind, label, actor, at=LATER):
        self._next_id += 1
        actor_type = "Bot" if actor == GUARD_ACTOR else "User"
        self.events[number].append(IssueEvent(self._next_id, kind, label, actor, at, actor_type))

    def change(self, number, label, add, actor) -> bool:
        labels = self.issues[number]["labels"]
        if add == (label in labels):
            return False
        if add:
            labels.append(label)
        else:
            labels.remove(label)
        self._event(number, "labeled" if add else "unlabeled", label, actor)
        return True

    def reopen(self, number, actor):
        self.issues[number]["open"] = True
        self._event(number, "reopened", None, actor)

    def lifecycle(self, number=N):
        return lifecycle.classify(self.issues[number]["labels"]).lifecycle

    def get_issue(self, number):
        self.calls.append(("get_issue", number))
        issue = self.issues[number]
        return IssueState(
            number, issue["open"], tuple(issue["labels"]), issue["pr"], issue["author"], BASE
        )

    def list_open_issues(self):
        self.calls.append(("list_open_issues",))
        return [self.get_issue(number) for number in self.issues if self.issues[number]["open"]]

    def list_issue_events(self, number):
        self.calls.append(("events", number))
        if self.events_error:
            raise GitHubError(502, "events unavailable")
        return list(self.events[number])

    def get_permission(self, login):
        self.calls.append(("permission", login))
        value = self.permissions.get(login)
        if isinstance(value, Exception):
            raise value
        if value is None:
            raise GitHubError(404, "not a collaborator")
        return value

    def add_label(self, number, label):
        self._guard(number, label, True)

    def remove_label(self, number, label):
        self._guard(number, label, False)

    def _guard(self, number, label, add):
        operation = "add" if add else "remove"
        self.calls.append((operation, number, label))
        if self.change(number, label, add, GUARD_ACTOR):
            self.guard_ops.append((label, operation))
            self.op_log.append((self.op_owner, label, operation))
        if self.after_guard_mutation is not None:
            self.after_guard_mutation(self, number)

    def mutation_calls(self):
        return [call for call in self.calls if call[0] in {"add", "remove"}]


def trigger(action, label=None, sender="author", sender_type="User"):
    return Trigger(action, label, sender, sender_type)


def drive(fake, trig, number=N):
    run = GuardRun(fake, number, trig)
    for _ in run.steps():
        pass
    return run


def issue(labels=(), *, author="author", **kwargs):
    fake = FakeGitHub()
    fake.create_issue(N, labels, author=author, **kwargs)
    return fake


class ClassificationTests(unittest.TestCase):
    def test_counts_only_the_three_lifecycle_labels(self):
        found = lifecycle.classify(["type:tooling", READY, "status:blocked", "status:odd"])
        self.assertEqual((READY,), found.lifecycle)
        self.assertTrue(found.valid)
        self.assertEqual(("status:blocked",), found.deprecated)
        self.assertEqual(("status:odd",), found.unknown)

    def test_zero_and_several_are_invalid(self):
        self.assertFalse(lifecycle.classify([]).valid)
        self.assertFalse(lifecycle.classify([NEEDS_TRIAGE, IN_PROGRESS]).valid)

    def test_plan_transition_replaces_every_other_lifecycle_label(self):
        self.assertEqual(
            ((IN_PROGRESS,), (NEEDS_TRIAGE, READY)),
            lifecycle.plan_transition([NEEDS_TRIAGE, READY, "type:x"], IN_PROGRESS),
        )
        self.assertEqual(((), ()), lifecycle.plan_transition([READY], READY))
        with self.assertRaises(ValueError):
            lifecycle.plan_transition([], "status:blocked")


class GuardStateTests(unittest.TestCase):
    def test_zero_labels_become_needs_triage(self):
        for action, label in (("opened", None), ("reopened", None), ("unlabeled", READY)):
            with self.subTest(action=action):
                fake = issue([])
                if action == "reopened":
                    fake.reopen(N, "author")
                if action == "unlabeled":
                    fake.issues[N]["labels"].append(READY)
                    fake.change(N, READY, False, "maint")
                run = drive(fake, trigger(action, label, "maint" if label else "author"))
                self.assertEqual((NEEDS_TRIAGE,), fake.lifecycle())
                self.assertEqual("repaired", run.result.outcome)

    def test_one_label_on_a_non_readiness_trigger_is_unchanged(self):
        for label in (NEEDS_TRIAGE, IN_PROGRESS, READY):
            with self.subTest(label=label):
                fake = issue([label, "type:x"])
                fake.change(N, "area:docs", True, "claimant")
                run = drive(fake, trigger("labeled", "area:docs", "claimant"))
                self.assertEqual("unchanged", run.result.outcome)
                self.assertEqual([], fake.mutation_calls())

    def test_several_labels_keep_the_triggering_lifecycle_label(self):
        fake = issue([NEEDS_TRIAGE])
        fake.change(N, IN_PROGRESS, True, "claimant")
        drive(fake, trigger("labeled", IN_PROGRESS, "claimant"))
        self.assertEqual((IN_PROGRESS,), fake.lifecycle())

    def test_several_labels_on_an_ambiguous_trigger_fall_back(self):
        fake = issue([NEEDS_TRIAGE, IN_PROGRESS])
        fake.change(N, "area:docs", True, "claimant")
        drive(fake, trigger("labeled", "area:docs", "claimant"))
        self.assertEqual((NEEDS_TRIAGE,), fake.lifecycle())

    def test_closed_issue_and_pull_request_are_no_ops(self):
        for kwargs in ({"is_open": False}, {"is_pull_request": True}):
            with self.subTest(**kwargs):
                fake = issue([], **kwargs)
                run = drive(fake, trigger("labeled", READY, "triager"))
                self.assertEqual("unchanged", run.result.outcome)
                self.assertEqual([("get_issue", N)], fake.calls)

    def test_remove_then_add_window_converges_after_grace(self):
        fake = issue([READY])
        fake.change(N, READY, False, "claimant")
        fake.change(N, IN_PROGRESS, True, "claimant")
        first = drive(fake, trigger("unlabeled", READY, "claimant"))
        second = drive(fake, trigger("labeled", IN_PROGRESS, "claimant"))
        self.assertEqual("superseded", first.result.outcome)
        self.assertEqual("unchanged", second.result.outcome)
        self.assertEqual((IN_PROGRESS,), fake.lifecycle())
        self.assertEqual([], fake.mutation_calls())

    def test_add_then_remove_window_converges_after_grace(self):
        fake = issue([READY])
        fake.change(N, IN_PROGRESS, True, "claimant")
        fake.change(N, READY, False, "claimant")
        first = drive(fake, trigger("labeled", IN_PROGRESS, "claimant"))
        second = drive(fake, trigger("unlabeled", READY, "claimant"))
        self.assertEqual("superseded", first.result.outcome)
        self.assertEqual("unchanged", second.result.outcome)
        self.assertEqual((IN_PROGRESS,), fake.lifecycle())

    def test_run_that_mutated_reanchors_to_a_newer_human_event(self):
        fake = issue([READY])
        fake.change(N, READY, False, "maint")

        def human_adds(github, number):
            github.after_guard_mutation = None
            github.change(number, IN_PROGRESS, True, "claimant")

        fake.after_guard_mutation = human_adds
        run = drive(fake, trigger("unlabeled", READY, "maint"))
        self.assertEqual("repaired", run.result.outcome)
        self.assertEqual(1, run.reanchors)
        self.assertEqual(Trigger("labeled", IN_PROGRESS, "claimant", "User"), run.trigger)
        self.assertEqual((IN_PROGRESS,), fake.lifecycle())
        late = drive(fake, trigger("labeled", IN_PROGRESS, "claimant"))
        self.assertEqual("unchanged", late.result.outcome)

    def test_guard_restores_an_automation_transition_it_removed(self):
        fake = issue([IN_PROGRESS])
        fake.change(N, READY, True, "claimant")
        stale = GuardRun(fake, N, trigger("labeled", READY, "claimant"))
        steps = stale.steps()
        for step in steps:
            if step == "add":
                break
        lifecycle.apply_transition(
            fake, N, READY, authorization=lifecycle.authorize(fake, "maint", "User")
        )
        for _ in steps:
            pass
        self.assertEqual((READY,), fake.lifecycle())
        self.assertIn((READY, "add"), fake.guard_ops[-3:])

    def test_guard_never_adds_ready_without_an_automation_transition(self):
        fake = issue([])
        fake.change(N, READY, True, GUARD_ACTOR)
        fake.change(N, READY, False, "maint")
        run = drive(fake, trigger("unlabeled", READY, "maint"))
        self.assertEqual((NEEDS_TRIAGE,), fake.lifecycle())
        self.assertNotIn((READY, "add"), fake.guard_ops)
        self.assertEqual("repaired", run.result.outcome)

    def test_token_transition_during_repair_supersedes_the_stale_decision(self):
        fake = issue([READY])
        fake.change(N, READY, False, "maint")

        def token_claims(github, number):
            github.after_guard_mutation = None
            github.change(number, IN_PROGRESS, True, GUARD_ACTOR)

        fake.after_guard_mutation = token_claims
        run = drive(fake, trigger("unlabeled", READY, "maint"))
        self.assertEqual("repaired", run.result.outcome)
        self.assertEqual(2, run.attempts)
        self.assertEqual((IN_PROGRESS,), fake.lifecycle())

    def test_guard_repairs_never_count_as_automation_transitions(self):
        fake = issue([])
        drive(fake, trigger("opened", None, "author"))
        guard_events = [e for e in fake.events[N] if e.actor == GUARD_ACTOR]
        self.assertTrue(guard_events)
        self.assertFalse(any(lifecycle.is_automation_transition(e) for e in guard_events))

    def test_retries_stop_at_the_bound_and_report_failure(self):
        fake = issue([])

        def keeps_removing(github, number):
            github.change(number, NEEDS_TRIAGE, False, GUARD_ACTOR)

        fake.after_guard_mutation = keeps_removing
        run = drive(fake, trigger("opened", None, "author"))
        self.assertEqual("failed", run.result.outcome)
        self.assertEqual(lifecycle.MAX_ATTEMPTS, run.attempts)

    def test_repeated_delivery_is_idempotent(self):
        fake = issue([NEEDS_TRIAGE])
        fake.change(N, IN_PROGRESS, True, "claimant")
        drive(fake, trigger("labeled", IN_PROGRESS, "claimant"))
        before = list(fake.guard_ops)
        again = drive(fake, trigger("labeled", IN_PROGRESS, "claimant"))
        self.assertEqual("unchanged", again.result.outcome)
        self.assertEqual(before, fake.guard_ops)

    def test_stale_ready_delivery_no_longer_attached_only_checks_the_count(self):
        fake = issue([NEEDS_TRIAGE])
        fake.change(N, READY, True, "triager")
        fake.change(N, READY, False, "triager")
        run = drive(fake, trigger("labeled", READY, "triager"))
        self.assertEqual("superseded", run.result.outcome)
        self.assertNotIn(("permission", "triager"), fake.calls)
        self.assertEqual((NEEDS_TRIAGE,), fake.lifecycle())


class ReadinessTests(unittest.TestCase):
    def test_unrelated_event_on_sole_ready_makes_no_permission_call(self):
        fake = issue([READY], author="triager")
        fake.change(N, "area:docs", True, "author")
        run = drive(fake, trigger("labeled", "area:docs", "author"))
        self.assertEqual("unchanged", run.result.outcome)
        self.assertFalse([call for call in fake.calls if call[0] == "permission"])
        self.assertEqual((READY,), fake.lifecycle())

    def test_labeled_ready_from_maintainer_is_kept_and_others_removed(self):
        for sender in ("owner", "maint", "release-mgr"):
            with self.subTest(sender=sender):
                fake = issue([NEEDS_TRIAGE])
                fake.change(N, READY, True, sender)
                drive(fake, trigger("labeled", READY, sender))
                self.assertEqual((READY,), fake.lifecycle())

    def test_labeled_ready_from_non_maintainer_falls_back(self):
        for sender in ("lookalike", "claimant", "triager", "author", "stranger"):
            with self.subTest(sender=sender):
                fake = issue([NEEDS_TRIAGE])
                fake.change(N, NEEDS_TRIAGE, False, sender)
                fake.change(N, READY, True, sender)
                drive(fake, trigger("labeled", READY, sender))
                self.assertEqual((NEEDS_TRIAGE,), fake.lifecycle())

    def test_rejected_ready_never_restores_in_progress(self):
        fake = issue([IN_PROGRESS])
        fake.change(N, READY, True, "claimant")
        drive(fake, trigger("labeled", READY, "claimant"))
        self.assertEqual((NEEDS_TRIAGE,), fake.lifecycle())

    def test_bot_added_ready_is_rejected_even_from_a_privileged_login(self):
        fake = issue([NEEDS_TRIAGE])
        fake.change(N, NEEDS_TRIAGE, False, "owner")
        fake.change(N, READY, True, "owner")
        drive(fake, trigger("labeled", READY, "owner", sender_type="Bot"))
        self.assertEqual((NEEDS_TRIAGE,), fake.lifecycle())
        self.assertNotIn(("permission", "owner"), fake.calls)

    def test_opened_with_ready_without_a_labeled_delivery(self):
        cases = (
            ("maint", "User", (READY,)),
            ("owner", "User", (READY,)),
            ("triager", "User", (NEEDS_TRIAGE,)),
            ("author", "User", (NEEDS_TRIAGE,)),
            ("owner", "Bot", (NEEDS_TRIAGE,)),
        )
        for author, sender_type, expected in cases:
            with self.subTest(author=author, sender_type=sender_type):
                fake = issue([READY, "type:x"], author=author)
                drive(fake, trigger("opened", None, author, sender_type))
                self.assertEqual(expected, fake.lifecycle())

    def test_opened_with_ready_and_several_labels_from_maintainer(self):
        fake = issue([NEEDS_TRIAGE, READY], author="maint")
        drive(fake, trigger("opened", None, "maint"))
        self.assertEqual((READY,), fake.lifecycle())

    def test_opened_permission_lookup_failure_falls_back(self):
        fake = issue([READY], author="maint")
        fake.permissions["maint"] = GitHubError(502, "unavailable")
        drive(fake, trigger("opened", None, "maint"))
        self.assertEqual((NEEDS_TRIAGE,), fake.lifecycle())

    def test_opened_then_labeled_delivery_reach_the_same_decision(self):
        for author, expected in (("triager", (NEEDS_TRIAGE,)), ("maint", (READY,))):
            with self.subTest(author=author):
                fake = issue([READY], author=author)
                drive(fake, trigger("opened", None, author))
                after_opened = list(fake.mutation_calls())
                drive(fake, trigger("labeled", READY, author))
                self.assertEqual(expected, fake.lifecycle())
                self.assertEqual(after_opened, fake.mutation_calls())

    def test_reopened_ready_is_authorized_against_the_reopening_sender(self):
        for sender, expected in (("maint", (READY,)), ("claimant", (NEEDS_TRIAGE,))):
            with self.subTest(sender=sender):
                fake = issue([READY], author="maint", is_open=False)
                fake.reopen(N, sender)
                drive(fake, trigger("reopened", None, sender))
                self.assertEqual(expected, fake.lifecycle())

    def test_reopened_in_progress_is_unchanged_without_a_permission_call(self):
        for label in (IN_PROGRESS, NEEDS_TRIAGE):
            with self.subTest(label=label):
                fake = issue([label], is_open=False)
                fake.reopen(N, "claimant")
                run = drive(fake, trigger("reopened", None, "claimant"))
                self.assertEqual("unchanged", run.result.outcome)
                self.assertFalse([call for call in fake.calls if call[0] == "permission"])

    def test_policy_never_adds_ready(self):
        labels_sets = ([], [NEEDS_TRIAGE], [IN_PROGRESS], [NEEDS_TRIAGE, IN_PROGRESS])
        actions = (
            ("opened", None),
            ("reopened", None),
            ("labeled", READY),
            ("labeled", IN_PROGRESS),
            ("unlabeled", READY),
        )
        for labels in labels_sets:
            for action, label in actions:
                for sender in ("owner", "triager"):
                    state = IssueState(N, True, tuple(labels), created_at=BASE)
                    granted = lifecycle.authorize(FakeGitHub(), sender, "User")
                    for ordered in (True, False):
                        decision = lifecycle.decide_repair(
                            state, trigger(action, label, sender), granted, ordered=ordered
                        )
                        self.assertNotEqual(READY, decision.target)


class AuthorizationTests(unittest.TestCase):
    def test_effective_admin_or_maintain_capabilities_are_required(self):
        expectations = {
            "owner": True,
            "maint": True,
            "release-mgr": True,
            "lookalike": False,
            "claimant": False,
            "triager": False,
            "author": False,
        }
        for login, granted in expectations.items():
            with self.subTest(login=login):
                result = lifecycle.authorize(FakeGitHub(), login, "User")
                self.assertEqual(granted, result.granted)

    def test_role_name_alone_never_grants(self):
        fake = FakeGitHub({"x": {"role_name": "admin", "user": {"permissions": {"admin": False}}}})
        self.assertFalse(lifecycle.authorize(fake, "x", "User").granted)

    def test_lookup_failures_and_malformed_payloads_fail_closed(self):
        payloads = {
            "missing": None,
            "error": GitHubError(500, "boom"),
            "not-dict": ["admin"],
            "no-user": {"role_name": "admin"},
            "no-permissions": {"user": {}},
            "string-admin": {"user": {"permissions": {"admin": "true"}}},
            "string-maintain": {"user": {"permissions": {"admin": False, "maintain": "yes"}}},
        }
        fake = FakeGitHub({key: value for key, value in payloads.items() if value is not None})
        for login in payloads:
            with self.subTest(login=login):
                self.assertFalse(lifecycle.authorize(fake, login, "User").granted)

    def test_non_user_senders_are_rejected_without_lookup(self):
        fake = FakeGitHub()
        for sender_type in ("Bot", "Organization", ""):
            self.assertFalse(lifecycle.authorize(fake, "owner", sender_type).granted)
        self.assertEqual([], fake.calls)

    def test_granted_authorization_cannot_be_forged(self):
        with self.assertRaises(UnauthorizedTransition):
            Authorization("owner", True, "forged")


class SharedTransitionTests(unittest.TestCase):
    def test_ready_target_requires_authorization_and_makes_no_call(self):
        for authorization in (None, lifecycle.authorize(FakeGitHub(), "claimant", "User")):
            with self.subTest(authorization=authorization):
                fake = issue([IN_PROGRESS])
                fake.calls.clear()
                with self.assertRaises(UnauthorizedTransition):
                    lifecycle.apply_transition(fake, N, READY, authorization=authorization)
                self.assertEqual([], fake.calls)

    def test_claim_transition_leaves_only_in_progress(self):
        fake = issue([READY])
        self.assertEqual(1, lifecycle.apply_transition(fake, N, IN_PROGRESS))
        self.assertEqual((IN_PROGRESS,), fake.lifecycle())

    def test_authorized_release_leaves_only_ready(self):
        fake = issue([IN_PROGRESS])
        granted = lifecycle.authorize(fake, "maint", "User")
        lifecycle.apply_transition(fake, N, READY, authorization=granted)
        self.assertEqual((READY,), fake.lifecycle())

    def test_transition_always_records_a_labeled_event_for_its_target(self):
        for labels in ([READY], [READY, IN_PROGRESS], [IN_PROGRESS]):
            with self.subTest(labels=labels):
                fake = issue(labels)
                lifecycle.apply_transition(fake, N, IN_PROGRESS)
                self.assertEqual((IN_PROGRESS,), fake.lifecycle())
                self.assertTrue(
                    any(lifecycle.is_automation_transition(e) for e in fake.events[N])
                )

    def test_needs_triage_is_not_an_automation_target(self):
        with self.assertRaises(ValueError):
            lifecycle.apply_transition(issue([]), N, NEEDS_TRIAGE)

    def test_transition_failure_is_bounded(self):
        fake = issue([READY])
        fake.after_guard_mutation = lambda github, number: github.change(
            number, NEEDS_TRIAGE, True, GUARD_ACTOR
        )
        with self.assertRaises(lifecycle.TransitionFailed):
            lifecycle.apply_transition(fake, N, IN_PROGRESS)
        self.assertLessEqual(len(fake.mutation_calls()), 3 * lifecycle.MAX_ATTEMPTS)


class OrderingFailureTests(unittest.TestCase):
    def test_events_failure_never_keeps_ready_among_several(self):
        fake = issue([NEEDS_TRIAGE], author="maint")
        fake.change(N, READY, True, "maint")
        fake.events_error = True
        drive(fake, trigger("labeled", READY, "maint"))
        self.assertEqual((NEEDS_TRIAGE,), fake.lifecycle())

    def test_events_failure_rejects_unauthorized_sole_ready(self):
        fake = issue([])
        fake.change(N, READY, True, "triager")
        fake.events_error = True
        drive(fake, trigger("labeled", READY, "triager"))
        self.assertEqual((NEEDS_TRIAGE,), fake.lifecycle())

    def test_events_failure_leaves_authorized_sole_ready_unchanged(self):
        fake = issue([])
        fake.change(N, READY, True, "maint")
        fake.events_error = True
        run = drive(fake, trigger("labeled", READY, "maint"))
        self.assertEqual("unchanged", run.result.outcome)
        self.assertEqual((READY,), fake.lifecycle())

    def test_missing_trigger_event_is_unordered(self):
        fake = issue([NEEDS_TRIAGE, IN_PROGRESS])
        run = drive(fake, trigger("labeled", IN_PROGRESS, "ghost"))
        self.assertEqual((NEEDS_TRIAGE,), fake.lifecycle())
        self.assertEqual("repaired", run.result.outcome)


# Deterministic interleaving. One scheduling step is a human action or exactly
# one GitHub API call by a guard run or a shared-helper transition, including
# each individual label add and remove.


class ScriptProcess:
    def __init__(self, actions):
        self.actions = list(actions)

    @property
    def done(self):
        return not self.actions

    def step(self, race):
        self.actions.pop(0)(race)


class StepProcess:
    """Advance a guard run or helper transition by one GitHub API call per step."""

    def __init__(self, steppable):
        self.steppable = steppable
        self.steps = steppable.steps()
        self.done = False
        try:
            next(self.steps)
        except StopIteration:
            self.done = True

    def step(self, race):
        try:
            next(self.steps)
        except StopIteration:
            self.done = True


class Race:
    def __init__(self, fake, scripts):
        self.fake = fake
        self.processes = {name: ScriptProcess(actions) for name, actions in scripts.items()}
        self.runs: dict[str, GuardRun] = {}
        self.transitions: dict[str, lifecycle.TransitionRun] = {}
        self.deliveries: list[Trigger] = []

    def deliver(self, trig, copies=1):
        for _ in range(copies):
            name = f"run{len(self.deliveries)}"
            self.deliveries.append(trig)
            run = GuardRun(self.fake, N, trig)
            self.runs[name] = run
            self.processes[name] = StepProcess(run)

    def start_transition(self, target, authorization=None):
        name = f"helper{len(self.transitions)}"
        transition = lifecycle.TransitionRun(self.fake, N, target, authorization=authorization)
        self.transitions[name] = transition
        self.processes[name] = StepProcess(transition)

    def ready(self):
        return sorted(name for name, process in self.processes.items() if not process.done)

    def step(self, name):
        self.fake.op_owner = name
        self.processes[name].step(self)
        self.fake.op_owner = "direct"


def add(label, actor, *, sender_type="User", copies=1):
    def action(race):
        if race.fake.change(N, label, True, actor):
            race.deliver(Trigger("labeled", label, actor, sender_type), copies)

    return action


def remove(label, actor):
    def action(race):
        if race.fake.change(N, label, False, actor):
            race.deliver(Trigger("unlabeled", label, actor, "User"))

    return action


def redeliver(index):
    def action(race):
        race.deliver(race.deliveries[index])

    return action


def token_transition(target, authorized_by=None):
    def action(race):
        authorization = None
        if authorized_by is not None:
            authorization = lifecycle.authorize(race.fake, authorized_by, "User")
        race.start_transition(target, authorization)

    return action


def _sequential_step_counts(build):
    race = build()
    counts: dict[str, int] = {}
    while race.ready():
        name = race.ready()[0]
        counts[name] = counts.get(name, 0) + 1
        race.step(name)
    return counts


def schedule_space_bound(build):
    counts = list(_sequential_step_counts(build).values())
    bound = math.factorial(sum(counts))
    for count in counts:
        bound //= math.factorial(count)
    return bound


def exhaustive(build):
    finished = []
    pending: list[list[str]] = [[]]
    while pending:
        prefix = pending.pop()
        race = build()
        for name in prefix:
            race.step(name)
        while race.ready():
            ready = race.ready()
            for alternative in ready[1:]:
                pending.append(prefix + [alternative])
            race.step(ready[0])
            prefix = prefix + [ready[0]]
        finished.append(race)
    return finished


def seeded(build, seeds):
    finished = []
    for seed in range(seeds):
        rng = random.Random(seed)
        race = build()
        while race.ready():
            race.step(rng.choice(race.ready()))
        finished.append(race)
    return finished


def all_schedules(build, *, limit=5000, seeds=600):
    if schedule_space_bound(build) <= limit:
        return exhaustive(build)
    return seeded(build, seeds)


def newest_human_label_event(fake):
    events = [e for e in fake.events[N] if lifecycle.is_ordering_event(e) and e.event == "labeled"]
    return max(events, key=lambda event: event.id)


def oscillates(operations):
    for label in {label for label, _ in operations}:
        sequence = [op for name, op in operations if name == label]
        for index in range(len(sequence) - 2):
            if sequence[index] == sequence[index + 2] != sequence[index + 1]:
                return True
    return False


def owner_oscillates(op_log):
    for owner in {owner for owner, _, _ in op_log}:
        if oscillates([(label, op) for name, label, op in op_log if name == owner]):
            return True
    return False


def guard_changes(race):
    return [entry for entry in race.fake.op_log if entry[0].startswith("run")]


class InterleavingTests(unittest.TestCase):
    def assert_converged(self, races, expected: Callable[[Race], str], *, max_guard_changes):
        self.assertTrue(races)
        for race in races:
            want = expected(race)
            self.assertEqual((want,), race.fake.lifecycle(), race.fake.op_log)
            for run in race.runs.values():
                self.assertIsNotNone(run.result)
                self.assertNotEqual("failed", run.result.outcome)
                self.assertLessEqual(run.attempts, lifecycle.MAX_ATTEMPTS)
            for transition in race.transitions.values():
                self.assertFalse(transition.failed)
                self.assertTrue(transition.recorded)
                self.assertLessEqual(transition.attempts, lifecycle.MAX_ATTEMPTS)
            self.assertLessEqual(len(guard_changes(race)), max_guard_changes, race.fake.op_log)
            self.assertFalse(owner_oscillates(race.fake.op_log), race.fake.op_log)
            changes = len(race.fake.guard_ops)
            for trig in race.deliveries:
                drive(race.fake, trig)
            self.assertEqual(changes, len(race.fake.guard_ops), "a re-run changed settled state")
            self.assertEqual((want,), race.fake.lifecycle())

    def test_competing_ready_and_in_progress_newest_wins(self):
        for ready_sender in ("maint", "triager"):
            with self.subTest(ready_sender=ready_sender):

                def build():
                    fake = issue([NEEDS_TRIAGE])
                    return Race(
                        fake,
                        {
                            "ready": [add(READY, ready_sender)],
                            "claim": [add(IN_PROGRESS, "claimant")],
                        },
                    )

                def expected(race):
                    newest = newest_human_label_event(race.fake)
                    if newest.label == IN_PROGRESS:
                        return IN_PROGRESS
                    return READY if ready_sender == "maint" else NEEDS_TRIAGE

                races = all_schedules(build)
                self.assert_converged(races, expected, max_guard_changes=4)
                outcomes = {race.fake.lifecycle() for race in races}
                self.assertIn((IN_PROGRESS,), outcomes)

    def test_zero_label_repair_racing_a_lifecycle_addition(self):
        def build():
            fake = issue([READY], author="maint")
            return Race(fake, {"human": [remove(READY, "maint"), add(IN_PROGRESS, "claimant")]})

        races = all_schedules(build)
        self.assert_converged(races, lambda race: IN_PROGRESS, max_guard_changes=2)
        self.assertTrue(any(race.fake.guard_ops for race in races))

    def test_claim_add_then_remove_window(self):
        def build():
            fake = issue([READY], author="maint")
            return Race(fake, {"human": [add(IN_PROGRESS, "claimant"), remove(READY, "claimant")]})

        self.assert_converged(all_schedules(build), lambda race: IN_PROGRESS, max_guard_changes=1)

    def test_duplicate_parallel_delivery_has_one_effective_change(self):
        def build():
            fake = issue([NEEDS_TRIAGE])
            return Race(fake, {"claim": [add(IN_PROGRESS, "claimant", copies=2)]})

        races = all_schedules(build)
        self.assert_converged(races, lambda race: IN_PROGRESS, max_guard_changes=1)
        for race in races:
            self.assertEqual([(NEEDS_TRIAGE, "remove")], race.fake.guard_ops)

    def test_older_delivery_redelivered_after_a_newer_one_is_superseded(self):
        def build():
            fake = issue([NEEDS_TRIAGE])
            return Race(
                fake,
                {"human": [add(READY, "maint"), add(IN_PROGRESS, "claimant"), redeliver(0)]},
            )

        races = all_schedules(build)
        self.assert_converged(races, lambda race: IN_PROGRESS, max_guard_changes=3)
        for race in races:
            self.assertEqual("superseded", race.runs["run2"].result.outcome)

    def test_duplicate_after_the_newer_run_settled_changes_nothing(self):
        fake = issue([NEEDS_TRIAGE])
        fake.change(N, READY, True, "maint")
        fake.change(N, IN_PROGRESS, True, "claimant")
        drive(fake, trigger("labeled", IN_PROGRESS, "claimant"))
        settled = list(fake.guard_ops)
        late = drive(fake, trigger("labeled", READY, "maint"))
        self.assertEqual("superseded", late.result.outcome)
        self.assertEqual(settled, fake.guard_ops)
        self.assertEqual((IN_PROGRESS,), fake.lifecycle())

    def test_token_claim_while_a_guard_run_is_pending(self):
        def build():
            fake = issue([READY], author="maint")
            race = Race(fake, {"claim63": [token_transition(IN_PROGRESS)]})
            race.deliver(Trigger("opened", None, "maint", "User"))
            return race

        self.assert_converged(all_schedules(build), lambda race: IN_PROGRESS, max_guard_changes=2)

    def test_token_claim_supersedes_an_in_flight_rejection(self):
        def build():
            fake = issue([NEEDS_TRIAGE])
            return Race(
                fake, {"human": [add(READY, "triager"), token_transition(IN_PROGRESS)]}
            )

        self.assert_converged(all_schedules(build), lambda race: IN_PROGRESS, max_guard_changes=6)

    def test_token_claim_supersedes_a_maintainer_ready_run(self):
        def build():
            fake = issue([NEEDS_TRIAGE])
            return Race(fake, {"human": [add(READY, "maint"), token_transition(IN_PROGRESS)]})

        self.assert_converged(all_schedules(build), lambda race: IN_PROGRESS, max_guard_changes=6)

    def test_authorized_release_supersedes_a_stale_rejection(self):
        def build():
            fake = issue([IN_PROGRESS])
            return Race(
                fake,
                {"human": [add(READY, "claimant"), token_transition(READY, authorized_by="maint")]},
            )

        self.assert_converged(all_schedules(build), lambda race: READY, max_guard_changes=6)

    def test_ordering_lookup_failure_mid_race_never_keeps_ready(self):
        def build():
            fake = issue([NEEDS_TRIAGE])
            fake.events_error = True
            return Race(
                fake,
                {"ready": [add(READY, "maint")], "claim": [add(IN_PROGRESS, "claimant")]},
            )

        races = all_schedules(build)
        for race in races:
            self.assertEqual((NEEDS_TRIAGE,), race.fake.lifecycle())
            for run in race.runs.values():
                self.assertLessEqual(run.attempts, lifecycle.MAX_ATTEMPTS)
            self.assertFalse(oscillates(race.fake.guard_ops))


class FakeTransport:
    """Route REST calls to in-memory pages and record every request."""

    def __init__(self, issues=(), events=None, issue=None, permission_payload=None):
        self.issues = list(issues)
        self.events = list(events or [])
        self.issue = issue
        self.permission_payload = permission_payload
        self.requests: list[tuple[str, str, object]] = []

    def __call__(self, method, url, body):
        self.requests.append((method, url, body))
        parsed = urllib.parse.urlparse(url)
        query = urllib.parse.parse_qs(parsed.query)
        path = parsed.path.split("/repos/owner/repo", 1)[1]
        page = int(query.get("page", ["1"])[0])
        if method == "GET" and path == "/issues":
            return 200, self.issues[(page - 1) * 100 : page * 100]
        if method == "GET" and path.endswith("/events"):
            return 200, self.events[(page - 1) * 100 : page * 100]
        if method == "GET" and path.startswith("/issues/"):
            return 200, self.issue
        if method == "GET" and path.endswith("/permission"):
            return 200, self.permission_payload
        if method == "POST" and path.endswith("/labels"):
            self.issue["labels"].append({"name": body["labels"][0]})
            return 200, self.issue["labels"]
        if method == "DELETE" and "/labels/" in path:
            name = urllib.parse.unquote(path.rsplit("/", 1)[1])
            self.issue["labels"] = [label for label in self.issue["labels"] if label["name"] != name]
            return 200, self.issue["labels"]
        return 404, None


def rest_issue(number, labels, *, pull_request=False):
    payload = {
        "number": number,
        "state": "open",
        "labels": [{"name": label} for label in labels],
        "user": {"login": "author"},
        "created_at": BASE,
    }
    if pull_request:
        payload["pull_request"] = {"url": "x"}
    return payload


class AuditAndClientTests(unittest.TestCase):
    def test_audit_paginates_excludes_pull_requests_and_reports_status_labels(self):
        issues = [rest_issue(number, [NEEDS_TRIAGE]) for number in range(1, 141)]
        issues[3] = rest_issue(4, [])
        issues[4] = rest_issue(5, [READY, IN_PROGRESS])
        issues[5] = rest_issue(6, [READY, "status:blocked"])
        issues[6] = rest_issue(7, [READY, "status:odd"])
        issues[120] = rest_issue(121, [], pull_request=True)
        transport = FakeTransport(issues)
        report = lifecycle.audit(lifecycle.GitHubClient("owner/repo", transport))

        self.assertEqual(139, report.audited)
        self.assertEqual([4, 5], [finding.number for finding in report.invalid])
        self.assertEqual(
            ["deprecated status label status:blocked", "unknown status label status:odd"],
            [finding.problem for finding in report.warnings],
        )
        pages = [url for method, url, _ in transport.requests if "page=" in url]
        self.assertEqual(2, len(pages))
        self.assertTrue(all(method == "GET" for method, _, _ in transport.requests))

    def test_audit_cli_exit_status(self):
        for labels, status in (([NEEDS_TRIAGE], 0), ([], 1)):
            transport = FakeTransport([rest_issue(1, labels)])
            original = lifecycle.urllib_transport
            lifecycle.urllib_transport = lambda token: transport
            try:
                with redirect_stdout(io.StringIO()):
                    self.assertEqual(status, lifecycle.main(["audit", "--repo", "owner/repo"]))
            finally:
                lifecycle.urllib_transport = original

    def test_guard_through_rest_uses_only_label_endpoints_and_never_comments(self):
        issue_payload = rest_issue(N, [NEEDS_TRIAGE, READY])
        events = [
            {"id": 1, "event": "labeled", "label": {"name": NEEDS_TRIAGE},
             "actor": {"login": "author"}, "created_at": BASE},
            {"id": 2, "event": "labeled", "label": {"name": READY},
             "actor": {"login": "maint"}, "created_at": LATER},
        ]
        transport = FakeTransport(
            events=events, issue=issue_payload, permission_payload=PERMISSIONS["maint"]
        )
        client = lifecycle.GitHubClient("owner/repo", transport)
        sleeps = []
        result = lifecycle.run_guard(
            client, N, trigger("labeled", READY, "maint"), grace_seconds=30, settle_seconds=5,
            sleep=sleeps.append,
        )
        self.assertEqual("repaired", result.outcome)
        self.assertEqual([30, 5], sleeps)
        self.assertEqual([{"name": READY}], issue_payload["labels"])
        for method, url, body in transport.requests:
            self.assertNotIn("/comments", url)
            self.assertIn(method, {"GET", "POST", "DELETE"})
            if method != "GET":
                self.assertIn(f"/issues/{N}/labels", url)

    def test_client_reads_the_event_actor_type(self):
        events = [
            {"id": 1, "event": "labeled", "label": {"name": READY},
             "actor": {"login": "maint", "type": "User"}, "created_at": BASE},
            {"id": 2, "event": "labeled", "label": {"name": IN_PROGRESS},
             "actor": {"login": GUARD_ACTOR, "type": "Bot"}, "created_at": LATER},
        ]
        client = lifecycle.GitHubClient("owner/repo", FakeTransport(events=events))
        self.assertEqual(["User", "Bot"], [e.actor_type for e in client.list_issue_events(N)])

    def test_client_rejects_malformed_payloads_and_repositories(self):
        with self.assertRaises(ValueError):
            lifecycle.GitHubClient("not-a-repo", FakeTransport())
        client = lifecycle.GitHubClient("owner/repo", FakeTransport(issue={"number": "x"}))
        with self.assertRaises(GitHubError):
            client.get_issue(N)

    def test_guard_cli_rejects_unknown_actions(self):
        with redirect_stdout(io.StringIO()), unittest.mock.patch("sys.stderr", io.StringIO()):
            status = lifecycle.main(
                ["guard", "--repo", "owner/repo", "--issue", "7", "--action", "edited"]
            )
        self.assertEqual(2, status)

    def test_guard_writes_a_job_summary(self):
        transport = FakeTransport(issue=rest_issue(N, [NEEDS_TRIAGE]))
        original = lifecycle.urllib_transport
        lifecycle.urllib_transport = lambda token: transport
        with tempfile.NamedTemporaryFile("r+", suffix=".md") as summary:
            with unittest.mock.patch.dict("os.environ", {"GITHUB_STEP_SUMMARY": summary.name}):
                try:
                    with redirect_stdout(io.StringIO()):
                        status = lifecycle.main(
                            ["guard", "--repo", "owner/repo", "--issue", str(N),
                             "--action", "opened", "--sender", "author", "--sender-type", "User",
                             "--grace-seconds", "0", "--settle-seconds", "0"]
                        )
                finally:
                    lifecycle.urllib_transport = original
            self.assertEqual(0, status)
            self.assertIn("Issue lifecycle guard", Path(summary.name).read_text(encoding="utf-8"))


class WorkflowContractTests(unittest.TestCase):
    def test_guard_workflow_is_least_privilege_and_event_aware(self):
        path = ROOT / ".github/workflows/issue-lifecycle.yml"
        workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
        triggers = workflow[True]
        self.assertEqual({"issues"}, set(triggers))
        self.assertEqual(
            ["opened", "reopened", "labeled", "unlabeled"], triggers["issues"]["types"]
        )
        self.assertEqual({"contents": "read", "issues": "write"}, workflow["permissions"])
        self.assertNotIn("concurrency", workflow)
        job = workflow["jobs"]["guard"]
        self.assertNotIn("concurrency", job)
        self.assertNotIn("permissions", job)
        checkout = job["steps"][0]
        self.assertFalse(checkout["with"]["persist-credentials"])
        self.assertNotIn("ref", checkout["with"])
        guard = job["steps"][-1]
        self.assertEqual(
            {
                "GITHUB_TOKEN",
                "ISSUE_NUMBER",
                "EVENT_ACTION",
                "EVENT_LABEL",
                "EVENT_SENDER",
                "EVENT_SENDER_TYPE",
            },
            set(guard["env"]),
        )
        self.assertNotIn("${{", guard["run"])


if __name__ == "__main__":
    unittest.main()
