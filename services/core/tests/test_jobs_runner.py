"""Job runner (ADR 0002): periods, claims, retries, crashes, alerts. Offline, fake clock."""

import json
import threading
import time
from datetime import UTC, datetime, timedelta

import pytest

from hs_core.jobs.alerts import FakeAlertSink
from hs_core.jobs.idempotency import dedupe_key
from hs_core.jobs.model import Continue, Done, JobSpec, Schedule
from hs_core.jobs.runner import Runner, row_id
from jobs_helpers import T0, Crash, Effects, FakeClock, HookRepository, make, runs

EVERY_5 = Schedule(timedelta(minutes=5), anchor=T0)


# ---------------------------------------------------------------- periods
def test_scheduled_job_runs_once_per_period_even_if_ticked_repeatedly():
    fx = Effects()
    repo, runner, clock, _ = make(fx.handler, schedule=EVERY_5, catch_up=timedelta(0))
    clock.advance(seconds=30)
    for _ in range(3):
        runner.tick()
    assert fx.invocations == [f"demo:{T0.isoformat()}#chunk0"]
    assert [r["status"] for r in runs(repo)] == ["completed"]


def test_missed_ticks_are_caught_up_not_lost():
    seen = []
    repo, runner, clock, _ = make(
        lambda ctx: seen.append(ctx.scheduled_for) or Done(),
        schedule=EVERY_5,
        catch_up=timedelta(hours=2),
    )
    runner.tick()
    clock.advance(hours=1)  # service or function down for an hour
    result = runner.tick()
    assert result.created == 12
    assert seen == [T0 + timedelta(minutes=5 * i) for i in range(13)]


def test_catch_up_window_bounds_a_very_long_outage():
    seen = []
    repo, runner, clock, _ = make(
        lambda ctx: seen.append(ctx.scheduled_for) or Done(),
        schedule=EVERY_5,
        catch_up=timedelta(hours=1),
    )
    runner.tick()
    clock.advance(days=10)
    runner.tick(budget_s=60)
    # first run + only periods inside the 1 hour window after 10 days
    assert len(seen) <= 1 + 13


def test_daily_schedule_uses_kampala_time():
    from zoneinfo import ZoneInfo

    daily = Schedule(
        timedelta(days=1), anchor=datetime(2026, 1, 1, 2, 0, tzinfo=ZoneInfo("Africa/Kampala"))
    )
    periods = daily.due_periods(
        datetime(2027, 1, 17, 0, 0, tzinfo=UTC), datetime(2027, 1, 18, 0, 0, tzinfo=UTC)
    )
    assert periods == [datetime(2027, 1, 17, 23, 0, tzinfo=UTC)]  # 02:00 Kampala = 23:00 UTC


def test_default_schedule_anchor_is_kampala_midnight():
    s = Schedule(timedelta(days=1))
    (p,) = s.due_periods(
        datetime(2027, 1, 17, 12, tzinfo=UTC), datetime(2027, 1, 18, 12, tzinfo=UTC)
    )
    assert p == datetime(2027, 1, 17, 21, 0, tzinfo=UTC)  # 00:00 on 18 Jan in Kampala (UTC+3)


# ---------------------------------------------------------------- watermark and indexed queries
def test_materialise_does_not_recreate_every_period_on_every_tick():
    repo, runner, clock, _ = make(schedule=EVERY_5, catch_up=timedelta(days=2))
    clock.advance(minutes=1)
    runner.tick()
    first = repo.count("create", "job_run")
    for _ in range(10):
        clock.advance(minutes=1)
        runner.tick()
    # 2 more periods came due over 10 minutes: only those are created (plus nothing else)
    assert repo.count("create", "job_run") - first == 2


def test_watermark_survives_restart_so_a_cold_start_does_not_rescan_the_window():
    repo, runner, clock, _ = make(schedule=EVERY_5, catch_up=timedelta(days=2))
    clock.advance(hours=1)
    runner.tick(budget_s=60)
    before = repo.count("create", "job_run")
    _, runner2, _, _ = make(repo=repo, clock=clock, schedule=EVERY_5, catch_up=timedelta(days=2))
    clock.advance(minutes=1)
    runner2.tick()
    assert repo.count("create", "job_run") == before  # no period came due, nothing recreated


def test_claimable_runs_are_fetched_with_the_status_run_at_index_and_a_limit():
    repo, runner, clock, _ = make(schedule=EVERY_5, catch_up=timedelta(hours=2))
    runner.tick()
    repo.calls.clear()
    clock.advance(minutes=1)
    runner.tick()
    job_run_lists = [c for c in repo.calls if c[0] == "list" and c[1] == "job_run"]
    assert job_run_lists, "claimable runs must be queried"
    for _, _, _, queries in job_run_lists:
        parsed = [json.loads(q) for q in queries or []]
        methods = {p["method"] for p in parsed}
        assert "limit" in methods and "orderAsc" in methods
        eq = next(p for p in parsed if p["method"] == "equal")
        assert eq["attribute"] == "status"
        # only attributes of the by_status_run_at index are filtered on
        assert {p.get("attribute") for p in parsed} <= {"status", "run_at", None}


def test_a_tick_claims_at_most_claim_batch_runs_and_the_rest_wait_for_the_next_tick():
    done = []
    repo = HookRepository()
    spec = JobSpec("demo", lambda ctx: done.append(ctx.dedupe_key) or Done())
    runner = Runner(repo, [spec], alert=FakeAlertSink(), clock=FakeClock(), claim_batch=10)
    for i in range(25):
        runner.enqueue("demo", f"k{i}", run_at=T0)
    runner.tick()
    assert len(done) == 10
    runner.tick()
    runner.tick()
    assert len(done) == 25 and len(set(done)) == 25


# ---------------------------------------------------------------- concurrency
class BarrierRepo(HookRepository):
    """Every ticking thread takes its first snapshot of job_run, then they race to claim."""

    def __init__(self, parties):
        super().__init__()
        self._barrier = threading.Barrier(parties, timeout=10)
        self._seen = threading.local()
        self.armed = False

    def list(self, table, queries=None):
        rows = super().list(table, queries)
        if table == "job_run" and self.armed and not getattr(self._seen, "done", False):
            self._seen.done = True
            self._barrier.wait()
        return rows


def test_two_workers_same_tick_apply_each_run_exactly_once():
    fx, workers = Effects(), 8

    def handler(ctx):
        time.sleep(0.005)
        return fx.handler(ctx)

    repo = BarrierRepo(workers)
    clock = FakeClock()
    runner = Runner(
        repo,
        [JobSpec("demo", handler, schedule=EVERY_5, catch_up=timedelta(minutes=30))],
        alert=FakeAlertSink(),
        clock=clock,
    )
    clock.advance(minutes=30)
    repo.armed = True
    results = []
    threads = [
        threading.Thread(target=lambda: results.append(runner.tick())) for _ in range(workers)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    repo.armed = False
    assert len(fx.invocations) == len(set(fx.invocations)) == 7  # periods 0,5,...,30 min
    assert sum(len(r.completed) for r in results) == 7
    assert all(r["status"] == "completed" for r in runs(repo))


def test_same_period_ticked_from_two_runner_instances_runs_once():
    fx = Effects()
    repo = HookRepository()
    clock = FakeClock()

    def mk():
        return Runner(
            repo,
            [JobSpec("demo", fx.handler, schedule=EVERY_5)],  # noqa: E731
            alert=FakeAlertSink(),
            clock=clock,
        )

    a, b = mk(), mk()
    a.tick()
    b.tick()
    assert len(fx.invocations) == 1


# ---------------------------------------------------------------- idempotency helper
def test_idempotency_key_is_name_and_period():
    assert dedupe_key("payout", "2027-W03") == "payout:2027-W03"
    assert dedupe_key("a", T0) == f"a:{T0.isoformat()}"
    assert dedupe_key("a", datetime(2027, 1, 18, 12, tzinfo=UTC).astimezone()).startswith(
        "a:2027-01-18T12:00:00"
    )
    with pytest.raises(ValueError):
        dedupe_key("", "x")
    with pytest.raises(ValueError):
        dedupe_key("a", "")


def test_job_keyed_by_name_and_period_cannot_apply_twice():
    fx = Effects()
    repo, runner, clock, _ = make(fx.handler)
    key = dedupe_key("demo", "2027-W03")
    assert runner.enqueue("demo", key, run_at=T0) is True
    assert runner.enqueue("demo", key, run_at=T0) is False
    runner.tick()
    clock.advance(hours=1)
    assert runner.enqueue("demo", key, run_at=clock()) is False  # even after it completed
    runner.tick()
    assert fx.invocations == [f"{key}#chunk0"]


def test_enqueue_supports_delay_and_manual_runs_with_audit_fields():
    got = []
    repo, runner, clock, _ = make(lambda ctx: got.append(ctx.payload) or Done())
    assert runner.enqueue(
        "demo", "reminder:sub-1", run_at=T0 + timedelta(days=1), payload={"sub": 1}
    )
    runner.tick()
    assert got == []  # delayed
    runner.enqueue(
        "demo",
        "manual:recalc:1",
        run_at=T0,
        trigger="manual",
        actor_id="admin-1",
        reason="re-run after fixing rates",
    )
    clock.advance(days=1)
    runner.tick()
    assert sorted(map(str, got)) == sorted(["{'sub': 1}", "{}"])
    manual = next(r for r in runs(repo) if r["trigger"] == "manual")
    assert (manual["actor_id"], manual["reason"]) == ("admin-1", "re-run after fixing rates")


# ---------------------------------------------------------------- run records
def test_each_run_records_job_period_started_finished_outcome_and_items():
    repo, runner, clock, _ = make(
        lambda ctx: Done(items=42), schedule=EVERY_5, catch_up=timedelta(0)
    )
    clock.advance(seconds=30)
    runner.tick()
    (run,) = runs(repo)
    assert run["name"] == "demo"
    assert run["scheduled_for"] == T0.isoformat()
    assert run["started_at"] == (T0 + timedelta(seconds=30)).isoformat()
    assert run["finished_at"] == (T0 + timedelta(seconds=30)).isoformat()
    assert run["status"] == "completed"
    assert json.loads(run["payload"])["_items"] == 42
    (att,) = repo.list("job_attempt")
    assert (att["outcome"], att["items"], att["run_id"]) == ("completed", 42, run["$id"])
    assert att["finished_at"]


def test_run_logging_emits_a_structured_line(caplog):
    caplog.set_level("INFO", logger="hs_core.jobs")
    repo, runner, clock, _ = make(
        lambda ctx: Done(items=3), schedule=EVERY_5, catch_up=timedelta(0)
    )
    runner.tick()
    rec = next(r for r in caplog.records if r.getMessage() == "job run finished")
    assert (rec.job, rec.outcome, rec.items) == ("demo", "completed", 3)


# ---------------------------------------------------------------- retries, failure, alerts
def test_failures_retry_with_backoff_then_fail_and_alert_once():
    calls = []

    def flaky(ctx):
        calls.append(ctx.dedupe_key)
        raise RuntimeError("MoMo sandbox unavailable")

    repo, runner, clock, alerts = make(flaky, max_attempts=3, backoff_base=timedelta(minutes=1))
    runner.enqueue("demo", "payout:2027-W03", run_at=T0)
    runner.tick()  # attempt 1 fails -> retry in 1 min
    (run,) = runs(repo)
    assert (run["status"], run["attempts"], run["run_at"]) == (
        "queued",
        1,
        (T0 + timedelta(minutes=1)).isoformat(),
    )
    clock.advance(seconds=30)
    runner.tick()
    assert len(calls) == 1  # not due yet
    clock.advance(seconds=30)
    runner.tick()  # attempt 2 fails -> retry in 2 min
    (run,) = runs(repo)
    assert run["run_at"] == (T0 + timedelta(minutes=3)).isoformat()
    clock.advance(minutes=2)
    runner.tick()  # attempt 3 fails -> failed
    (run,) = runs(repo)
    assert len(calls) == 3 and run["status"] == "failed"
    assert "MoMo sandbox unavailable" in run["last_error"]
    final = [a for a in alerts.sent if a.kind == "final_failure"]
    assert [a.dedupe_key for a in final] == ["payout:2027-W03"]
    clock.advance(hours=1)
    runner.tick()
    assert len(calls) == 3  # failed runs are not picked up again
    assert len([a for a in alerts.sent if a.kind == "final_failure"]) == 1


def test_backoff_doubles_and_is_capped_at_one_hour():
    def boom(ctx):
        raise RuntimeError("x")

    repo, runner, clock, _ = make(boom, max_attempts=20, backoff_base=timedelta(minutes=1))
    runner.enqueue("demo", "k", run_at=T0)
    delays = []
    for _ in range(9):
        runner.tick()
        (run,) = runs(repo)
        due = datetime.fromisoformat(run["run_at"])
        delays.append(due - clock())
        clock.now = due
    assert delays[:4] == [timedelta(minutes=m) for m in (1, 2, 4, 8)]
    assert max(delays) == timedelta(hours=1)


def test_alert_after_n_consecutive_failed_runs_once_per_streak_and_resets_on_success():
    outcomes = iter([False, False, False, False, True, False, False, False])

    def handler(ctx):
        if not next(outcomes):
            raise RuntimeError("down")
        return Done()

    repo, runner, clock, alerts = make(handler, max_attempts=99, backoff_base=timedelta(seconds=1))
    for i in range(8):
        runner.enqueue("demo", f"k{i}", run_at=T0)
        runner.tick()
        clock.advance(minutes=10)
    kinds = [(a.kind, a.count) for a in alerts.sent if a.kind == "consecutive_failures"]
    # streak of 4 failures alerts once at 3; success resets; the next streak alerts at 3 again
    assert kinds == [("consecutive_failures", 3), ("consecutive_failures", 3)]


def test_a_broken_alert_sink_does_not_break_the_tick():
    class Boom:
        def send(self, alert):
            raise RuntimeError("sentry down")

    repo = HookRepository()
    spec = JobSpec("demo", lambda ctx: 1 / 0, max_attempts=1)
    runner = Runner(repo, [spec], alert=Boom(), clock=FakeClock())
    runner.enqueue("demo", "k", run_at=T0)
    result = runner.tick()
    assert result.failed == ["k"]


# ---------------------------------------------------------------- timeout
def test_job_exceeding_its_timeout_is_failed_and_retried():
    started = threading.Event()
    ctxs = []

    def slow(ctx):
        ctxs.append(ctx)
        started.set()
        ctx.cancelled.wait(5)
        return Done()

    repo, runner, clock, _ = make(
        slow, timeout=timedelta(milliseconds=50), backoff_base=timedelta(minutes=1)
    )
    runner.enqueue("demo", "slow:1", run_at=T0)
    result = runner.tick()
    (run,) = runs(repo)
    assert result.retried == ["slow:1"]
    assert run["status"] == "queued" and run["attempts"] == 1
    assert "timed out" in run["last_error"]
    assert ctxs[0].cancelled.is_set()  # cooperative handlers are told to stop
    (att,) = repo.list("job_attempt")
    assert att["outcome"] == "failed" and "timed out" in att["error"]


def test_timed_out_job_that_keeps_failing_ends_failed_with_alert():
    repo, runner, clock, alerts = make(
        lambda ctx: time.sleep(0.2) or Done(),
        timeout=timedelta(milliseconds=20),
        max_attempts=2,
        backoff_base=timedelta(seconds=1),
    )
    runner.enqueue("demo", "slow:2", run_at=T0)
    runner.tick()
    clock.advance(minutes=1)
    runner.tick()
    (run,) = runs(repo)
    assert run["status"] == "failed"
    assert [a.kind for a in alerts.sent if a.kind == "final_failure"] == ["final_failure"]


# ---------------------------------------------------------------- crashes (worker killed)
def crash_when(repo, op, table, match=lambda data: True, nth=1):
    seen = {"n": 0}

    def hook(o, t, rid, data):
        if o == op and t == table and match(data or {}):
            seen["n"] += 1
            if seen["n"] == nth:
                raise Crash()

    repo.before = hook


CRASH_POINTS = {
    # name: (op, table, match, handler invocations expected overall)
    "after_attempt_row_before_run_update": (
        "update",
        "job_run",
        lambda d: d.get("status") == "running",
        1,
    ),
    "after_handler_before_attempt_outcome": ("update", "job_attempt", lambda d: "outcome" in d, 2),
    "after_attempt_outcome_before_run_update": (
        "update",
        "job_run",
        lambda d: d.get("status") == "completed",
        1,
    ),
}


@pytest.mark.parametrize("point", list(CRASH_POINTS))
def test_killing_the_worker_at_any_point_never_double_applies(point):
    op, table, match, invocations = CRASH_POINTS[point]
    fx = Effects()
    repo, runner, clock, _ = make(fx.handler)
    runner.enqueue("demo", "payout:2027-W10", run_at=T0)
    crash_when(repo, op, table, match)
    with pytest.raises(Crash):
        runner.tick()
    repo.before = None
    clock.advance(minutes=1)
    runner.tick()  # lease / claim still young: nobody else touches it
    clock.advance(minutes=6)
    _, runner2, _, _ = make(fx.handler, repo=repo, clock=clock)  # a new process
    runner2.tick()
    clock.advance(minutes=1)
    runner2.tick()
    (run,) = runs(repo)
    assert run["status"] == "completed"
    assert fx.applied == {"payout:2027-W10#chunk0": 1}  # the effect applied exactly once
    assert len(fx.invocations) == invocations


def test_killing_the_worker_inside_the_handler_applies_the_effect_once():
    fx = Effects()
    state = {"first": True}

    def handler(ctx):
        fx.handler(ctx)  # effect applied (idempotent by key) ...
        if state["first"]:
            state["first"] = False
            raise Crash()  # ... then the process dies before returning

    repo, runner, clock, _ = make(handler)
    runner.enqueue("demo", "payout:2027-W14", run_at=T0)
    with pytest.raises(Crash):
        runner.tick()
    clock.advance(minutes=6)
    runner.tick()
    assert fx.applied == {"payout:2027-W14#chunk0": 1}
    assert runs(repo)[0]["status"] == "completed"


def test_crash_after_outcome_recorded_does_not_rerun_the_handler():
    fx = Effects()
    repo, runner, clock, _ = make(fx.handler)
    runner.enqueue("demo", "payout:2027-W11", run_at=T0)
    crash_when(repo, "update", "job_run", lambda d: d.get("status") == "completed")
    with pytest.raises(Crash):
        runner.tick()
    repo.before = None
    clock.advance(minutes=6)
    runner.tick()
    assert len(fx.invocations) == 1  # reconciled from the attempt row, not re-run
    (run,) = runs(repo)
    assert run["status"] == "completed"


def test_claim_orphaned_by_crash_between_the_two_writes_is_recovered_once():
    fx = Effects()
    repo, runner, clock, _ = make(fx.handler)
    runner.enqueue("demo", "payout:2027-W04", run_at=T0)
    (run,) = runs(repo)
    repo.create(
        "job_attempt",
        row_id("job_attempt", run["$id"], 0, 1),
        {"run_id": run["$id"], "attempt": 1, "started_at": T0.isoformat(), "outcome": None},
    )
    clock.advance(minutes=2)
    runner.tick()
    assert fx.invocations == []  # claim younger than the lease: left alone
    clock.advance(minutes=5)
    runner.tick()
    runner.tick()
    assert fx.invocations == ["payout:2027-W04#chunk0"]
    (run,) = runs(repo)
    assert run["status"] == "completed" and run["attempts"] == 1


def test_orphan_takeover_is_atomic_across_concurrent_ticks():
    fx, workers = Effects(), 8

    def handler(ctx):
        time.sleep(0.005)
        return fx.handler(ctx)

    repo = BarrierRepo(workers)
    clock = FakeClock()
    runner = Runner(repo, [JobSpec("demo", handler)], alert=FakeAlertSink(), clock=clock)
    runner.enqueue("demo", "payout:2027-W05", run_at=T0)
    (run,) = runs(repo)
    repo.create(
        "job_attempt",
        row_id("job_attempt", run["$id"], 0, 1),
        {"run_id": run["$id"], "attempt": 1, "started_at": T0.isoformat(), "outcome": None},
    )
    clock.advance(minutes=6)
    repo.armed = True
    threads = [threading.Thread(target=runner.tick) for _ in range(workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert fx.invocations == ["payout:2027-W05#chunk0"]


def test_claimer_stalled_past_the_lease_does_not_double_run_after_takeover():
    """Claimer A creates its attempt row, then its job_run update stalls past the lease.
    B takes over and finishes. A's late update lands, A verifies, sees it was superseded
    and must not run the handler; the run stays completed."""
    fx = Effects()
    repo, runner_a, clock, _ = make(fx.handler)
    _, runner_b, _, _ = make(fx.handler, repo=repo, clock=clock)
    runner_a.enqueue("demo", "payout:2027-W12", run_at=T0)
    fired = {"done": False}

    def stall(op, table, rid, data):
        if (
            op == "update"
            and table == "job_run"
            and data.get("status") == "running"
            and not fired["done"]
        ):
            fired["done"] = True
            clock.advance(minutes=6)  # A is stuck in the Appwrite call for 6 minutes
            runner_b.tick()  # meanwhile B takes over the orphaned claim and completes the run

    repo.before = stall
    result = runner_a.tick()
    assert fx.invocations == ["payout:2027-W12#chunk0"]  # only B's
    assert result.lost_claims == 1 and result.completed == []
    clock.advance(minutes=10)
    runner_a.tick()
    runner_b.tick()
    (run,) = runs(repo)
    assert run["status"] == "completed"
    assert len(fx.invocations) == 1


def test_claimer_whose_own_lease_ran_out_before_its_update_stands_down():
    fx = Effects()
    repo, runner, clock, _ = make(fx.handler)
    runner.enqueue("demo", "payout:2027-W13", run_at=T0)

    def stall(op, table, rid, data):
        if op == "update" and table == "job_run" and data.get("status") == "running":
            clock.advance(minutes=6)  # nobody took over, but our claim is stale

    repo.before = stall
    result = runner.tick()
    repo.before = None
    assert fx.invocations == [] and result.lost_claims == 1
    clock.advance(minutes=6)
    runner.tick()  # recovered later, once
    assert fx.invocations == ["payout:2027-W13#chunk0"]


def test_crashed_worker_lease_expires_and_run_is_reclaimed_once():
    fx = Effects()
    repo, runner, clock, _ = make(fx.handler)
    runner.enqueue("demo", "auto-confirm:2027-01-18T09", run_at=T0)
    crash_when(repo, "update", "job_attempt", lambda d: "outcome" in d)  # dies after the handler
    with pytest.raises(Crash):
        runner.tick()
    repo.before = None
    clock.advance(minutes=2)
    runner.tick()
    assert len(fx.invocations) == 1  # lease valid: nobody else runs it
    clock.advance(minutes=5)
    runner.tick()
    (run,) = runs(repo)
    assert run["status"] == "completed" and run["attempts"] == 2
    assert "lease expired" in run["last_error"]
    assert fx.applied["auto-confirm:2027-01-18T09#chunk0"] == 1


def test_lease_expiry_on_final_attempt_fails_and_alerts_once_without_rerunning():
    fx = Effects()
    repo, runner, clock, alerts = make(fx.handler, max_attempts=1)
    runner.enqueue("demo", "payout:2027-W06", run_at=T0)
    # attempt 1 runs the handler, then the worker dies before recording the outcome
    crash_when(repo, "update", "job_attempt", lambda d: "outcome" in d)
    with pytest.raises(Crash):
        runner.tick()
    repo.before = None
    clock.advance(minutes=6)
    workers = [threading.Thread(target=runner.tick) for _ in range(4)]
    for w in workers:
        w.start()
    for w in workers:
        w.join()
    (run,) = runs(repo)
    assert run["status"] == "failed" and "lease expired" in run["last_error"]
    assert len(fx.invocations) == 1  # not run again
    assert [a.kind for a in alerts.sent if a.kind == "final_failure"] == ["final_failure"]


# ---------------------------------------------------------------- chunks
def test_long_job_continues_in_chunks_without_redoing_work():
    processed, items = [], list(range(250))

    def batch(ctx):
        start = ctx.cursor or 0
        chunk = items[start : start + 100]
        processed.extend((ctx.effect_key, i) for i in chunk)
        if start + 100 < len(items):
            return Continue(start + 100, items=len(chunk))
        return Done(items=len(chunk))

    repo, runner, clock, _ = make(batch)
    runner.enqueue("demo", "tier-eval:2027-01", run_at=T0)
    for _ in range(5):
        runner.tick()
        clock.advance(minutes=1)
    assert [i for _, i in processed] == items
    assert {k for k, _ in processed} == {f"tier-eval:2027-01#chunk{n}" for n in range(3)}
    (run,) = runs(repo)
    assert run["status"] == "completed" and json.loads(run["payload"])["_items"] == 250


def test_crash_after_a_chunk_finished_continues_from_the_recorded_cursor():
    seen = []

    def batch(ctx):
        seen.append(ctx.cursor)
        return Continue((ctx.cursor or 0) + 1, items=1) if (ctx.cursor or 0) < 1 else Done(items=1)

    repo, runner, clock, _ = make(batch)
    runner.enqueue("demo", "tier-eval:2027-02", run_at=T0)
    crash_when(repo, "update", "job_run", lambda d: d.get("status") == "queued" and "payload" in d)
    with pytest.raises(Crash):
        runner.tick()
    repo.before = None
    clock.advance(minutes=6)
    for _ in range(3):
        runner.tick()
        clock.advance(minutes=1)
    assert seen == [None, 1]  # chunk 0 not redone; chunk 1 started from the recorded cursor
    assert runs(repo)[0]["status"] == "completed"
