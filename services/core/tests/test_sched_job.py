"""S01 (#93): nightly `session-generate` job (F04 runner), admin generate-now endpoint, settings."""

import logging
import threading
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from hs_core.auth import FakeTokenVerifier
from hs_core.jobs.alerts import FakeAlertSink
from hs_core.jobs.model import Continue, Done, JobContext
from hs_core.jobs.runner import Runner
from hs_core.jobs.schedule import JOBS
from hs_core.main import create_app
from hs_core.scheduling import job as sjob
from hs_core.scheduling.audit import FakeAuditSink
from hs_core.scheduling.generator import SessionGenerator
from hs_core.settings import Settings
from jobs_helpers import Crash
from sched_helpers import NOW, UniqueRepo, World, snapshot

TUE_4PM = (2, 960, 1020)
THU_4PM = (4, 960, 1020)


def gen_for(w, **kw):
    kw.setdefault("alert", FakeAlertSink())
    kw.setdefault("audit", FakeAuditSink())
    return SessionGenerator(w.repo, clock=w.clock, **kw)


def ctx(cursor=None, chunk=0, cancelled=None):
    return JobContext(
        "session-generate",
        "run1",
        "session-generate:x",
        NOW,
        {},
        cursor,
        chunk,
        cancelled or threading.Event(),
    )


def many(w, n, teachers=5):
    ts = [w.teacher() for _ in range(teachers)]
    out = []
    for i in range(n):
        wd = 1 + i % 7
        out.append(w.assignment(ts[i % teachers], [(wd, 360 + (i // 7) * 70, 420 + (i // 7) * 70)]))
    return out


# ---------------------------------------------------------------- registration and schedule
def test_job_is_in_the_one_schedule_list_and_runs_nightly_at_01_00_kampala():
    spec = next(j for j in JOBS if j.name == "session-generate")
    assert spec.schedule.every == timedelta(days=1)
    periods = spec.schedule.due_periods(
        datetime(2026, 11, 29, tzinfo=UTC), datetime(2026, 12, 2, tzinfo=UTC)
    )
    assert [p.hour for p in periods] == [22, 22, 22]  # 01:00 EAT = 22:00 UTC the day before
    assert spec.timeout <= timedelta(seconds=10) and spec.max_attempts >= 2


def test_job_name_is_unique_and_handler_refuses_to_run_unbound():
    assert len([j for j in JOBS if j.name == "session-generate"]) == 1
    sjob._bound.clear()
    with pytest.raises(RuntimeError):
        sjob.session_generate(ctx())


# ---------------------------------------------------------------- handler behaviour
def test_chunks_cover_every_active_subscription_exactly_once_and_complete():
    w = World()
    ids = many(w, 45)
    g = gen_for(w)
    handler = sjob.make_handler(g, page=20, budget_s=30)
    cursor, chunks = None, 0
    while True:
        out = handler(ctx(cursor, chunks))
        chunks += 1
        if isinstance(out, Done):
            break
        assert isinstance(out, Continue)
        cursor = out.cursor
    assert chunks == 3
    assert {s["assignment_id"] for s in w.sessions()} == set(ids)


def test_a_zero_budget_still_makes_progress_one_subscription_per_chunk():
    w = World()
    many(w, 5, teachers=5)
    handler = sjob.make_handler(gen_for(w), page=20, budget_s=0)
    cursor, chunks = None, 0
    while chunks < 20:
        out = handler(ctx(cursor, chunks))
        chunks += 1
        if isinstance(out, Done):
            break
        cursor = out.cursor
    assert isinstance(out, Done) and chunks >= 5


def test_cancel_flag_stops_the_chunk_before_the_next_assignment_and_resumes_from_the_cursor():
    w = World()
    ids = many(w, 6)
    g = gen_for(w)
    ev = threading.Event()
    ev.set()
    out = sjob.make_handler(g, page=20, budget_s=30)(ctx(None, 0, ev))
    assert isinstance(out, Continue) and out.cursor is None and out.items == 0
    assert not w.sessions()
    done = sjob.make_handler(g, page=20, budget_s=30)(ctx(out.cursor, 1))
    assert isinstance(done, Done) and {s["assignment_id"] for s in w.sessions()} == set(ids)


def test_cancel_mid_assignment_leaves_no_partial_state_that_a_rerun_cannot_finish():
    w = World()
    t = w.teacher()
    aid = w.assignment(t, [TUE_4PM, THU_4PM])
    ev = threading.Event()
    seen = []

    def before(op, table, row_id, data):
        if op == "create" and table == "session":
            seen.append(1)
            if len(seen) == 2:
                ev.set()

    w.repo.before = before
    g = gen_for(w)
    r = g.generate(aid, cancelled=ev)
    assert r.status == "cancelled" and 1 <= len(w.sessions()) < 8
    w.repo.before = None
    g.generate(aid)
    assert len(w.sessions()) == 8


def test_a_failing_assignment_does_not_block_the_others_and_raises_an_alert_and_an_issue():
    w = World()
    bad_t, good_t = w.teacher(), w.teacher()
    bad = w.assignment(bad_t, [TUE_4PM])
    good = w.assignment(good_t, [TUE_4PM])
    alerts = FakeAlertSink()
    g = gen_for(w, alert=alerts)

    def before(op, table, row_id, data):
        if op == "create" and table == "session" and data["teacher_id"] == bad_t:
            raise RuntimeError("boom with secret-looking detail 0700123456")

    w.repo.before = before
    out = sjob.make_handler(g, page=20, budget_s=30)(ctx())
    assert isinstance(out, Done)
    assert w.sessions(assignment_id=good) and not w.sessions(assignment_id=bad)
    assert [a.kind for a in alerts.sent] == ["session_generation_failed"]
    a = alerts.sent[0]
    assert a.job == "session-generate" and bad in a.dedupe_key and "0700123456" not in a.error
    issue = w.issues(assignment_id=bad, kind="generation_error")
    assert len(issue) == 1 and "0700123456" not in (issue[0].get("detail") or "")
    # the next night: still failing -> still ONE open issue and the alert hook fires again only per
    # run
    sjob.make_handler(g, page=20, budget_s=30)(ctx())
    assert len(w.issues(assignment_id=bad, kind="generation_error")) == 1
    w.repo.before = None
    sjob.make_handler(g, page=20, budget_s=30)(ctx())
    assert (
        w.sessions(assignment_id=bad)
        and w.issues(assignment_id=bad, kind="generation_error")[0]["status"] == "resolved"
    )


def test_a_crash_baseexception_is_not_swallowed_by_the_isolation():
    w = World()
    aid = w.assignment(w.teacher(), [TUE_4PM])

    def before(op, table, row_id, data):
        if op == "create" and table == "session":
            raise Crash

    w.repo.before = before
    with pytest.raises(Crash):
        sjob.make_handler(gen_for(w), page=20, budget_s=30)(ctx())
    w.repo.before = None
    assert (
        gen_for(w).generate(aid).created == 4
    )  # the lease row is left to expire; creation resumes


def test_busy_teacher_defers_that_assignment_only():
    w = World()
    t1, t2 = w.teacher(), w.teacher()
    a1 = w.assignment(t1, [TUE_4PM])
    a2 = w.assignment(t2, [TUE_4PM])
    g = gen_for(w)
    g.leases.acquire(t1)  # another worker holds t1
    out = sjob.make_handler(g, page=20, budget_s=30)(ctx())
    assert isinstance(out, Done)
    assert not w.sessions(assignment_id=a1) and w.sessions(assignment_id=a2)
    assert g.generate(a1).status == "deferred"


def test_chunk_log_has_ids_and_counts_only(caplog):
    w = World()
    many(w, 3)
    with caplog.at_level(logging.INFO, logger="hs_core.scheduling"):
        sjob.make_handler(gen_for(w), page=20, budget_s=30)(ctx())
    rec = next(r for r in caplog.records if r.getMessage() == "session generation chunk")
    assert (
        rec.assignments == 3
        and rec.rows_created > 0
        and rec.failures == 0
        and rec.max_lag_days >= 0
    )
    assert rec.run_id == "run1" and rec.deferred == 0 and rec.conflicts == 0


def test_end_to_end_through_the_runner_two_nights_no_duplicates_and_each_night_extends():
    w = World()
    ids = many(w, 12)
    g = gen_for(w)
    spec = next(j for j in JOBS if j.name == "session-generate")
    from dataclasses import replace

    spec = replace(spec, handler=sjob.make_handler(g, page=5, budget_s=30))
    runner = Runner(w.repo, [spec], alert=FakeAlertSink(), clock=w.clock)
    w.clock.now = datetime(2026, 11, 29, 22, 5, tzinfo=UTC)  # 01:05 EAT Mon 30 Nov
    for _ in range(6):
        runner.tick()
    first = snapshot(w.repo)
    assert {s["assignment_id"] for s in w.sessions()} == set(ids)
    for _ in range(3):  # extra ticks in the same period: nothing happens
        runner.tick()
    assert snapshot(w.repo) == first
    w.clock.advance(days=1)
    for _ in range(6):
        runner.tick()
    second = snapshot(w.repo)
    assert set(first) <= set(second) and len(second) >= len(first)
    w.clock.advance(minutes=1)
    for _ in range(3):
        runner.tick()
    assert snapshot(w.repo) == second


# ---------------------------------------------------------------- admin endpoint
def app_for(w, g):
    v = FakeTokenVerifier()
    v.add("admin", "u_admin", ["opsadmin"])
    v.add("parent", "u_p", ["parent"])
    s = Settings(_env_file=None, hs_env="dev")
    return TestClient(create_app(settings=s, repository=w.repo, verifier=v, scheduler=g))


def test_admin_generate_now_requires_ops_admin_a_reason_and_audits():
    w = World()
    aid = w.assignment(w.teacher(), [TUE_4PM])
    g = gen_for(w)
    c = app_for(w, g)
    url = "/admin/scheduling/generate-now"
    assert c.post(url, json={"assignment_id": aid, "reason": "check"}).status_code == 401
    assert (
        c.post(
            url,
            json={"assignment_id": aid, "reason": "check"},
            headers={"Authorization": "Bearer parent"},
        ).status_code
        == 403
    )
    h = {"Authorization": "Bearer admin"}
    for body in (
        {"assignment_id": aid},
        {"assignment_id": aid, "reason": ""},
        {"assignment_id": aid, "reason": "  "},
        {"reason": "no assignment"},
    ):
        assert c.post(url, json=body, headers=h).status_code == 422, body
    assert not w.sessions() and not g.audit.entries
    r = c.post(url, json={"assignment_id": aid, "reason": "parent called, start early"}, headers=h)
    assert r.status_code == 200
    out = r.json()
    assert out["created"] == 4 and out["status"] == "generated" and out["assignment_id"] == aid
    assert (
        g.audit.entries[0].actor_id == "u_admin"
        and g.audit.entries[0].reason == "parent called, start early"
    )
    assert (
        c.post(
            url, json={"assignment_id": "missing", "reason": "typo in id"}, headers=h
        ).status_code
        == 404
    )


def test_admin_generate_now_can_reconcile_from_a_date():
    w = World()
    aid = w.assignment(w.teacher(), [(1, 960, 1020), THU_4PM], per_week=2)
    g = gen_for(w)
    g.generate(aid)
    w.repo.update(
        "subscription", w.repo.get("assignment", aid)["subscription_id"], {"plan_id": w.plan(1)}
    )
    c = app_for(w, g)
    r = c.post(
        "/admin/scheduling/generate-now",
        json={"assignment_id": aid, "reason": "plan change check", "reconcile_from": "2026-12-07"},
        headers={"Authorization": "Bearer admin"},
    )
    assert r.status_code == 200 and r.json()["pruned"] == 3
    bad = c.post(
        "/admin/scheduling/generate-now",
        json={"assignment_id": aid, "reason": "bad date xx", "reconcile_from": "yesterday"},
        headers={"Authorization": "Bearer admin"},
    )
    assert bad.status_code == 422


# ---------------------------------------------------------------- settings and wiring
def test_settings_defaults_and_ranges():
    s = Settings(_env_file=None)
    assert (
        s.session_gen_horizon_days,
        s.session_gen_lease_ttl_s,
        s.session_gen_lock_wait_s,
        s.session_gen_chunk_budget_s,
    ) == (28, 30, 3, 6)
    assert s.problems() == []
    for name, bad in (
        ("session_gen_horizon_days", 0),
        ("session_gen_horizon_days", 400),
        ("session_gen_lease_ttl_s", 1),
        ("session_gen_lock_wait_s", 99),
        ("session_gen_chunk_budget_s", 20),
    ):
        assert any(name.upper() in p for p in Settings(_env_file=None, **{name: bad}).problems()), (
            name
        )


def test_app_builds_the_scheduler_from_settings_and_binds_the_job():
    repo = UniqueRepo()
    s = Settings(
        _env_file=None, hs_env="dev", session_gen_horizon_days=14, session_gen_lease_ttl_s=45
    )
    app = create_app(settings=s, repository=repo, verifier=FakeTokenVerifier())
    g = app.state.scheduler
    assert g.horizon_days == 14 and g.leases.ttl == timedelta(seconds=45)
    assert sjob._bound["generator"] is g


def test_generator_and_writer_have_no_pii_fields_in_logs_and_use_utc(caplog):
    w = World()
    aid = w.assignment(w.teacher(), [TUE_4PM])
    with caplog.at_level(logging.DEBUG):
        gen_for(w).generate(aid)
    text = " ".join(
        str(r.__dict__) for r in caplog.records if r.name.startswith("hs_core.scheduling")
    )
    assert "home_location" not in text and "lat_e6" not in text
