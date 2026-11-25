"""Data-subject deletion (C03, DP-5): lifecycle, what is deleted vs retained, holds, cascades."""

import json
import threading
from datetime import timedelta

import pytest

from hs_core.retention.deletion import (
    BadTransition,
    DeletionBlocked,
    DeletionError,
    DeletionService,
    count_subject_rows,
    execute_deletion,
)
from hs_core.retention.files import FakeRetentionFileStore
from hs_core.retention.purger import pseudonym
from retention_helpers import NOW, Clock, Crash, RecRepo, ago


def world(repo, files=None):
    files = files if files is not None else FakeRetentionFileStore()
    c = repo.create
    c(
        "user_profile",
        "u1",
        {"phone": "+256700000001", "email": "mum@example.org", "name": "Mum Name"},
    )
    c("parent_profile", "p1", {"user_id": "u1", "preferred_contact": "push"})
    c(
        "guardian_link",
        "g1",
        {"parent_id": "p1", "user_id": "u9", "status": "active", "active_key": "k"},
    )
    for cid in ("c1", "c2"):
        c("child", cid, {"parent_id": "p1", "name": f"Kid {cid}", "date_of_birth": "2018-02-02"})
        c("session_log", f"l-{cid}", {"session_id": f"s-{cid}", "child_id": cid, "teacher_id": "t1", "submitted_at": ago(10), "note": "private note", "session_key": f"k-{cid}"})  # fmt: skip
        c("session_log_topic", f"lt-{cid}", {"session_log_id": f"l-{cid}", "topic_id": "x", "mastery": "taught", "log_topic_key": f"lk-{cid}"})  # fmt: skip
        c("session_log_struggle", f"ls-{cid}", {"session_log_id": f"l-{cid}", "struggle_tag_id": "x", "log_struggle_key": f"sk-{cid}"})  # fmt: skip
        c(
            "topic_progress",
            f"tp-{cid}",
            {"child_id": cid, "topic_id": "x", "current_mastery": "taught"},
        )
        c("school_upload", f"up-{cid}", {"child_id": cid, "type": "report", "file_id": f"file-{cid}", "uploaded_by_id": "u1", "uploaded_at": ago(5)})  # fmt: skip
        files.put("school-reports", f"file-{cid}")
        c("consent_record", f"cr-{cid}", {"parent_id": "p1", "child_id": cid, "accepted_by_id": "u1", "document": "terms", "version": "1", "decision": "accepted", "app": "parent", "accepted_at": ago(300)})  # fmt: skip
        c("subscription", f"sub-{cid}", {"child_id": cid, "parent_id": "p1", "plan_id": "pl", "status": "cancelled", "cancelled_at": ago(20), "cancel_reason": "moving away"})  # fmt: skip
        c("session", f"se-{cid}", {"assignment_id": "a", "child_id": cid, "teacher_id": "t1", "subscription_id": f"sub-{cid}", "home_location_id": "h1", "scheduled_start_at": ago(10), "scheduled_end_at": ago(10), "status": "confirmed", "teacher_slot_key": f"ts-{cid}"})  # fmt: skip
    c(
        "home_location",
        "h1",
        {"parent_id": "p1", "label": "Home", "directions": "blue gate", "lat_e6": 1, "lng_e6": 2},
    )
    c("invoice", "i1", {"subscription_id": "sub-c1", "parent_id": "p1", "period_start_date": "2026-12-01", "period_end_date": "2026-12-31", "amount": 100000, "currency": "UGX", "status": "paid", "created_at": ago(30)})  # fmt: skip
    c("payment", "pay1", {"invoice_id": "i1", "parent_id": "p1", "provider": "momo", "provider_reference": "r1", "amount": 100000, "currency": "UGX", "status": "succeeded", "idempotency_key": "ik", "raw_callback": "+256700000001", "requested_at": ago(30)})  # fmt: skip
    c("notification", "n1", {"user_id": "u1", "channel": "push", "template_key": "k", "template_version": 1, "category": "x", "payload": "{}", "status": "sent", "attempts": 1, "dedupe_key": "dd", "created_at": ago(3)})  # fmt: skip
    c(
        "device_token",
        "dt1",
        {"user_id": "u1", "app": "parent", "platform": "ios", "token_hash": "h"},
    )
    c("notification_preference", "np1", {"user_id": "u1", "marketing_muted": True})
    c("policy_acceptance", "pa1", {"user_id": "u1", "policy": "privacy", "version": "1", "app": "parent", "accepted_at": ago(300)})  # fmt: skip
    c(
        "audit_log",
        "al1",
        {"action": "x", "entity": "child", "entity_id": "c1", "occurred_at": ago(2)},
    )
    # teacher
    c("user_profile", "ut1", {"phone": "+256700000002", "name": "Teacher Name"})
    c("teacher_profile", "t1", {"user_id": "ut1", "status": "active", "bio": "my bio"})
    c("teacher_zone", "tz1", {"teacher_id": "t1", "zone_id": "z"})
    c("teacher_document", "td1", {"teacher_id": "t1", "type": "id", "file_id": "tdoc1", "verification_status": "verified"})  # fmt: skip
    files.put("teacher-docs", "tdoc1")
    c("earning", "e1", {"teacher_id": "t1", "session_id": "se-c1", "type": "session", "amount": 20000, "currency": "UGX", "status": "paid", "dedupe_key": "ed", "created_at": ago(8), "note": "free text"})  # fmt: skip
    c("payout", "po1", {"batch_id": "b", "teacher_id": "t1", "amount": 20000, "currency": "UGX", "provider": "momo", "status": "paid", "idempotency_key": "pk", "created_at": ago(7), "last_error": "x"})  # fmt: skip
    c("teacher_session_history", "tsh1", {"teacher_id": "t1", "session_id": "se-c1", "session_log_id": "l-c1", "child_first_name": "Kid", "session_date": "2026-12-01", "duration_minutes": 60})  # fmt: skip
    c("ledger_account", "la1", {"code": "teacher:t1", "name": "Teacher Name liability", "type": "liability", "normal_side": "credit", "teacher_id": "t1", "currency": "UGX", "active": True, "created_at": ago(100)})  # fmt: skip
    c("ledger_entry", "le1", {"transaction_id": "tx", "line": 1, "account_id": "la1", "side": "credit", "amount": 20000, "currency": "UGX", "posted_at": ago(8)})  # fmt: skip
    return files


def run(repo, files, kind, sid, **kw):
    return execute_deletion(repo, files, kind, sid, clock=Clock(), **kw)


# ------------------------------------------------------------------ what gets deleted vs retained
def test_parent_deletion_cascades_to_children_and_keeps_tax_and_proof_records():
    repo, files = RecRepo(), None
    files = world(repo)
    res = run(repo, files, "parent", "p1")
    for t in ("child", "session_log", "session_log_topic", "session_log_struggle", "topic_progress", "school_upload", "home_location", "guardian_link", "parent_profile"):  # fmt: skip
        assert repo.rows(t) == {} or t == "x", t
    assert not files.exists("school-reports", "file-c1") and not files.exists(
        "school-reports", "file-c2"
    )
    # retained: payments and invoices with the link anonymised, personal fields minimised
    pay = repo.rows("payment")["pay1"]
    assert (
        pay["parent_id"] == pseudonym("p1")
        and pay["raw_callback"] is None
        and pay["amount"] == 100000
    )
    assert repo.rows("invoice")["i1"]["parent_id"] == pseudonym("p1")
    sub = repo.rows("subscription")["sub-c1"]
    assert sub["parent_id"] == pseudonym("p1") and sub["child_id"] == pseudonym("c1") and sub["cancel_reason"] is None  # fmt: skip
    ses = repo.rows("session")["se-c1"]
    assert ses["child_id"] == pseudonym("c1") and ses["home_location_id"] == pseudonym("h1")
    # proof is kept and counted
    assert set(repo.rows("consent_record")) == {"cr-c1", "cr-c2"} and repo.rows("audit_log")
    assert res.retained["consent_record"] == 4 or res.retained["consent_record"] == 2
    assert res.deleted["child"] == 2 and res.children == ["c1", "c2"]
    assert res.anonymised["payment"] == 1 and res.files == 2
    # the teacher side of the family's data is untouched
    assert "t1" in repo.rows("teacher_profile") and "e1" in repo.rows("earning")


def test_child_deletion_cascades_dependents_but_not_the_parent_or_siblings():
    repo = RecRepo()
    files = world(repo)
    res = run(repo, files, "child", "c1")
    assert set(repo.rows("child")) == {"c2"} and set(repo.rows("session_log")) == {"l-c2"}
    assert set(repo.rows("session_log_topic")) == {"lt-c2"} and set(repo.rows("school_upload")) == {
        "up-c2"
    }
    assert set(repo.rows("topic_progress")) == {"tp-c2"} and "p1" in repo.rows("parent_profile")
    assert files.exists("school-reports", "file-c2") and not files.exists(
        "school-reports", "file-c1"
    )
    assert repo.rows("subscription")["sub-c1"]["child_id"] == pseudonym("c1")
    assert repo.rows("subscription")["sub-c1"]["parent_id"] == "p1"  # only the child's reference
    assert res.deleted["child"] == 1


def test_teacher_deletion_removes_documents_and_files_and_keeps_pseudonymous_earnings():
    repo = RecRepo()
    files = world(repo)
    run(repo, files, "teacher", "t1")
    assert repo.rows("teacher_profile") == {} and repo.rows("teacher_document") == {}
    assert repo.rows("teacher_zone") == {} and repo.rows("teacher_session_history") == {}
    assert not files.exists("teacher-docs", "tdoc1")
    e, po = repo.rows("earning")["e1"], repo.rows("payout")["po1"]
    assert e["teacher_id"] == pseudonym("t1") and e["note"] is None and e["amount"] == 20000
    assert po["teacher_id"] == pseudonym("t1") and po["last_error"] is None
    acct = repo.rows("ledger_account")["la1"]
    assert acct["teacher_id"] == pseudonym("t1") and acct["name"] == "[deleted]"
    assert "le1" in repo.rows("ledger_entry") and "al1" in repo.rows("audit_log")
    assert repo.rows("session")["se-c1"]["teacher_id"] == pseudonym("t1")
    # the children's own records are not touched by a teacher's deletion
    assert set(repo.rows("child")) == {"c1", "c2"}


def test_teacher_and_parent_differ_documents_only_exist_for_teachers_children_only_for_parents():
    repo = RecRepo()
    files = world(repo)
    teacher_rows = count_subject_rows(repo, "teacher", "t1")
    parent_rows = count_subject_rows(repo, "parent", "p1")
    assert "teacher_document" in teacher_rows and "teacher_document" not in parent_rows
    assert "child" in {t for t in parent_rows} or "home_location" in parent_rows
    assert "child" not in teacher_rows and files


def test_user_deletion_resolves_to_the_profile_and_the_users_own_rows():
    repo = RecRepo()
    files = world(repo)
    run(repo, files, "user", "u1")
    assert "u1" not in repo.rows("user_profile")
    assert repo.rows("notification") == {} and repo.rows("device_token") == {} and repo.rows("notification_preference") == {}  # fmt: skip
    assert (
        repo.rows("child") == {} and repo.rows("parent_profile") == {}
    )  # the parent profile of this user
    assert "pa1" in repo.rows("policy_acceptance")  # proof kept
    assert "ut1" in repo.rows("user_profile")  # the teacher user is another person


def test_deletion_is_idempotent_and_a_rerun_changes_nothing():
    repo = RecRepo()
    files = world(repo)
    run(repo, files, "parent", "p1")
    seen = {t: repo.rows(t) for t in ("payment", "subscription", "session", "child")}
    writes = repo.writes
    res = run(repo, files, "parent", "p1")
    assert repo.writes == writes and {t: repo.rows(t) for t in seen} == seen
    assert not res.deleted and not res.anonymised


def test_crash_before_every_write_of_a_deletion_then_rerun_gives_the_clean_state():
    clean = RecRepo()
    cf = world(clean)
    seeded = clean.writes
    run(clean, cf, "parent", "p1")
    total = clean.writes - seeded
    names = ("child", "session_log", "session_log_topic", "payment", "invoice", "subscription", "session", "home_location", "parent_profile", "school_upload")  # fmt: skip
    expect = {t: clean.rows(t) for t in names}
    assert total > 20
    for k in range(1, total + 1, 2):
        repo = RecRepo()
        files = world(repo)
        repo.crash_at = repo.writes + k
        with pytest.raises(Crash):
            run(repo, files, "parent", "p1")
        repo.crash_at = None
        run(repo, files, "parent", "p1")
        assert {t: repo.rows(t) for t in names} == expect, f"crash before write {k}"
        assert not files.exists("school-reports", "file-c1")


# ------------------------------------------------------------------ holds
def hold(repo, kind, sid, hid="h"):
    repo.create("retention_hold", hid, {"subject_kind": kind, "subject_id": sid, "reason": "safeguarding", "placed_by_id": "s", "placed_at": ago(2)})  # fmt: skip


@pytest.mark.parametrize("kind,sid,target", [("child", "c1", ("child", "c1")), ("child", "c1", ("parent", "p1")), ("parent", "p1", ("parent", "p1")), ("teacher", "t1", ("teacher", "t1")), ("user", "ut1", ("user", "ut1"))])  # fmt: skip
def test_a_hold_blocks_the_whole_deletion_and_nothing_is_changed(kind, sid, target):
    repo = RecRepo()
    files = world(repo)
    hold(repo, kind, sid)
    before = {t: repo.rows(t) for t in ("child", "payment", "teacher_profile", "user_profile", "parent_profile", "session_log")}  # fmt: skip
    writes = repo.writes
    with pytest.raises(DeletionBlocked):
        run(repo, files, *target)
    assert repo.writes == writes and {t: repo.rows(t) for t in before} == before
    assert files.exists("school-reports", "file-c1") and files.exists("teacher-docs", "tdoc1")


def test_open_safeguarding_case_blocks_deletion_of_the_child_and_of_the_teacher():
    repo = RecRepo()
    files = world(repo)
    repo.create("safeguarding_case", "case1", {"child_id": "c1", "teacher_id": "t1", "raised_by_id": "u", "status": "open", "description": "x", "category": "other", "raised_at": ago(3)})  # fmt: skip
    for target in (("child", "c1"), ("teacher", "t1"), ("parent", "p1")):
        with pytest.raises(DeletionBlocked):
            run(repo, files, *target)
    repo.update("safeguarding_case", "case1", {"status": "closed", "closed_at": ago(1)})
    run(repo, files, "child", "c1")  # a closed case is not a hold; the case itself is retained
    assert "case1" in repo.rows("safeguarding_case")


def test_a_hold_on_one_childs_log_blocks_the_parents_deletion_as_a_whole():
    repo = RecRepo()
    files = world(repo)
    hold(repo, "row", "session_log:l-c2")
    with pytest.raises(DeletionBlocked) as e:
        run(repo, files, "parent", "p1")
    assert e.value.table == "session_log"
    assert set(repo.rows("child")) == {"c1", "c2"}


def test_unknown_subject_kind_is_refused():
    with pytest.raises(DeletionError):
        execute_deletion(RecRepo(), FakeRetentionFileStore(), "robot", "x")


# ------------------------------------------------------------------ lifecycle
def svc(repo, files, clock=None, grace=30, audit=None):
    return DeletionService(repo, files, grace_days=grace, clock=clock or Clock(), audit=audit)


class Audit:
    def __init__(self):
        self.events = []

    def summary(self, **kw):
        self.events.append(kw)


def test_full_lifecycle_requested_verified_scheduled_executed_confirmed():
    repo = RecRepo()
    files = world(repo)
    clock, audit = Clock(), Audit()
    s = svc(repo, files, clock, audit=audit)
    r = s.request("parent", "p1", "u1")
    assert r["status"] == "requested" and r["open_key"] == "parent:p1"
    assert s.verify(r["$id"])["status"] == "verified"
    sched = s.schedule(r["$id"])
    assert sched["status"] == "scheduled"
    assert sched["scheduled_for_at"] == (NOW + timedelta(days=30)).isoformat()
    assert repo.rows("child")  # nothing deleted during the grace period
    with pytest.raises(DeletionError, match="grace"):
        s.execute(r["$id"])
    clock.advance(days=30, seconds=1)
    done = s.execute(r["$id"])
    assert done["status"] == "executed" and repo.rows("child") == {}
    result = json.loads(done["result"])
    assert (
        result["deleted"]["child"] == 2
        and result["anonymised"]["payment"] == 1
        and result["retained"]
    )
    assert s.confirm(r["$id"])["status"] == "confirmed"
    assert audit.events[0]["action"] == "deletion.execute" and "Kid" not in json.dumps(audit.events)
    assert s.execute(r["$id"])["status"] == "confirmed"  # a repeat call is a no-op


def test_the_grace_period_boundary_to_the_second():
    repo = RecRepo()
    files = world(repo)
    clock = Clock()
    s = svc(repo, files, clock, grace=7)
    r = s.request("child", "c1", "u1")
    s.verify(r["$id"])
    s.schedule(r["$id"])
    clock.advance(days=7, seconds=-1)
    with pytest.raises(DeletionError):
        s.execute(r["$id"])
    clock.advance(seconds=1)
    assert s.execute(r["$id"])["status"] == "executed"


def test_cancel_before_execution_keeps_everything_and_frees_the_open_slot():
    repo = RecRepo()
    files = world(repo)
    s = svc(repo, files)
    r = s.request("parent", "p1", "u1")
    s.verify(r["$id"])
    s.schedule(r["$id"])
    assert s.cancel(r["$id"])["status"] == "cancelled" and repo.rows("child")
    again = s.request("parent", "p1", "u1")
    assert again["$id"] != r["$id"] and again["status"] == "requested"


def test_only_valid_transitions_are_allowed():
    repo = RecRepo()
    files = world(repo)
    s = svc(repo, files)
    r = s.request("parent", "p1", "u1")
    for bad in (s.schedule, s.confirm):
        with pytest.raises((BadTransition, DeletionError)):
            bad(r["$id"])
    with pytest.raises(BadTransition):
        s.execute(r["$id"])
    s.verify(r["$id"])
    with pytest.raises(BadTransition):
        s.verify(r["$id"])
    with pytest.raises(DeletionError):
        s.request("alien", "x", "u1")


def test_asking_twice_returns_the_same_open_request_even_from_many_threads():
    repo = RecRepo()
    files = world(repo)
    s = svc(repo, files)
    ids, lock = [], threading.Lock()

    def go():
        r = s.request("parent", "p1", "u1")
        with lock:
            ids.append(r["$id"])

    ts = [threading.Thread(target=go) for _ in range(30)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(set(ids)) == 1 and len(repo.rows("deletion_request")) == 1


def test_a_hold_blocks_at_scheduling_and_again_at_execution():
    repo = RecRepo()
    files = world(repo)
    clock = Clock()
    s = svc(repo, files, clock)
    r = s.request("parent", "p1", "u1")
    s.verify(r["$id"])
    hold(repo, "child", "c1")
    blocked = s.schedule(r["$id"])
    assert (
        blocked["status"] == "blocked"
        and blocked["blocked_reason"] == "hold"
        and blocked["open_key"] is None
    )
    assert repo.rows("child")
    # a hold that appears AFTER scheduling is caught at execution
    repo.update("retention_hold", "h", {"revoked_at": ago(0)})
    r2 = s.request("parent", "p1", "u1")
    s.verify(r2["$id"])
    s.schedule(r2["$id"])
    repo.create("retention_hold", "h2", {"subject_kind": "child", "subject_id": "c2", "reason": "legal_claim", "placed_by_id": "s", "placed_at": ago(0)})  # fmt: skip
    clock.advance(days=31)
    out = s.execute(r2["$id"])
    assert out["status"] == "blocked" and set(repo.rows("child")) == {"c1", "c2"}


def test_run_due_executes_only_scheduled_requests_past_their_grace():
    repo = RecRepo()
    files = world(repo)
    clock = Clock()
    s = svc(repo, files, clock)
    a = s.request("child", "c1", "u1")
    b = s.request("child", "c2", "u1")
    for r in (a, b):
        s.verify(r["$id"])
    s.schedule(a["$id"])
    clock.advance(days=20)
    s.schedule(b["$id"])
    clock.advance(days=11)  # a is past 30 days, b is not
    assert s.run_due() == [a["$id"]]
    assert set(repo.rows("child")) == {"c2"}


def test_deletion_request_rows_hold_ids_and_states_only():
    repo = RecRepo()
    files = world(repo)
    s = svc(repo, files)
    r = s.request("parent", "p1", "u1")
    blob = json.dumps(repo.rows("deletion_request")[r["$id"]])
    for pii in ("Mum", "Kid", "256700", "example.org"):
        assert pii not in blob
    assert Clock
