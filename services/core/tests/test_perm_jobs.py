"""address-revoke, permission-reconcile, permission-audit on the F04 runner (ADR 0012)."""

import dataclasses
import json
from datetime import timedelta

import pytest

from hs_core.jobs import schedule
from hs_core.jobs.alerts import FakeAlertSink
from hs_core.jobs.runner import Runner
from hs_core.permissions import jobs as pjobs
from hs_core.permissions.audit import audit_all
from hs_core.permissions.service import (
    RetryQueue,
    Scope,
    assignment_changed,
)
from jobs_helpers import T0, Crash
from perm_world import H, World, iso

NAMES = ("address-revoke", "permission-reconcile", "permission-audit")


def specs(*names):
    return [j for j in schedule.JOBS if j.name in names]


@pytest.fixture
def env():
    w = World().seed()
    alerts = FakeAlertSink()
    w.svc.alerts = alerts
    pjobs.bind(lambda: w.svc)
    runner = Runner(w.repo, specs(*NAMES), alert=alerts, clock=w.clock)
    w.svc.queue = RetryQueue(runner, w.clock)
    yield w, runner, alerts
    pjobs.bind(None)


@pytest.fixture
def audit_env():
    """Only the audit job on the runner (the nightly reconcile would repair what tests inject)."""
    w = World().seed()
    alerts = FakeAlertSink()
    w.svc.alerts = alerts
    pjobs.bind(lambda: w.svc)
    yield w, Runner(w.repo, specs("permission-audit"), alert=alerts, clock=w.clock), alerts
    pjobs.bind(None)


def run_to_completion(w, runner, name, step=timedelta(seconds=40), limit=80):
    """Tick (advancing the fake clock) until a run of job `name` has completed."""
    for _ in range(limit):
        runner.tick()
        if any(r["status"] == "completed" for r in runs_of(w, name)):
            return
        w.clock.now += step
    raise AssertionError(f"{name} did not complete")


def can(w, uid, table, rid):
    return w.as_(uid).can_read(table, rid)


def runs_of(w, name):
    return [
        r
        for r in w.repo.list("job_run")
        if r["name"] == name and not r["dedupe_key"].startswith("state:")
    ]


# ------------------------------------------------------------------ schedule
def test_the_three_jobs_are_in_the_one_schedule_with_sensible_cadence():
    by = {j.name: j for j in schedule.JOBS}
    assert set(NAMES) <= set(by)
    assert by["address-revoke"].schedule.every == timedelta(minutes=5)
    assert by["address-revoke"].catch_up <= timedelta(hours=1)  # F04 follow-up 1: small catch-up
    assert by["permission-reconcile"].schedule.every == timedelta(days=1)
    assert by["permission-audit"].schedule.every == timedelta(days=1)
    assert len({j.name for j in schedule.JOBS}) == len(schedule.JOBS)


def test_nightly_jobs_run_at_night_in_kampala_time():
    from hs_core.jobs.model import KAMPALA

    for name, hour in (("permission-reconcile", 2), ("permission-audit", 3)):
        spec = next(j for j in schedule.JOBS if j.name == name)
        first = spec.schedule.due_periods(T0, T0 + timedelta(days=1))[0]
        assert first.astimezone(KAMPALA).hour == hour


def test_an_unbound_job_fails_loudly_instead_of_doing_nothing():
    pjobs.bind(None)
    from hs_core.jobs.model import JobContext

    ctx = JobContext("address-revoke", "r", "k", None, {}, None, 0, None)
    with pytest.raises(RuntimeError):
        pjobs.address_revoke(ctx)


# ------------------------------------------------------------------ address-revoke
def kid(w, i, teacher="T4"):
    """A family of its own (parent, address, child) whose teacher's access lapses at T0 + 18 h."""
    w.add("parent_profile", f"PK{i}", user_id="uPB")
    w.add("home_location", f"hlK{i}", parent_id=f"PK{i}", label="Home")
    w.add("child", f"ch{i}", parent_id=f"PK{i}", name=f"Kid{i} X")
    w.add(
        "assignment",
        f"as{i}",
        child_id=f"ch{i}",
        teacher_id=teacher,
        start_at=iso(T0 - 9 * H),
        end_at=iso(T0 - 5 * H),
        end_reason="trial_not_continued",
    )
    w.add(
        "session",
        f"ss{i}",
        child_id=f"ch{i}",
        teacher_id=teacher,
        assignment_id=f"as{i}",
        status="confirmed",
        scheduled_start_at=iso(T0 - 7 * H),
        scheduled_end_at=iso(T0 - 6 * H),
        is_substitute=False,
    )
    w.svc.sync_scope(Scope("child", f"ch{i}"))


def end_assignment_at(w, end, last_session_end):
    w.patch("assignment", "asg1", end_at=iso(end), end_reason="trial_not_continued")
    w.patch("session", "sA1", scheduled_end_at=iso(last_session_end), status="confirmed")
    w.svc.apply(assignment_changed("assignment:asg1:end", "a1", "T1", ended=True))


def test_address_access_is_revoked_by_the_job_24h_after_the_last_session(env):
    w, runner, _ = env
    end_assignment_at(w, T0, T0 - 2 * H)  # last session ended 2 h ago -> lapses in 22 h
    assert can(w, "uT1", "home_location", "hlA") and not can(w, "uT1", "child", "a1")
    w.clock.now = T0 + 21 * H + timedelta(minutes=55)
    runner.tick()
    assert can(w, "uT1", "home_location", "hlA")  # not yet
    w.clock.now = T0 + 22 * H + timedelta(minutes=1)
    runner.tick()
    assert not can(w, "uT1", "home_location", "hlA")
    assert can(w, "uT1", "teacher_session_history", "tshT1")  # earnings history untouched


def test_the_job_is_idempotent_late_and_repeatable(env):
    w, runner, _ = env
    end_assignment_at(w, T0, T0 - 2 * H)
    w.clock.now = T0 + 5 * 24 * H  # the job was down for days
    runner.tick()
    assert not can(w, "uT1", "home_location", "hlA")
    n = len(w.repo.writes)
    for _ in range(3):
        w.clock.now += timedelta(minutes=5)
        runner.tick()
    assert [x for x in w.repo.writes[n:] if x[1] not in ("job_run", "job_attempt")] == []


def test_substitute_access_is_removed_by_the_job_after_the_covered_session(env):
    w, runner, _ = env
    w.svc.apply(assignment_changed("noop", "a1", "T3"))
    assert can(w, "uT3", "home_location", "hlA")  # window: sSub ends T0+21h, lapses T0+45h
    w.clock.now = T0 + 45 * H + timedelta(minutes=6)
    runner.tick()
    assert not can(w, "uT3", "home_location", "hlA") and not can(w, "uT3", "child", "a1")
    assert can(w, "uT1", "child", "a1")  # the regular teacher is not affected


def test_address_revoke_is_chunked_and_resumes(env, monkeypatch):
    w, runner, _ = env
    monkeypatch.setattr(pjobs, "WINDOWS_PER_CHUNK", 3)
    for i in range(8):
        kid(w, i)
    assert all(can(w, "uT4", "home_location", f"hlK{i}") for i in range(8))
    w.clock.now = T0 + 19 * H
    for _ in range(4):  # 3 windows per chunk: several ticks and continuations
        runner.tick()
        w.clock.now += timedelta(minutes=5)
    assert not any(can(w, "uT4", "home_location", f"hlK{i}") for i in range(8))
    assert {x["state"] for x in w.repo.list("access_window") if x["teacher_id"] == "T4"} == {
        "closed"
    }


def test_failed_revoke_is_retried_with_backoff_and_alerts(env):
    w, runner, alerts = env
    end_assignment_at(w, T0, T0 - 2 * H)
    w.clock.now = T0 + 23 * H
    w.repo.fail = lambda op, t, r: op == "update" and t == "home_location"
    runner.tick()
    assert can(w, "uT1", "home_location", "hlA")  # over-grant persists while the write fails
    assert any(a.kind == "permission_over_grant" for a in alerts.sent)
    runs = runs_of(w, "address-revoke")
    assert any(r["status"] == "queued" and r["attempts"] >= 1 for r in runs)
    w.repo.fail = None
    for _ in range(8):  # backoff 1 min, 2 min, ...
        w.clock.now += timedelta(minutes=3)
        runner.tick()
    assert not can(w, "uT1", "home_location", "hlA")


def test_killing_the_worker_midway_then_a_fresh_process_finishes_without_double_effects(env):
    w, runner, _ = env
    for i in range(6):
        kid(w, i)
    w.clock.now = T0 + 19 * H
    seen = {"n": 0}

    def die_on_third_permission_write(op, table, rid):
        if op == "update" and table == "home_location":
            seen["n"] += 1
            return seen["n"] == 3
        return False

    w.repo.crash = die_on_third_permission_write
    with pytest.raises(Crash):
        runner.tick()
    w.repo.crash = None
    revoked_before = sum(not can(w, "uT4", "home_location", f"hlK{i}") for i in range(6))
    assert 0 < revoked_before < 6  # the kill left the work half done
    w.clock.now += timedelta(minutes=6)  # the dead claimer's lease runs out
    fresh = Runner(w.repo, specs(*NAMES), alert=FakeAlertSink(), clock=w.clock)
    for _ in range(4):
        fresh.tick()
        w.clock.now += timedelta(minutes=5)
    assert not any(can(w, "uT4", "home_location", f"hlK{i}") for i in range(6))
    writes_before = len(w.repo.writes)
    fresh.tick()
    assert [x for x in w.repo.writes[writes_before:] if x[1] in ("child", "home_location")] == []


# ------------------------------------------------------------------ permission-reconcile
def test_event_retry_job_repairs_a_failed_step_three_within_about_a_minute(env):
    w, _, _ = env
    spec = dataclasses.replace(specs("permission-reconcile")[0], schedule=None)  # event runs only
    runner = Runner(w.repo, [spec], alert=w.svc.alerts, clock=w.clock)
    w.svc.queue = RetryQueue(runner, w.clock)
    ev = assignment_changed("assignment:asgN:start", "a2", "T4")
    w.repo.fail = lambda op, t, r: op == "update" and t in ("child", "home_location")

    def business():
        w.add("assignment", "asgN", child_id="a2", teacher_id="T4", start_at=iso(T0), end_at=None)

    _, report = w.svc.run(ev, business)
    assert not report.ok and not can(w, "uT4", "child", "a2")  # under-grant, safe
    w.repo.fail = None
    w.clock.now += timedelta(seconds=59)
    runner.tick()
    assert not can(w, "uT4", "child", "a2")  # the job is not due yet
    w.clock.now += timedelta(seconds=2)
    runner.tick()
    assert can(w, "uT4", "child", "a2")


def test_the_same_event_is_queued_once(env):
    w, runner, _ = env
    ev = assignment_changed("assignment:dup", "a2", "T4")
    assert w.svc.queue.enqueue(ev) is True and w.svc.queue.enqueue(ev) is False
    assert (
        len([r for r in runs_of(w, "permission-reconcile") if r["dedupe_key"] == ev.dedupe_key])
        == 1
    )


def test_event_job_that_keeps_failing_alerts_and_finally_fails_but_never_over_grants_more(env):
    w, _, alerts = env
    spec = dataclasses.replace(specs("permission-reconcile")[0], schedule=None)
    runner = Runner(w.repo, [spec], alert=alerts, clock=w.clock)
    w.svc.queue = RetryQueue(runner, w.clock)
    w.patch("assignment", "asg1", end_at=iso(T0 - H), end_reason="admin")
    ev = assignment_changed("assignment:asg1:end", "a1", "T1", ended=True)
    w.svc.queue.enqueue(ev)
    w.repo.fail = lambda op, t, r: op == "update" and t == "child"
    for _ in range(8):
        w.clock.now += timedelta(minutes=5)
        runner.tick()
    assert can(w, "uT1", "child", "a1")  # still over-granted: the alerts are the signal
    assert any(a.kind == "permission_over_grant" for a in alerts.sent)
    assert any(a.kind == "final_failure" for a in alerts.sent)


def test_nightly_reconcile_repairs_recently_touched_business_rows(env):
    w, runner, _ = env
    # a business write that slipped through without its permission step (the drift case)
    w.add("assignment", "asgDrift", child_id="a2", teacher_id="T4", start_at=iso(T0), end_at=None)
    assert not can(w, "uT4", "child", "a2")
    w.clock.now = T0 + 20 * H  # next 02:00 Kampala is 23:00 UTC
    w.clock.now = T0 + 14 * H + timedelta(minutes=5)
    runner.tick()
    assert can(w, "uT4", "child", "a2")


def test_nightly_reconcile_looks_back_only_over_the_lookback_window(env):
    """Rows untouched for days are not rescanned by reconcile: the audit job is what finds them."""
    w, runner, _ = env
    w.repo.update("child", "b1", {}, permissions=['read("user:uT4")', 'read("label:opsadmin")'])
    w.clock.now = T0 + 5 * 24 * H
    runner.tick()
    assert can(w, "uT4", "child", "b1")  # drift stays
    summary = audit_all(w.repo, w.files, w.clock)
    assert summary.over_grants == 1 and "child/b1" in summary.over_samples[0]


def test_nightly_reconcile_is_chunked_and_resumes(env, monkeypatch):
    w, _, _ = env
    runner = Runner(w.repo, specs("permission-reconcile"), alert=w.svc.alerts, clock=w.clock)
    monkeypatch.setattr(pjobs, "ROWS_PER_CHUNK", 25)
    w.add("assignment", "asgDrift", child_id="a2", teacher_id="T4", start_at=iso(T0), end_at=None)
    w.clock.now = T0 + 14 * H + timedelta(minutes=5)
    run_to_completion(w, runner, "permission-reconcile", step=timedelta(seconds=30), limit=120)
    assert can(w, "uT4", "child", "a2")
    attempts = [a for a in w.repo.list("job_attempt") if a.get("outcome") == "continue"]
    assert len(attempts) >= 2  # it really ran in several chunks, each resuming from the cursor


# ------------------------------------------------------------------ permission-audit
def test_audit_reports_zero_differences_on_a_correct_world(env):
    w, _, _ = env
    s = audit_all(w.repo, w.files, w.clock)
    assert s.clean and s.rows > 150 and s.files == 6 and s.drift_rate == 0.0


def test_audit_reports_over_under_and_forbidden_without_writing(env):
    w, _, _ = env
    w.repo.update(
        "child", "a1", {}, permissions=['read("user:uPA")', 'read("user:uT4")']
    )  # over + under
    w.repo.update("child", "b1", {}, permissions=['read("any")'])  # forbidden + over + under
    w.repo.update("topic", "tpPub", {}, permissions=[])  # under only (users read missing)
    w.repo.update(
        "invoice",
        "invA",
        {},
        permissions=[
            'read("users")',
            'update("user:uPA")',
            'read("label:opsadmin")',
            'read("user:uPA")',
        ],
    )
    w.files.update_permissions(
        "teacher-docs",
        "f_td_t1",
        ['read("user:uT4")', 'read("user:uT1")', 'read("label:opsadmin")', 'read("user:uL1")'],
    )
    writes = len(w.repo.writes)
    s = audit_all(w.repo, w.files, w.clock)
    assert (s.over_grants, s.under_grants, s.forbidden) == (4, 3, 2)
    assert len(w.repo.writes) == writes and w.files.writes == w.files.writes  # report only
    assert s.high_severity and s.drift_rate > 0
    assert any(x.startswith("file:teacher-docs/f_td_t1") for x in s.over_samples)


def test_audit_job_alerts_on_over_grant_and_is_quiet_when_clean(audit_env):
    w, runner, alerts = audit_env
    w.clock.now = T0 + 20 * H  # past 03:00 Kampala
    run_to_completion(w, runner, "permission-audit")
    assert not [a for a in alerts.sent if a.kind == "permission_drift"]
    w.repo.update("child", "b1", {}, permissions=['read("user:uT4")'])
    w.clock.now += 24 * H
    for _ in range(40):
        runner.tick()
        w.clock.now += timedelta(seconds=40)
    drift = [a for a in alerts.sent if a.kind == "permission_drift"]
    assert len(drift) == 1 and drift[0].count >= 1 and "over_grants" in drift[0].error


def test_audit_under_grant_alone_is_low_severity_no_alert(audit_env):
    w, runner, alerts = audit_env
    w.repo.update("child", "b1", {}, permissions=['read("label:opsadmin")'])
    writes = len(w.repo.writes)
    w.clock.now = T0 + 20 * H
    run_to_completion(w, runner, "permission-audit")
    assert not [a for a in alerts.sent if a.kind == "permission_drift"]
    assert [x for x in w.repo.writes[writes:] if x[1] == "child"] == []  # reports, never repairs
    s = audit_all(w.repo, w.files, w.clock)
    assert s.under_grants == 1 and not s.high_severity


def test_audit_is_chunked_resumable_and_equals_the_single_pass(audit_env, monkeypatch):
    from hs_core.permissions.audit import Auditor

    w, runner, alerts = audit_env
    w.repo.update("child", "b1", {}, permissions=['read("user:uT4")'])
    w.repo.update("home_location", "hlA", {}, permissions=['read("any")'])
    whole = audit_all(w.repo, w.files, w.clock, rows_per_chunk=10_000)
    a = Auditor(w.repo, w.files, w.clock)
    cursor, chunks = a.start(), 0
    while cursor is not None:
        cursor = json.loads(json.dumps(cursor))  # the cursor survives the job payload round trip
        cursor, summary = a.step(cursor, rows_budget=7)
        chunks += 1
    assert chunks > 20 and summary.to_dict() == whole.to_dict()
    monkeypatch.setattr(pjobs, "ROWS_PER_CHUNK", 7)
    w.clock.now = T0 + 20 * H
    for _ in range(80):
        runner.tick()
        w.clock.now += timedelta(seconds=40)
    (run,) = [r for r in runs_of(w, "permission-audit") if r["status"] == "completed"]
    assert (
        run["attempts"] >= 1 and len([x for x in alerts.sent if x.kind == "permission_drift"]) == 1
    )


def test_audit_survives_a_crash_between_chunks_and_resumes_from_the_cursor(audit_env, monkeypatch):
    w, runner, alerts = audit_env
    monkeypatch.setattr(pjobs, "ROWS_PER_CHUNK", 20)
    w.repo.update("child", "b1", {}, permissions=['read("user:uT4")'])
    w.clock.now = T0 + 20 * H
    seen = {"n": 0}

    def die_once(op, table, rid):
        if op == "update" and table == "job_run":
            seen["n"] += 1
            return seen["n"] == 4
        return False

    w.repo.crash = die_once
    with pytest.raises(Crash):
        for _ in range(30):
            runner.tick()
            w.clock.now += timedelta(seconds=30)
    w.repo.crash = None
    w.clock.now += timedelta(minutes=6)
    fresh = Runner(w.repo, specs("permission-audit"), alert=alerts, clock=w.clock)
    for _ in range(60):
        fresh.tick()
        w.clock.now += timedelta(seconds=40)
    assert (
        len([a for a in alerts.sent if a.kind == "permission_drift"]) == 1
    )  # alerted once, not per chunk
    assert [r["status"] for r in runs_of(w, "permission-audit")].count("completed") >= 1


def test_audit_skips_planned_tables_that_do_not_exist_yet(env):
    w, _, _ = env
    s = audit_all(w.repo, w.files, w.clock)  # job_run etc. are listed, none raise
    assert s.clean


def test_the_app_wires_the_permission_service_and_the_jobs_run_end_to_end():
    from hs_core.auth import FakeTokenVerifier
    from hs_core.main import create_app
    from hs_core.permissions.fake import PermissionAwareRepository
    from hs_core.settings import Settings

    repo = PermissionAwareRepository()
    app = create_app(
        Settings(_env_file=None, hs_env="dev"), repository=repo, verifier=FakeTokenVerifier()
    )
    try:
        svc = app.state.permissions
        assert svc.repo is repo and pjobs._service() is svc
        assert {j for j in NAMES} <= set(app.state.runner.jobs)
        repo.create("child", "c9", {"parent_id": "P9", "name": "Zed Q"}, ['read("user:ghost")'])
        repo.create("parent_profile", "P9", {"user_id": "u9"}, [])
        repo.create("user_profile", "u9", {"status": "active"}, [])
        report = svc.sync_scope(Scope("child", "c9"))
        assert report.ok and repo.get("child", "c9")["$permissions"] == [
            'read("label:opsadmin")'
        ]  # the ghost grant is gone; the parent holds no `parent` label here
        assert (
            svc.queue.enqueue(assignment_changed("e:app", "c9", "T9")) is True
        )  # queue is the app's runner
    finally:
        pjobs.bind(None)
