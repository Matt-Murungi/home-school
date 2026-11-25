"""The `retention-purge` job (C03): guard, lease, dry run, chunking, isolation, summary, audit."""

import json
import logging
import threading
from datetime import UTC, datetime, timedelta

import pytest

from hs_core.jobs.alerts import FakeAlertSink
from hs_core.jobs.model import Continue, Done, JobContext
from hs_core.jobs.schedule import JOBS
from hs_core.repository import RepositoryUnavailable
from hs_core.retention import jobs as retention_jobs
from hs_core.retention import run as run_mod
from hs_core.retention.files import FakeRetentionFileStore
from hs_core.retention.purger import PurgeConfig
from hs_core.retention.run import Deps, RetentionJob
from retention_helpers import NOW, Clock, Crash, RecRepo, add_child, add_log, ago, iso


class Audit:
    def __init__(self):
        self.events = []

    def summary(self, **kw):
        self.events.append(kw)


def mk_job(repo=None, *, enforce=True, clock=None, quarantine_days=0, blast=True, **kw):
    repo = repo if repo is not None else RecRepo()
    clock = clock or Clock()
    alerts, audit = FakeAlertSink(), Audit()
    cfg = PurgeConfig(enforce=enforce, quarantine_days=quarantine_days, blast_override=blast, **{k: v for k, v in kw.items() if k in PurgeConfig.__dataclass_fields__})  # fmt: skip
    deps = Deps(repo=repo, files=kw.get("files") or FakeRetentionFileStore(), config=cfg, clock=clock, alert=alerts, audit=audit, lease_s=kw.get("lease_s", 600), payload_page=kw.get("payload_page", 40))  # fmt: skip
    return RetentionJob(deps), repo, clock, alerts, audit


def ctx(run_id="run-A", cursor=None, cancelled=None):
    return JobContext("retention-purge", run_id, f"retention-purge:{run_id}", NOW, {}, cursor, 0, cancelled)  # fmt: skip


def run_to_end(job, run_id="run-A", max_chunks=500):
    cursor, last = None, None
    for _ in range(max_chunks):
        last = job(ctx(run_id, json.loads(json.dumps(cursor)) if cursor else None))
        if isinstance(last, Done):
            return last
        cursor = last.cursor
    raise AssertionError("job did not finish")


def seed(repo):
    for i in range(6):
        repo.create("device_token", f"t{i}", {"user_id": "u", "app": "parent", "platform": "ios", "token_hash": f"h{i}", "revoked_at": ago(100)})  # fmt: skip
    repo.create("device_token", "fresh", {"user_id": "u", "app": "parent", "platform": "ios", "token_hash": "hf", "revoked_at": ago(3)})  # fmt: skip
    add_child(repo, "c1", deleted_at=ago(3000))
    add_log(repo, "l1", child="c1", note="SECRETNOTE-canary")
    repo.create("payment", "pay1", {"invoice_id": "i", "parent_id": "p1", "provider": "momo", "provider_reference": "r", "amount": 1, "currency": "UGX", "status": "succeeded", "idempotency_key": "k", "raw_callback": "SECRETCALLBACK", "requested_at": ago(3000)})  # fmt: skip


# ------------------------------------------------------------------ dry run default / enforce
def test_dry_run_reports_would_counts_and_changes_no_business_row():
    job, repo, _, _, audit = mk_job(enforce=False)
    seed(repo)
    before = {t: dict(repo.rows(t)) for t in ("device_token", "session_log", "child", "payment")}
    run_to_end(job)
    assert {t: dict(repo.rows(t)) for t in before} == before
    ev = audit.events[0]
    assert ev["action"] == "retention.dry_run"
    assert ev["detail"]["tables"]["device_token"]["would_delete"] == 6
    assert "deleted" not in ev["detail"]["tables"]["device_token"]
    runs = [r for r in repo.rows("retention_run").values() if r["kind"] == "run"]
    assert runs[0]["mode"] == "dry_run" and runs[0]["status"] == "completed"


def test_enforced_run_deletes_exactly_what_is_due_and_logs_counts_per_table():
    job, repo, _, _, audit = mk_job()
    seed(repo)
    done = run_to_end(job)
    assert set(repo.rows("device_token")) == {"fresh"}
    assert repo.rows("session_log") == {} and repo.rows("child") == {}
    pay = repo.rows("payment")["pay1"]
    assert pay["raw_callback"] is None and pay["parent_id"].startswith("anon-")
    tables = audit.events[0]["detail"]["tables"]
    assert tables["device_token"]["deleted"] == 6 and tables["payment"]["anonymised"] == 1
    assert (
        audit.events[0]["action"] == "retention.purge" and audit.events[0]["key"] == "retention:1"
    )
    assert "enforce" in done.detail


def test_second_run_the_next_day_is_idempotent_zero_changes():
    clock = Clock()
    job, repo, _, _, audit = mk_job(clock=clock)
    seed(repo)
    run_to_end(job, "r1")
    clock.advance(days=1)
    seen = len(repo.calls)
    run_to_end(job, "r2")
    business = [c for c in repo.calls[seen:] if c[0] in ("delete",)]
    assert business == []
    assert audit.events[1]["detail"]["totals"]["deleted"] == 0


def test_logs_summary_and_run_rows_carry_ids_and_counts_never_values(caplog):
    caplog.set_level(logging.DEBUG)
    job, repo, _, alerts, audit = mk_job()
    seed(repo)
    run_to_end(job)
    blob = json.dumps(audit.events) + json.dumps(list(repo.rows("retention_run").values())) + caplog.text + json.dumps([a.__dict__ for a in alerts.sent])  # fmt: skip
    for secret in ("SECRETNOTE", "SECRETCALLBACK", "Ada", "256700"):
        assert secret not in blob


def test_ids_are_listed_only_for_small_tables():
    job, repo, *_, audit = mk_job()
    for i in range(60):
        repo.create("device_token", f"t{i:03d}", {"user_id": "u", "app": "parent", "platform": "ios", "token_hash": f"h{i}", "revoked_at": ago(100)})  # fmt: skip
    repo.create("deletion_request", "dr1", {"subject_kind": "user", "subject_id": "u", "requested_by_id": "u", "status": "confirmed", "requested_at": ago(4000)})  # fmt: skip
    run_to_end(job)
    t = audit.events[0]["detail"]["tables"]
    assert "ids" not in t["device_token"] and t["deletion_request"]["ids"] == ["dr1"]


# ------------------------------------------------------------------ the purge guard
@pytest.mark.parametrize("year", [2001, 2025, 2101, 2300])
def test_guard_refuses_an_absurd_clock_and_deletes_nothing(year):
    job, repo, clock, alerts, _ = mk_job()
    seed(repo)
    clock.now = datetime(year, 6, 1, tzinfo=UTC)
    done = run_to_end(job)
    assert done.detail.startswith("refused: clock_year")
    assert len(repo.rows("device_token")) == 7 and repo.deletes() == []
    assert [a.kind for a in alerts.sent] == ["retention_guard_refused"]
    runs = [r for r in repo.rows("retention_run").values() if r["kind"] == "run"]
    assert runs[0]["status"] == "refused"


def test_guard_refuses_when_the_clock_is_earlier_than_the_last_run():
    clock = Clock()
    job, repo, _, alerts, _ = mk_job(clock=clock)
    seed(repo)
    run_to_end(job, "r1")
    for i in range(3):
        repo.create("device_token", f"n{i}", {"user_id": "u", "app": "parent", "platform": "ios", "token_hash": "x", "revoked_at": ago(100)})  # fmt: skip
    clock.now = NOW - timedelta(days=2)  # the clock jumped back
    done = run_to_end(job, "r2")
    assert done.detail == "refused: clock_before_last_run"
    assert len(repo.rows("device_token")) == 4 and alerts.sent[-1].kind == "retention_guard_refused"


def test_guard_allows_small_clock_skew_and_a_normal_next_day():
    clock = Clock()
    job, repo, _, _, _ = mk_job(clock=clock)
    run_to_end(job, "r1")
    clock.now = NOW - timedelta(minutes=4)
    assert not run_to_end(job, "r2").detail.startswith("refused")


# ------------------------------------------------------------------ lease: one run at a time
def test_fifty_concurrent_runs_exactly_one_does_the_work(monkeypatch):
    monkeypatch.setattr(run_mod, "ROWS_PER_CHUNK", 1)  # the winner is still mid-run (lease live)
    repo = RecRepo()
    seed(repo)
    results = {}
    lock = threading.Lock()

    def go(i):
        job, *_ = mk_job(repo)
        res = job(ctx(f"run-{i}"))
        with lock:
            results[i] = res

    # all jobs share one repo and one fixed clock; start them together
    threads = [threading.Thread(target=go, args=(i,)) for i in range(50)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    standing_down = [
        r for r in results.values() if isinstance(r, Done) and "holds the lease" in r.detail
    ]
    workers = [
        r for r in results.values() if not (isinstance(r, Done) and "holds the lease" in r.detail)
    ]
    assert len(workers) == 1 and len(standing_down) == 49
    leases = [r for r in repo.rows("retention_run").values() if r["kind"] == "lease"]
    assert len(leases) == 1


def test_a_second_run_while_the_first_is_mid_way_stands_down_and_the_first_can_continue(
    monkeypatch,
):
    monkeypatch.setattr(run_mod, "ROWS_PER_CHUNK", 2)
    job, repo, *_ = mk_job()
    seed(repo)
    first = job(ctx("run-A"))
    assert isinstance(first, Continue)  # mid-way
    other = job(ctx("run-B"))
    assert isinstance(other, Done) and "holds the lease" in other.detail
    cursor = first.cursor
    while True:
        res = job(ctx("run-A", json.loads(json.dumps(cursor))))
        if isinstance(res, Done):
            break
        cursor = res.cursor
    assert len(repo.rows("device_token")) == 1


def test_lease_of_a_crashed_run_is_replaced_after_it_expires_and_not_before():
    clock = Clock()
    job, repo, _, _, audit = mk_job(clock=clock, lease_s=600)
    seed(repo)
    # run-A takes the lease and "dies" after the guard/lease/run-row writes
    run_mod.acquire_lease(repo, "run-A", clock(), 600, "enforce")
    assert "holds the lease" in job(ctx("run-B")).detail
    clock.advance(seconds=599)
    assert "holds the lease" in job(ctx("run-B")).detail
    clock.advance(seconds=2)
    done = run_to_end(job, "run-B")
    assert "holds the lease" not in done.detail and len(repo.rows("device_token")) == 1
    leases = sorted(r["seq"] for r in repo.rows("retention_run").values() if r["kind"] == "lease")
    assert leases == [1, 2]  # a new generation, never a rewrite of the old one


def test_the_owner_keeps_its_lease_across_chunks_and_renews_it(monkeypatch):
    monkeypatch.setattr(run_mod, "ROWS_PER_CHUNK", 2)
    clock = Clock()
    job, repo, *_ = mk_job(clock=clock)
    seed(repo)
    cur, seen = None, 0
    while True:
        res = job(ctx("run-A", json.loads(json.dumps(cur)) if cur else None))
        clock.advance(seconds=30)
        if isinstance(res, Done):
            break
        seen += 1
        cur = res.cursor
    assert seen >= 2 and len(repo.rows("device_token")) == 1


# ------------------------------------------------------------------ chunking and budgets
def test_a_run_spans_chunks_with_a_json_cursor_and_ends_in_the_same_state(monkeypatch):
    clean, crepo, *_ = mk_job()
    seed(crepo)
    run_to_end(clean)
    monkeypatch.setattr(run_mod, "ROWS_PER_CHUNK", 3)
    job, repo, *_ = mk_job()
    seed(repo)
    chunks = 0
    cursor = None
    while True:
        res = job(ctx("run-A", json.loads(json.dumps(cursor)) if cursor else None))
        if isinstance(res, Done):
            break
        chunks += 1
        cursor = res.cursor
        assert res.items <= 6
    assert chunks >= 3
    for t in ("device_token", "session_log", "child", "payment"):
        assert repo.rows(t) == crepo.rows(t)


def test_the_cancel_flag_stops_the_run_between_rows_and_it_resumes_later():
    job, repo, *_ = mk_job()
    seed(repo)
    flag = threading.Event()
    flag.set()
    res = job(ctx("run-A", None, flag))
    assert isinstance(res, Continue) and len(repo.rows("device_token")) == 7
    done = run_to_end(job)
    assert isinstance(done, Done) and len(repo.rows("device_token")) == 1


def test_the_run_clock_is_fixed_in_the_first_chunk_across_kampala_midnight(monkeypatch):
    """A run that starts at 23:59 Kampala and continues after midnight judges all rows by one date."""
    monkeypatch.setattr(run_mod, "ROWS_PER_CHUNK", 1)
    clock = Clock(datetime(2027, 1, 18, 23, 59, tzinfo=run_mod.KAMPALA))
    job, repo, *_ = mk_job(clock=clock)
    day = datetime(2026, 12, 19, 12, tzinfo=run_mod.KAMPALA)  # + 30d = 18 Jan: kept on the 18th
    repo.create("device_token", "edge", {"user_id": "u", "app": "parent", "platform": "ios", "token_hash": "x", "revoked_at": iso(day)})  # fmt: skip
    for i in range(3):
        repo.create("device_token", f"old{i}", {"user_id": "u", "app": "parent", "platform": "ios", "token_hash": "x", "revoked_at": ago(300)})  # fmt: skip
    cursor, n = None, 0
    while True:
        res = job(ctx("run-A", json.loads(json.dumps(cursor)) if cursor else None))
        clock.advance(minutes=3)  # midnight passes mid-run
        n += 1
        if isinstance(res, Done):
            break
        cursor = res.cursor
    assert n > 2 and set(repo.rows("device_token")) == {"edge"}


# ------------------------------------------------------------------ isolation and blast radius
def test_one_failing_table_does_not_stop_the_others_and_is_alerted():
    job, repo, _, alerts, audit = mk_job()
    seed(repo)
    real = repo.list

    def flaky(table, queries=None):
        if table == "session_log":
            raise RepositoryUnavailable("503")
        return real(table, queries)

    repo.list = flaky
    run_to_end(job)
    assert (
        set(repo.rows("device_token")) == {"fresh"}
        and repo.rows("payment")["pay1"]["raw_callback"] is None
    )
    assert "l1" in repo.rows("session_log")
    assert any(a.kind == "retention_table_failed" and "session_log" in a.error for a in alerts.sent)
    assert audit.events[0]["detail"]["tables"]["session_log"]["errors"] >= 1
    assert "SECRETNOTE" not in json.dumps(audit.events)


def test_blast_radius_refuses_the_table_alerts_and_the_override_allows_it():
    job, repo, _, alerts, audit = mk_job(blast=False, max_delete_pct=10)
    for i in range(100):
        repo.create("device_token", f"t{i:03d}", {"user_id": "u", "app": "parent", "platform": "ios", "token_hash": "x", "revoked_at": ago(100)})  # fmt: skip
    run_to_end(job)
    assert len(repo.rows("device_token")) == 100
    assert any(a.kind == "retention_blast_limit" for a in alerts.sent)
    assert audit.events[0]["detail"]["tables"]["device_token"]["refused"].startswith("blast_limit")
    job2, *_ = mk_job(repo, blast=True)
    run_to_end(job2, "run-B")
    assert repo.rows("device_token") == {}


# ------------------------------------------------------------------ N01 payload expiry stage
def notification(repo, nid, created, status="sent"):
    repo.create("notification", nid, {"user_id": "u", "channel": "push", "template_key": "k", "template_version": 1, "category": "session", "payload": json.dumps({"vars": {"name": "Ada"}, "meta": {"locale": "en"}}), "status": status, "attempts": 1, "dedupe_key": f"d{nid}", "created_at": created})  # fmt: skip


def test_notification_payloads_older_than_90_days_are_stripped_by_the_n01_hook_in_chunks():
    job, repo, *_ = mk_job(payload_page=3)
    for i in range(10):
        notification(repo, f"n{i}", ago(100))
    notification(repo, "young", ago(30))
    notification(repo, "queued", ago(100), status="queued")
    run_to_end(job)
    rows = repo.rows("notification")
    assert all("Ada" not in rows[f"n{i}"]["payload"] for i in range(10))
    assert "Ada" in rows["young"]["payload"] and "Ada" in rows["queued"]["payload"]


def test_payload_expiry_boundary_is_the_kampala_midnight_90_days_back():
    job, repo, *_ = mk_job()
    notification(
        repo, "edge", iso(datetime(2026, 10, 20, 0, 0, tzinfo=run_mod.KAMPALA))
    )  # = boundary: kept
    notification(repo, "older", iso(datetime(2026, 10, 19, 23, 59, tzinfo=run_mod.KAMPALA)))
    run_to_end(job)
    rows = repo.rows("notification")
    assert "Ada" in rows["edge"]["payload"] and "Ada" not in rows["older"]["payload"]


def test_dry_run_leaves_payloads_alone():
    job, repo, *_ = mk_job(enforce=False)
    notification(repo, "n", ago(100))
    run_to_end(job)
    assert "Ada" in repo.rows("notification")["n"]["payload"]


# ------------------------------------------------------------------ crash injection at job level
def test_crash_before_every_write_of_a_whole_run_then_resume_reaches_the_clean_state():
    def world():
        r = RecRepo()
        seed(r)
        return r

    clean = world()
    cjob, *_ = mk_job(clean)
    seed_writes = clean.writes
    run_to_end(cjob)
    total = clean.writes - seed_writes
    expect = {t: clean.rows(t) for t in ("device_token", "session_log", "child", "payment")}
    assert total > 12
    for k in range(1, total + 1):
        repo = world()
        job, *_ = mk_job(repo)
        repo.crash_at = repo.writes + k
        with pytest.raises(Crash):
            run_to_end(job)
        repo.crash_at = None
        job2, *_ = mk_job(repo)
        run_to_end(job2)  # F04 re-runs the chunk with the same run id
        assert {t: repo.rows(t) for t in expect} == expect, f"crash before write {k}"


# ------------------------------------------------------------------ schedule, binding, wiring
def test_job_is_registered_daily_in_the_one_schedule_list():
    spec = next(j for j in JOBS if j.name == "retention-purge")
    assert (
        spec.schedule.every == timedelta(days=1) and spec.handler is retention_jobs.retention_purge
    )
    assert spec.max_attempts == 3 and spec.catch_up == timedelta(days=1)


def test_unbound_job_fails_loudly_and_the_app_binds_it_in_dry_run_by_default():
    retention_jobs.bind(None)
    with pytest.raises(RuntimeError, match="not bound"):
        retention_jobs.retention_purge(ctx())
    from hs_core.main import create_app
    from hs_core.settings import Settings

    app = create_app(Settings(_env_file=None, hs_env="dev"), repository=RecRepo())
    assert app.state.retention.d.config.enforce is False
    assert retention_jobs._job is app.state.retention
    app2 = create_app(
        Settings(_env_file=None, hs_env="dev", retention_enforce=True), repository=RecRepo()
    )
    assert app2.state.retention.d.config.enforce is True


def test_run_through_the_f04_runner_end_to_end_with_the_real_audit_service():
    from hs_core.jobs.runner import Runner
    from hs_core.main import create_app
    from hs_core.settings import Settings

    repo = RecRepo()
    seed(repo)
    app = create_app(Settings(_env_file=None, hs_env="dev", retention_enforce=True, retention_blast_override=True, retention_quarantine_days=0), repository=repo)  # fmt: skip
    clock = Clock(datetime(2027, 1, 18, 1, 0, tzinfo=UTC))
    runner = Runner(app.state.repository, JOBS, clock=clock)
    runner.enqueue("retention-purge", "manual-1", run_at=clock())
    for _ in range(40):
        runner.tick()
        clock.advance(seconds=30)
    assert set(repo.rows("device_token")) == {"fresh"}
    audit_rows = [r for r in repo.rows("audit_log").values() if r.get("entity") == "retention_run"]
    # the manual run and the scheduled 03:30 Kampala period each leave ONE summary event
    assert len(audit_rows) == 2 and {r["action"] for r in audit_rows} == {"retention.purge"}
    assert "SECRETNOTE" not in json.dumps(audit_rows)
