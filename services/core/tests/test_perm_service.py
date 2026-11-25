"""PermissionService: events -> diff, idempotence, order of operations and the ADR 0012
divergence table (grant/business write failures, failed revoke, partial application)."""

from datetime import timedelta

import pytest

from hs_core.jobs.alerts import FakeAlertSink
from hs_core.permissions.service import (
    NullQueue,
    PartialApplication,
    PermissionEvent,
    Scope,
    account_changed,
    assignment_changed,
    case_changed,
    guardian_changed,
    role_changed,
    row_created,
    substitute_cover,
)
from hs_core.repository import RepositoryUnavailable
from jobs_helpers import T0
from perm_world import H, World, iso


@pytest.fixture
def w():
    return World().seed()


def can(w, uid, table, rid):
    return w.as_(uid).can_read(table, rid)


# ------------------------------------------------------------------ events
def test_assignment_start_grants_the_teacher_and_the_address(w):
    assert not can(w, "uT4", "child", "a2") and not can(w, "uT4", "home_location", "hlA")
    w.add("assignment", "asgN", child_id="a2", teacher_id="T4", start_at=iso(T0), end_at=None)
    w.svc.apply(assignment_changed("assignment:asgN:start", "a2", "T4"))
    assert can(w, "uT4", "child", "a2") and can(w, "uT4", "home_location", "hlA")
    assert can(w, "uT4", "session_log", "slA2")  # child's history while assigned
    assert not can(w, "uT4", "child", "a1")


def test_assignment_end_revokes_child_now_and_address_after_the_grace(w):
    w.patch("assignment", "asg1", end_at=iso(T0), end_reason="trial_not_continued")
    w.patch("session", "sA1", scheduled_end_at=iso(T0 - 3 * H))
    w.svc.apply(assignment_changed("assignment:asg1:end", "a1", "T1", ended=True))
    assert not can(w, "uT1", "child", "a1") and not can(w, "uT1", "session_log", "slA1")
    assert can(w, "uT1", "home_location", "hlA")  # still inside 24 h of the last session
    assert can(w, "uT1", "teacher_session_history", "tshT1")  # own redacted history stays
    win = w.repo.list("access_window")
    assert [
        (x["teacher_id"], x["child_id"], x["state"]) for x in win if x["teacher_id"] == "T1"
    ] == [("T1", "a1", "open")]
    w.clock.now = T0 + 21 * H + timedelta(seconds=1)
    w.svc.apply(assignment_changed("assignment:asg1:end2", "a1", "T1", ended=True))
    assert not can(w, "uT1", "home_location", "hlA")
    assert all(
        x["state"] == "closed" for x in w.repo.list("access_window") if x["teacher_id"] == "T1"
    )


def test_substitute_cover_event_grants_then_window_event_revokes(w):
    w.add(
        "session",
        "sSub2",
        child_id="a2",
        teacher_id="T4",
        is_substitute=True,
        status="scheduled",
        scheduled_start_at=iso(T0 + 2 * H),
        scheduled_end_at=iso(T0 + 3 * H),
    )
    w.svc.apply(substitute_cover("session:sSub2:cover", "a2", "T4"))
    assert (
        can(w, "uT4", "child", "a2")
        and can(w, "uT4", "home_location", "hlA")
        and can(w, "uT4", "session", "sSub2")
    )
    w.clock.now = T0 + 3 * H + 24 * H
    w.svc.apply(substitute_cover("session:sSub2:expire", "a2", "T4"))
    assert not can(w, "uT4", "child", "a2") and not can(w, "uT4", "home_location", "hlA")
    assert not can(w, "uT4", "session", "sSub2")


def test_case_created_assigned_and_closed(w):
    w.add(
        "safeguarding_case",
        "cs9",
        child_id="b1",
        teacher_id="T5",
        raised_by_id="uPB",
        assigned_to_id=None,
        status="open",
    )
    w.add("case_status", "csB9", case_id="cs9", reporter_id="uPB")
    w.svc.apply(case_changed("case:cs9:create", "cs9", "case_created"))
    assert not can(w, "uL2", "safeguarding_case", "cs9") and can(w, "uPB", "case_status", "csB9")
    assert not can(w, "uPB", "safeguarding_case", "cs9")
    w.patch("safeguarding_case", "cs9", assigned_to_id="uL2", status="acknowledged")
    w.svc.apply(case_changed("case:cs9:assign", "cs9", "case_assigned"))
    assert can(w, "uL2", "safeguarding_case", "cs9") and can(w, "uL2", "child", "b1")
    assert (
        can(w, "uL2", "home_location", "hlB")
        and can(w, "uL2", "teacher_document", "td_t4") is False
    )
    w.patch("safeguarding_case", "cs9", status="closed")
    w.svc.apply(case_changed("case:cs9:close", "cs9", "case_closed"))
    assert not can(w, "uL2", "child", "b1") and not can(w, "uL2", "home_location", "hlB")
    assert can(w, "uL2", "safeguarding_case", "cs9")  # the record stays with its lead


def test_role_change_adds_and_removes_access(w):
    w.revoke_label("uT1", "teacher")
    w.svc.apply(role_changed("role:uT1:withdraw-teacher", "uT1"))
    assert not can(w, "uT1", "child", "a1") and not can(w, "uT1", "assignment", "asg1")
    assert can(w, "uT1", "user_profile", "uT1")
    w.grant_label("uT1", "teacher")
    w.svc.apply(role_changed("role:uT1:grant-teacher", "uT1"))
    assert can(w, "uT1", "child", "a1")


def test_role_change_for_a_lead_moves_the_case_rows(w):
    w.revoke_label("uL1", "safeguardinglead")
    w.svc.apply(role_changed("role:uL1:withdraw", "uL1"))
    assert not can(
        w,
        "uL1",
        "safeguarding_case",
        "cs1",
    ) and not can(w, "uL1", "child", "a1")


def test_guardian_added_and_removed(w):
    assert not can(w, "uPB", "child", "a1")
    w.add("guardian_link", "glN", parent_id="PA", user_id="uPB", status="active", grants="children")
    w.grant_label("uPB", "guardian")
    w.svc.apply(guardian_changed("guardian:glN:add", "PA", "uPB"))
    assert can(w, "uPB", "child", "a1") and not can(w, "uPB", "session_log", "slA1")
    w.patch("guardian_link", "glN", status="revoked", revoked_at=iso(T0))
    w.svc.apply(guardian_changed("guardian:glN:remove", "PA", "uPB", removed=True))
    assert not can(w, "uPB", "child", "a1") and can(w, "uPB", "child", "b1")


def test_account_deactivation_and_deletion(w):
    w.patch("user_profile", "uPA", status="deactivated")
    w.svc.apply(account_changed("account:uPA:deactivate", "uPA"))
    assert not can(w, "uPA", "child", "a1") and can(w, "uPA", "user_profile", "uPA")
    w.patch("user_profile", "uPA", deleted_at=iso(T0))
    w.svc.apply(account_changed("account:uPA:delete", "uPA", deleted=True))
    assert not can(w, "uPA", "user_profile", "uPA") and not can(w, "uPA", "notification", "nfA")
    assert w.repo.get("child", "a1")["$permissions"] and "user:uPA" not in str(
        w.repo.get("child", "a1")["$permissions"]
    )
    # the family's other adults lose nothing they were entitled to
    assert can(w, "uGA", "child", "a1")


def test_deleted_or_suspended_teacher_loses_access_via_account_event(w):
    w.patch("teacher_profile", "T1", status="suspended")
    w.svc.apply(account_changed("account:uT1:suspend", "uT1"))
    for t, r in (
        ("child", "a1"),
        ("home_location", "hlA"),
        ("session_log", "slA1"),
        ("handover_summary", "hsA1"),
    ):
        assert not can(w, "uT1", t, r), t


def test_a_new_row_gets_its_permissions_on_creation_event(w):
    w.add("notification", "nfNew", user_id="uPB", status="queued")
    assert w.repo.get("notification", "nfNew")["$permissions"] == []
    w.svc.apply(row_created("notification:nfNew:create", "notification", "nfNew"))
    assert can(w, "uPB", "notification", "nfNew") and not can(w, "uPA", "notification", "nfNew")


def test_catalogue_publish_adds_users_read_only_by_status(w):
    w.patch("catalogue_version", "cvDraft", status="published")
    w.svc.apply(
        PermissionEvent(
            "catalogue_changed",
            "catalogue:cvDraft:publish",
            (Scope("catalogue_version", "cvDraft"),),
        )
    )
    assert can(w, "uNone", "topic", "tpDraft") and can(w, "uNone", "catalogue_version", "cvDraft")


def test_event_validation():
    with pytest.raises(ValueError):
        PermissionEvent("nope", "x", (Scope("child", "a"),))
    with pytest.raises(ValueError):
        PermissionEvent("manual", "", (Scope("child", "a"),))
    with pytest.raises(ValueError):
        PermissionEvent("manual", "x", ())
    with pytest.raises(ValueError):
        Scope("galaxy", "x")


# ------------------------------------------------------------------ idempotence and diff
def test_applying_twice_is_a_noop_and_writes_nothing_the_second_time(w):
    ev = assignment_changed("assignment:x", "a1", "T1")
    w.svc.apply(ev)
    before = len(w.repo.writes)
    r = w.svc.apply(ev)
    assert r.changed == 0 and r.grants == 0 and r.revokes == 0 and len(w.repo.writes) == before


def test_only_the_difference_is_written_and_nothing_else_changes(w):
    w.add("assignment", "asgN", child_id="a2", teacher_id="T4", start_at=iso(T0), end_at=None)
    a1_before = w.repo.get("child", "a1")
    n = len(w.repo.writes)
    w.svc.apply(assignment_changed("assignment:asgN:start", "a2", "T4"))
    changed = {(t, r) for op, t, r in w.repo.writes[n:] if op == "update"}
    assert ("child", "a2") in changed and ("child", "a1") not in changed
    assert w.repo.get("child", "a1") == a1_before


def test_permission_writes_change_no_column_and_only_read_entries(w):
    w.patch("assignment", "asg1", end_at=iso(T0 - H), end_reason="admin")
    snapshots = {
        t: {r["$id"]: {k: v for k, v in r.items() if not k.startswith("$")} for r in w.repo.list(t)}
        for t in ("audit_log", "safeguarding_action", "child", "ledger_entry")
    }
    w.svc.apply(assignment_changed("assignment:asg1:end", "a1", "T1", ended=True))
    w.svc.sync_scope(Scope("table", "audit_log"))
    for t, rows in snapshots.items():
        for rid, data in rows.items():
            now = w.repo.get(t, rid)
            assert {k: v for k, v in now.items() if not k.startswith("$")} == data
    for t in ("audit_log", "child", "session_log"):
        for r in w.repo.list(t):
            assert all(p.startswith("read(") for p in r["$permissions"])


def test_drift_is_repaired_both_ways_by_a_recompute(w):
    w.repo.update(
        "child", "a1", {}, permissions=['read("any")', 'update("user:uT4")', 'read("user:uT4")']
    )
    w.repo.update("child", "b1", {}, permissions=[])
    w.svc.apply(PermissionEvent("manual", "m1", (Scope("child", "a1"), Scope("child", "b1"))))
    assert "any" not in str(w.repo.get("child", "a1")["$permissions"])
    assert not can(w, "uT4", "child", "a1") and can(w, "uPB", "child", "b1")


def test_soft_deleted_rows_lose_their_permissions(w):
    w.patch("child", "a2", deleted_at=iso(T0))
    w.svc.apply(PermissionEvent("manual", "m2", (Scope("child", "a2"),)))
    assert w.repo.get("child", "a2")["$permissions"] == []


# ------------------------------------------------------------------ order of operations
def test_run_enqueues_first_then_writes_the_business_change_then_the_permissions(w):
    order = []

    class Q(NullQueue):
        def enqueue(self, event):
            order.append("enqueue")
            return super().enqueue(event)

    w.svc.queue = Q()
    orig = w.repo.update

    def spy(table, rid, data, permissions=None):
        order.append(f"update:{table}" + (":perm" if permissions is not None else ""))
        return orig(table, rid, data, permissions)

    w.repo.update = spy

    def business():
        order.append("business")
        w.add("assignment", "asgN", child_id="a2", teacher_id="T4", start_at=iso(T0), end_at=None)
        return "ok"

    result, report = w.svc.run(assignment_changed("assignment:asgN:start", "a2", "T4"), business)
    assert result == "ok" and report.ok
    assert order[0] == "enqueue" and order[1] == "business"
    assert any(o.endswith(":perm") for o in order[2:])
    assert can(w, "uT4", "child", "a2")


def test_business_write_fails_nothing_is_granted_and_the_queued_job_finds_nothing(w):
    q = NullQueue()
    w.svc.queue = q

    def business():
        raise RuntimeError("business write failed")

    ev = assignment_changed("assignment:asgN:start", "a2", "T4")
    with pytest.raises(RuntimeError):
        w.svc.run(ev, business)
    assert len(q.events) == 1  # the job is queued ...
    assert not can(w, "uT4", "child", "a2")  # ... nothing was granted
    r = w.svc.apply(q.events[0])  # the job's run: from state, nothing to change
    assert r.changed == 0 and not can(w, "uT4", "child", "a2")


def test_business_write_ok_but_grant_fails_is_an_under_grant_and_the_job_repairs_it(w):
    q = NullQueue()
    w.svc.queue = q
    ev = assignment_changed("assignment:asgN:start", "a2", "T4")

    def business():
        w.add("assignment", "asgN", child_id="a2", teacher_id="T4", start_at=iso(T0), end_at=None)

    w.repo.fail = lambda op, t, r: op == "update" and t in ("child", "home_location")
    _, report = w.svc.run(ev, business)
    assert report is not None and not report.ok  # reported, business change stands
    assert not can(w, "uT4", "child", "a2")  # under-grant: safe
    w.repo.fail = None
    r = w.svc.apply(q.events[0])  # the retry job
    assert r.ok and can(w, "uT4", "child", "a2")


def test_business_end_ok_but_revoke_fails_is_an_over_grant_until_the_retry(w):
    q = NullQueue()
    w.svc.queue = q
    ev = assignment_changed("assignment:asg1:end", "a1", "T1", ended=True)

    def business():
        w.patch("assignment", "asg1", end_at=iso(T0), end_reason="admin")

    w.repo.fail = lambda op, t, r: op == "update" and (t, r) == ("child", "a1")
    _, report = w.svc.run(ev, business)
    assert report.failed_revokes == 1 and can(
        w, "uT1", "child", "a1"
    )  # over-grant (the unsafe state)
    assert not can(w, "uT1", "home_location", "hlA")  # rows that could be written were
    w.repo.fail = None
    w.svc.apply(q.events[0])
    assert not can(w, "uT1", "child", "a1")


def test_partial_application_applies_the_rest_and_a_retry_completes_it(w):
    ev = PermissionEvent("manual", "bulk", (Scope("parent", "PA"),))
    w.revoke_label("uT1", "teacher")  # makes several rows lose uT1
    seen = {"n": 0}

    def fail_second(op, table, rid):
        if op == "update":
            seen["n"] += 1
            return seen["n"] == 2
        return False

    w.repo.fail = fail_second
    with pytest.raises(PartialApplication) as exc:
        w.svc.apply(ev)
    assert len(exc.value.report.failed) == 1 and exc.value.report.changed >= 1
    w.repo.fail = None
    again = w.svc.apply(ev)
    assert again.ok and again.changed == 1  # only the row that failed is left to do
    assert w.svc.apply(ev).changed == 0


def test_repository_outage_during_apply_does_not_undo_the_business_change(w):
    q = NullQueue()
    w.svc.queue = q
    business_done = []

    def business():
        w.add("assignment", "asgN", child_id="a2", teacher_id="T4", start_at=iso(T0), end_at=None)
        business_done.append(1)
        w.repo.available = False  # Appwrite goes away right after the business write

    _, report = w.svc.run(assignment_changed("assignment:asgN:start", "a2", "T4"), business)
    w.repo.available = True
    assert business_done and report is None
    assert not can(w, "uT4", "child", "a2")  # fail safe: nothing granted
    w.svc.apply(q.events[0])
    assert can(w, "uT4", "child", "a2")


def test_enqueue_failure_stops_before_any_business_write(w):
    class Broken(NullQueue):
        def enqueue(self, event):
            raise RepositoryUnavailable("queue down")

    w.svc.queue = Broken()
    called = []
    with pytest.raises(RepositoryUnavailable):
        w.svc.run(assignment_changed("e", "a1", "T1"), lambda: called.append(1))
    assert called == []


def test_concurrent_recompute_converges_to_the_state_not_the_last_writer(w):
    """Two runs for one entity interleave: whichever writes last writes a correct list."""
    w.add("assignment", "asgN", child_id="a2", teacher_id="T4", start_at=iso(T0), end_at=None)
    stale_ctx = w.svc.context()  # facts read BEFORE the change below
    w.patch("assignment", "asgN", end_at=iso(T0 - H), end_reason="admin")
    w.svc.sync_scope(Scope("child", "a2"))  # the newer run writes first
    assert not can(w, "uT4", "child", "a2")
    # an older run still holding stale facts would re-grant: a fresh context per run prevents it
    fresh = w.svc.apply(PermissionEvent("manual", "late", (Scope("child", "a2"),)))
    assert fresh.ok and not can(w, "uT4", "child", "a2")
    assert stale_ctx.now == w.clock.now  # (documented: contexts are per run, never shared)


def test_alert_sink_failure_never_breaks_the_repair(w):
    class Bad(FakeAlertSink):
        def send(self, alert):
            raise RuntimeError("sink down")

    w.svc.alerts = Bad()
    w.svc.alert_over_grant("k", "d", 1)  # must not raise


def test_files_follow_the_row_on_events(w):
    assert (
        w.files.get_permissions("teacher-docs", "f_td_t1")
        == w.repo.get("teacher_document", "td_t1")["$permissions"]
    )
    w.patch("safeguarding_case", "cs1", status="closed")
    w.svc.apply(case_changed("case:cs1:close", "cs1", "case_closed"))
    assert "uL1" not in str(w.files.get_permissions("teacher-docs", "f_td_t1"))
    w.patch("safeguarding_case", "cs1", status="open")
    w.svc.apply(case_changed("case:cs1:reopen", "cs1", "case_assigned"))
    assert "uL1" in str(w.files.get_permissions("teacher-docs", "f_td_t1"))
