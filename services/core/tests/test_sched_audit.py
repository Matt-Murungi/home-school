"""S01 (#93) round 2: the generator in the loop with M06's audit layer.

Per-row audit through the AuditedRepository (decision in docs/ops/audit.md "Session generation").
Covers attribution, exactly-once under retries, crash injection over every write including the
audit rows, fail-closed, and the bypass mutation.
"""

import threading
from datetime import timedelta

import pytest

from hs_core.audit import AuditedRepository
from hs_core.audit.context import AuditContext, audit_scope, job_scope
from hs_core.audit.errors import AuditContextMissing, AuditUnavailable
from hs_core.jobs.alerts import FakeAlertSink
from hs_core.jobs.model import JobContext
from hs_core.scheduling import job as sjob
from hs_core.scheduling import rules
from hs_core.scheduling.generator import SessionGenerator
from hs_core.scheduling.leases import LeaseBusy, TeacherLeases
from hs_core.scheduling.writer import SessionWriter
from jobs_helpers import Crash
from sched_helpers import NOW, World, iso, overlapping_pairs

TUE_4PM = (2, 960, 1020)
THU_4PM = (4, 960, 1020)


def unbound(w, **kw):
    kw.setdefault("alert", FakeAlertSink())
    return SessionGenerator(AuditedRepository(w.repo, w.audit), clock=w.clock, **kw)


def job_ctx(run_id="run1", chunk=0):
    return JobContext(
        "session-generate",
        run_id,
        "session-generate:2026-11-30",
        NOW,
        {},
        None,
        chunk,
        threading.Event(),
    )


def intents(w, action="session.generate"):
    return [r for r in w.audit_rows(action=action)]


# ---------------------------------------------------------------- attribution
def test_nightly_job_writes_are_attributed_to_the_system_actor_with_the_job_name():
    w = World()
    aid = w.assignment(w.teacher(), [TUE_4PM])
    g = unbound(w)
    with job_scope(job_ctx()):
        sjob.make_handler(g, page=20, budget_s=30)(job_ctx())
    rows = intents(w)
    assert len(rows) == 4 and len(w.sessions(assignment_id=aid)) == 4
    for r in rows:
        assert r["actor_id"] is None and "system" in r["actor_roles"]
        assert "session-generate" in r["after"]


def test_unbound_generator_without_any_context_refuses_to_write_sessions():
    w = World()
    aid = w.assignment(w.teacher(), [TUE_4PM])
    with pytest.raises(AuditContextMissing):
        unbound(w).generate(aid)
    assert not w.sessions()


def test_generator_refuses_a_repository_without_the_audit_layer():
    w = World()
    with pytest.raises(TypeError):
        SessionGenerator(w.repo, clock=w.clock)  # the raw repository: the bypass


def test_reschedule_and_soft_delete_carry_the_callers_actor():
    w = World()
    aid = w.assignment(w.teacher(), [TUE_4PM])
    g = unbound(w)
    g.generate(aid, ctx=w.ctx)
    s = sorted(w.sessions(assignment_id=aid), key=lambda r: r["scheduled_start_at"])
    parent = AuditContext.for_principal("u_parent", ["parent"], request_id="r1")
    start = w.clock() + timedelta(days=2)
    g.writer.reschedule(s[0]["$id"], start, start + timedelta(hours=1), ctx=parent)
    row = w.audit_rows(action="session.reschedule")[0]
    assert row["actor_id"] == "u_parent" and row["entity_id"] == s[0]["$id"]
    admin = AuditContext.for_principal("u_admin", ["opsadmin"], request_id="r2")
    g.writer.soft_delete(s[1]["$id"], w.clock(), ctx=admin)
    assert w.audit_rows(action="session.soft_delete")[0]["actor_id"] == "u_admin"


def test_cancel_future_is_audited_with_actor_and_required_reason():
    from hs_core.audit.errors import ReasonRequired

    w = World()
    aid = w.assignment(w.teacher(), [TUE_4PM])
    g = unbound(w)
    g.generate(aid, ctx=w.ctx)
    from datetime import date

    b03 = AuditContext.for_principal("u_billing", ["opsadmin"], request_id="r3")
    with pytest.raises(ReasonRequired):
        g.cancel_future(aid, date(2026, 11, 30), " ", ctx=b03)
    g.cancel_future(aid, date(2026, 11, 30), "subscription paused", ctx=b03)
    head = w.audit_rows(action="scheduling.cancel_future")
    assert (
        len(head) == 1
        and head[0]["actor_id"] == "u_billing"
        and head[0]["reason"] == "subscription paused"
    )
    rows = w.audit_rows(action="session.cancel_future")
    assert len(rows) == 4 and {r["actor_id"] for r in rows} == {"u_billing"}


def test_leave_artifacts_are_audited_session_and_cancellation():
    w = World()
    t = w.teacher()
    aid = w.assignment(t, [TUE_4PM])
    from datetime import UTC, datetime

    w.leave(t, datetime(2026, 12, 8, 0, 0, tzinfo=UTC), datetime(2026, 12, 9, 0, 0, tzinfo=UTC))
    unbound(w).generate(aid, ctx=w.ctx)
    assert len(w.audit_rows(action="session.generate_leave")) == 1
    assert len(w.audit_rows(action="cancellation.teacher_leave")) == 1
    assert len(w.audit_rows(action="session.generate")) == 3


# ---------------------------------------------------------------- exactly once under retries
def run_with_crash_at(k, with_audit_crash=True):
    """Crash (BaseException) at the k-th write of the first attempt, then retry the SAME job chunk
    (same effect key -> same audit key base). Returns the world."""
    w = World()
    t = w.teacher()
    aid = w.assignment(t, [TUE_4PM, THU_4PM], trial=True)
    count = {"n": 0}

    def before(op, table, row_id, data):
        if table == "audit_log" and not with_audit_crash:
            return
        count["n"] += 1
        if count["n"] == k:
            raise Crash

    w.repo.before = before
    jc = job_ctx()
    crashed = False
    try:
        with job_scope(jc):
            unbound(w).generate(aid)
    except Crash:
        crashed = True
    w.repo.before = None
    with job_scope(job_ctx()):  # the retried chunk: a NEW context object, same effect key
        unbound(w).generate(aid)
    return w, aid, crashed


def final_state(w):
    return sorted(
        (r["slot_ref"], r["status"], r["is_trial"]) for r in w.sessions() if not r.get("deleted_at")
    )


def test_crash_at_every_write_including_audit_rows_then_retry_is_exactly_once():
    clean = World()
    t = clean.teacher()
    cid = clean.assignment(t, [TUE_4PM, THU_4PM], trial=True)
    with job_scope(job_ctx()):
        unbound(clean).generate(cid)
    expected = final_state(clean)
    assert len(expected) == 8
    total_writes = len(clean.repo.calls) and sum(
        1 for c in clean.repo.calls if c[0] in ("create", "update")
    )
    crashed_any = 0
    for k in range(1, total_writes + 2):
        w, aid, crashed = run_with_crash_at(k)
        crashed_any += crashed
        assert final_state(w) == expected, f"write {k}"
        assert overlapping_pairs(w.repo, "t1") == []
        # exactly one live audit intent per session create: replayed, never doubled
        ids = [
            r["entity_id"]
            for r in w.audit_rows(action="session.generate")
            if '"status":"intent"' in r["after"]
        ]
        assert len(ids) == len(set(ids)), f"duplicate audit intent after crash at write {k}"
        for r in w.sessions():
            assert ids.count(r["$id"]) == 1, (k, r["slot_ref"])
    assert crashed_any > 8  # the injection really crashed at most write positions


def test_a_retried_chunk_after_success_adds_no_audit_rows_and_no_sessions():
    w = World()
    aid = w.assignment(w.teacher(), [TUE_4PM])
    with job_scope(job_ctx()):
        unbound(w).generate(aid)
    n_audit, n_sessions = len(w.audit_rows()), len(w.sessions())
    with job_scope(job_ctx()):
        unbound(w).generate(aid)
    assert (len(w.audit_rows()), len(w.sessions())) == (n_audit, n_sessions)


def test_in_process_retry_of_the_same_create_replays_the_intent():
    w = World()
    aid = w.assignment(w.teacher(), [TUE_4PM])
    g = unbound(w)
    jc = job_ctx()
    state = {"fail": True}

    def before(op, table, row_id, data):
        if op == "create" and table == "session" and state["fail"]:
            state["fail"] = False
            raise Crash

    w.repo.before = before
    with pytest.raises(Crash), job_scope(jc):
        g.generate(aid)
    w.repo.before = None
    with job_scope(job_ctx()):
        g.generate(aid)
    live = [r for r in w.audit_rows(action="session.generate")]
    ids = [r["entity_id"] for r in live]
    assert len(ids) == len(set(ids)) == 4


# ---------------------------------------------------------------- fail closed
def test_audit_store_down_means_no_session_is_written():
    w = World()
    aid = w.assignment(w.teacher(), [TUE_4PM])

    def before(op, table, row_id, data):
        if table == "audit_log":
            raise RuntimeError("audit store unavailable")

    w.repo.before = before
    with pytest.raises(AuditUnavailable), audit_scope(w.ctx):
        unbound(w).generate(aid)
    assert not w.sessions()


def test_audit_store_down_isolated_per_assignment_in_the_job_and_alerted():
    w = World()
    t1, t2 = w.teacher(), w.teacher()
    a1 = w.assignment(t1, [TUE_4PM])
    a2 = w.assignment(t2, [TUE_4PM])
    alerts = FakeAlertSink()
    g = unbound(w, alert=alerts)

    def before(op, table, row_id, data):
        if table == "audit_log":
            raise RuntimeError("down")

    w.repo.before = before
    with job_scope(job_ctx()):
        out = g.run_chunk(None, budget_s=30)
    assert out.failures == 2 and not w.sessions()
    assert len(alerts.sent) == 2
    w.repo.before = None
    with job_scope(job_ctx()):
        g.run_chunk(None, budget_s=30)
    assert w.sessions(assignment_id=a1) and w.sessions(assignment_id=a2)


def test_generate_now_writes_nothing_when_its_own_audit_row_cannot_be_written():
    w = World()
    aid = w.assignment(w.teacher(), [TUE_4PM])

    def before(op, table, row_id, data):
        if table == "audit_log":
            raise RuntimeError("down")

    w.repo.before = before
    admin = AuditContext.for_principal("admin1", ["opsadmin"], request_id="r")
    with pytest.raises(AuditUnavailable):
        unbound(w).generate_now(aid, ctx=admin, reason="manual check")
    assert not w.sessions()


# ---------------------------------------------------------------- the race rollback is audited
def test_the_race_rollback_is_an_audited_change():
    import contextlib

    w = World()
    t = w.teacher()
    aud = AuditedRepository(w.repo, w.audit, ctx=w.ctx)
    leases = TeacherLeases(w.repo, w.clock)
    writer = SessionWriter(aud, leases, w.clock)

    def row(minutes):
        start = rules.parse_utc("2026-12-01T13:00:00.000+00:00") + timedelta(minutes=minutes)
        return {
            "child_id": "c",
            "teacher_id": t,
            "subscription_id": "s",
            "home_location_id": "h",
            "scheduled_start_at": iso(start),
            "scheduled_end_at": iso(start + timedelta(hours=1)),
            "status": "scheduled",
            "status_changed_at": iso(NOW),
        }

    first = writer.create(row(0))
    late = w.repo.create("session", "late", rules.with_session_keys(row(20)))  # a rogue writer
    with pytest.raises(rules.SessionConflict), contextlib.suppress(LeaseBusy):
        writer._recheck_or_rollback(late, w.clock())
    assert w.audit_rows(action="session.race_rollback")[0]["entity_id"] == "late"
    assert not w.repo.get("session", first["$id"]).get("deleted_at")


# ------------------------------------------------------ mutation: bypassing the audited repo
def test_mutation_a_session_write_that_bypasses_the_audited_repo_leaves_no_audit_row():
    """The detector: through the audited repository every session create has an audit row; a writer
    built on the raw repository does not. (The generator refuses the raw one; this shows what the
    refusal and M06's tripwire protect.)"""
    w = World()
    t = w.teacher()
    clock = w.clock
    row = {
        "child_id": "c",
        "teacher_id": t,
        "subscription_id": "s",
        "home_location_id": "h",
        "scheduled_start_at": "2026-12-01T13:00:00.000+00:00",
        "scheduled_end_at": "2026-12-01T14:00:00.000+00:00",
        "status": "scheduled",
        "status_changed_at": iso(NOW),
    }
    raw_writer = SessionWriter(w.repo, TeacherLeases(w.repo, clock), clock)
    raw_writer.create(dict(row))
    assert not w.audit_rows(action="session.create")  # bypass: a session with no audit row
    audited_writer = SessionWriter(w.audited, TeacherLeases(w.repo, clock), clock)
    audited_writer.create(
        dict(
            row,
            scheduled_start_at="2026-12-02T13:00:00.000+00:00",
            scheduled_end_at="2026-12-02T14:00:00.000+00:00",
        )
    )
    assert len(w.audit_rows(action="session.create")) == 1


def test_tripwire_flags_a_literal_session_write_on_a_raw_receiver(tmp_path):
    from hs_core.audit.tripwire import find_unaudited_writes

    (tmp_path / "bad.py").write_text('def f(repo):\n    repo.create("session", "x", {})\n')
    (tmp_path / "ok.py").write_text(
        'def f(audited_repo):\n    audited_repo.create("session", "x", {})\n'
    )
    found = find_unaudited_writes(tmp_path)
    assert len(found) == 1 and "bad.py" in found[0]
