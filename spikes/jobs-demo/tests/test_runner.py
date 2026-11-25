import threading
import time
from datetime import UTC, datetime, timedelta

from jobs_demo.runner import Continue, Done, JobSpec, Runner, Schedule
from jobs_demo.store import MemoryStore, row_id

T0 = datetime(2027, 1, 18, 9, 0, tzinfo=UTC)
EVERY_5 = Schedule(timedelta(minutes=5), anchor=T0)


def make(handler, **spec_kwargs):
    store, alerts = MemoryStore(), []
    spec = JobSpec("demo", handler, **spec_kwargs)
    return store, Runner(store, [spec], alert=alerts.append), alerts


def runs(store):
    return sorted(store.list_rows("job_run"), key=lambda r: r["run_at"])


def test_scheduled_job_runs_once_per_period_even_if_ticked_repeatedly():
    effects = []
    store, runner, _ = make(lambda ctx: effects.append(ctx.dedupe_key) or Done(), schedule=EVERY_5,
                            catch_up=timedelta(0))
    for _ in range(3):
        runner.tick(T0 + timedelta(seconds=30))
    assert effects == [f"demo:{T0.isoformat()}"]
    assert [r["status"] for r in runs(store)] == ["completed"]


def test_missed_ticks_are_caught_up_not_lost():
    effects = []
    store, runner, _ = make(lambda ctx: effects.append(ctx.scheduled_for) or Done(), schedule=EVERY_5,
                            catch_up=timedelta(hours=2))
    runner.tick(T0)
    # Function or core service down for an hour; the next tick sees 12 more periods due.
    result = runner.tick(T0 + timedelta(hours=1))
    assert result.created == 12
    assert effects == [T0 + timedelta(minutes=5 * i) for i in range(13)]


class BarrierStore(MemoryStore):
    """All ticking threads take their snapshot of job_run, then race to claim it."""

    def __init__(self, parties):
        super().__init__()
        self._barrier = threading.Barrier(parties, timeout=10)
        self.armed = False

    def list_rows(self, table):
        rows = super().list_rows(table)
        if table == "job_run" and self.armed:
            self._barrier.wait()
        return rows


def test_concurrent_ticks_apply_each_run_exactly_once():
    effects, lock = [], threading.Lock()

    def handler(ctx):
        time.sleep(0.005)  # long enough that a second claimer would overlap
        with lock:
            effects.append(ctx.dedupe_key)
        return Done()

    workers = 8
    store = BarrierStore(workers)
    runner = Runner(store, [JobSpec("demo", handler, schedule=EVERY_5, catch_up=timedelta(minutes=30))])
    results = []
    store.armed = True
    threads = [threading.Thread(target=lambda: results.append(runner.tick(T0 + timedelta(minutes=30))))
               for _ in range(workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    store.armed = False
    assert len(effects) == len(set(effects)) == 7  # periods at 0, 5, ... 30 minutes
    assert sum(len(r.completed) for r in results) == 7
    # Every thread saw all 7 runs as claimable, so 7 x (8 - 1) claims must have been lost.
    assert sum(r.lost_claims for r in results) == 7 * (workers - 1)
    assert all(r["status"] == "completed" for r in runs(store))


def test_failures_retry_with_backoff_then_fail_and_alert():
    calls = []

    def flaky(ctx):
        calls.append(ctx.dedupe_key)
        raise RuntimeError("MoMo sandbox unavailable")

    store, runner, alerts = make(flaky, max_attempts=3, backoff_base=timedelta(minutes=1))
    runner.enqueue("demo", "payout:2027-W03", run_at=T0)

    runner.tick(T0)                                    # attempt 1 fails -> retry in 1 min
    (run,) = runs(store)
    assert (run["status"], run["attempts"], run["run_at"]) == ("queued", 1, (T0 + timedelta(minutes=1)).isoformat())
    runner.tick(T0 + timedelta(seconds=30))            # not due yet
    assert len(calls) == 1
    runner.tick(T0 + timedelta(minutes=1))             # attempt 2 fails -> retry in 2 min
    runner.tick(T0 + timedelta(minutes=3))             # attempt 3 fails -> failed
    (run,) = runs(store)
    assert len(calls) == 3 and run["status"] == "failed" and "MoMo sandbox unavailable" in run["last_error"]
    assert [a["dedupe_key"] for a in alerts] == ["payout:2027-W03"]
    runner.tick(T0 + timedelta(hours=1))
    assert len(calls) == 3                              # failed runs are not picked up again


def test_crashed_worker_lease_expires_and_run_is_reclaimed_once():
    effects = []
    store, runner, _ = make(lambda ctx: effects.append(ctx.dedupe_key) or Done())
    runner.enqueue("demo", "auto-confirm:2027-01-18T09", run_at=T0)
    (run,) = runs(store)
    runner._claim(run, T0)                              # a worker claims it, then dies mid-run
    runner.tick(T0 + timedelta(minutes=2))              # lease (5 min) still valid: nobody else runs it
    assert effects == []
    runner.tick(T0 + timedelta(minutes=6))              # lease expired: reclaimed as attempt 2
    (run,) = runs(store)
    assert effects == ["auto-confirm:2027-01-18T09"]
    assert run["status"] == "completed" and run["attempts"] == 2
    assert "lease expired" in run["last_error"]


def test_long_job_continues_in_chunks_without_redoing_work():
    processed, items = [], list(range(250))

    def batch(ctx):
        start = ctx.cursor or 0
        chunk = items[start:start + 100]
        processed.extend((ctx.effect_key, i) for i in chunk)
        return Continue(start + 100) if start + 100 < len(items) else Done()

    store, runner, _ = make(batch)
    runner.enqueue("demo", "tier-eval:2027-01", run_at=T0)
    for minute in range(5):
        runner.tick(T0 + timedelta(minutes=minute))
    assert [i for _, i in processed] == items           # every item once, in order
    assert {k for k, _ in processed} == {f"tier-eval:2027-01#chunk{n}" for n in range(3)}
    assert runs(store)[0]["status"] == "completed"


def test_enqueue_is_idempotent_and_supports_delay_and_manual_runs():
    effects = []
    store, runner, _ = make(lambda ctx: effects.append(ctx.payload) or Done())
    assert runner.enqueue("demo", "reminder:sub-1:2027-02-01", run_at=T0 + timedelta(days=1), payload={"sub": 1})
    assert not runner.enqueue("demo", "reminder:sub-1:2027-02-01", run_at=T0, payload={"sub": 1})
    runner.tick(T0)
    assert effects == []                                 # delayed until its run_at
    runner.enqueue("demo", "manual:recalc:1", run_at=T0, trigger="manual", actor_id="admin-1",
                   reason="re-run after fixing rates")
    runner.tick(T0 + timedelta(days=1))
    assert effects == [{"sub": 1}, {}] or effects == [{}, {"sub": 1}]
    manual = next(r for r in runs(store) if r["trigger"] == "manual")
    assert (manual["actor_id"], manual["reason"]) == ("admin-1", "re-run after fixing rates")


def test_daily_schedule_uses_kampala_time():
    daily = Schedule(timedelta(days=1), anchor=datetime(2026, 1, 1, 2, 0, tzinfo=__import__("zoneinfo").ZoneInfo("Africa/Kampala")))
    periods = daily.due_periods(datetime(2027, 1, 17, 0, 0, tzinfo=UTC), datetime(2027, 1, 18, 0, 0, tzinfo=UTC))
    assert periods == [datetime(2027, 1, 17, 23, 0, tzinfo=UTC)]  # 02:00 in Kampala = 23:00 UTC previous day


def test_claim_orphaned_by_crash_between_the_two_writes_is_recovered_once():
    """Worker created the job_attempt row, then died before updating job_run."""
    effects = []
    store, runner, _ = make(lambda ctx: effects.append(ctx.dedupe_key) or Done())
    runner.enqueue("demo", "payout:2027-W04", run_at=T0)
    (run,) = runs(store)
    store.create_row("job_attempt", row_id("job_attempt", run["$id"], 0, 1),
                     {"run_id": run["$id"], "attempt": 1, "started_at": T0.isoformat()})
    assert runs(store)[0]["status"] == "queued" and runs(store)[0]["attempts"] == 0

    runner.tick(T0 + timedelta(minutes=2))      # claim is younger than the lease: leave it alone
    assert effects == []
    runner.tick(T0 + timedelta(minutes=6))      # lease passed: orphan is taken over
    runner.tick(T0 + timedelta(minutes=7))      # and never re-run
    assert effects == ["payout:2027-W04"]
    (run,) = runs(store)
    assert run["status"] == "completed" and run["attempts"] == 1


def test_orphan_takeover_is_atomic_across_concurrent_ticks():
    effects, lock = [], threading.Lock()

    def handler(ctx):
        time.sleep(0.005)
        with lock:
            effects.append(ctx.dedupe_key)
        return Done()

    workers = 8
    store = BarrierStore(workers)
    runner = Runner(store, [JobSpec("demo", handler)])
    runner.enqueue("demo", "payout:2027-W05", run_at=T0)
    (run,) = runs(store)
    store.create_row("job_attempt", row_id("job_attempt", run["$id"], 0, 1),
                     {"run_id": run["$id"], "attempt": 1, "started_at": T0.isoformat()})
    store.armed = True
    threads = [threading.Thread(target=runner.tick, args=(T0 + timedelta(minutes=6),)) for _ in range(workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert effects == ["payout:2027-W05"]


def test_lease_expiry_on_final_attempt_fails_and_alerts_without_rerunning():
    effects = []
    store, runner, alerts = make(lambda ctx: effects.append(1) or Done(), max_attempts=1)
    runner.enqueue("demo", "payout:2027-W06", run_at=T0)
    (run,) = runs(store)
    runner._claim(run, T0)                      # attempt 1 starts, then the worker dies
    runner.tick(T0 + timedelta(minutes=6))      # lease expired and no attempts left
    (run,) = runs(store)
    assert effects == [] and run["status"] == "failed" and "lease expired" in run["last_error"]
    assert [a["dedupe_key"] for a in alerts] == ["payout:2027-W06"]
