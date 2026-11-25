"""S01 (#93): per-teacher serialisation. Lease (row based, fenced, expiring) + re-check-after-insert
rollback.

The M02 residual risk: two overlapping sessions with DIFFERENT starts both pass the service check
and both insert,
because the unique teacher_slot_key only catches the same start. Fault injection below forces that
interleaving.
"""

import threading
from datetime import UTC, datetime, timedelta

import pytest

from hs_core.scheduling import rules
from hs_core.scheduling.leases import LeaseBusy, LeaseLost, TeacherLeases
from hs_core.scheduling.writer import AlreadyGenerated, SessionWriter
from jobs_helpers import FakeClock
from sched_helpers import NOW, UniqueRepo, iso, live_held, overlapping_pairs

D = datetime(2026, 12, 1, 13, 0, tzinfo=UTC)  # Tuesday 16:00 Kampala


def srow(teacher="t1", start=D, minutes=60, **kw):
    r = {
        "child_id": "c1",
        "teacher_id": teacher,
        "subscription_id": "s1",
        "home_location_id": "h1",
        "scheduled_start_at": iso(start),
        "scheduled_end_at": iso(start + timedelta(minutes=minutes)),
        "status": "scheduled",
        "status_changed_at": iso(NOW),
    }
    r.update(kw)
    return r


def make(repo=None, ttl=30, wait=0.0):
    repo = repo or UniqueRepo()
    clock = FakeClock(NOW)
    leases = TeacherLeases(
        repo, clock, ttl=timedelta(seconds=ttl), wait=timedelta(seconds=wait), sleep=lambda s: None
    )
    return repo, clock, leases, SessionWriter(repo, leases, clock)


# ---------------------------------------------------------------- leases
def test_lease_is_exclusive_per_teacher_but_not_across_teachers():
    repo, clock, leases, _ = make()
    a = leases.acquire("t1")
    with pytest.raises(LeaseBusy):
        leases.acquire("t1")
    b = leases.acquire("t2")
    assert a.generation == 1 and b.generation == 1
    leases.release(a)
    assert leases.acquire("t1").generation == 2  # generations only grow (fencing)


def test_expired_lease_is_taken_over_and_the_stalled_holder_is_fenced_out():
    repo, clock, leases, _ = make(ttl=30)
    old = leases.acquire("t1")
    clock.advance(seconds=31)
    new = leases.acquire("t1")
    assert new.generation == old.generation + 1
    with pytest.raises(LeaseLost):
        old.check(remote=True)
    new.check(remote=True)


def test_local_expiry_is_noticed_without_a_read():
    repo, clock, leases, _ = make(ttl=30)
    lease = leases.acquire("t1")
    clock.advance(seconds=29)
    lease.check()
    clock.advance(seconds=2)
    with pytest.raises(LeaseLost):
        lease.check()


def test_takeover_of_an_expired_lease_has_exactly_one_winner_among_many_threads():
    repo, clock, leases, _ = make(ttl=30)
    leases.acquire("t1")
    clock.advance(seconds=60)
    wins, busy, lock = [], [], threading.Lock()
    gate = threading.Barrier(24)

    def go():
        gate.wait()
        try:
            lease = leases.acquire("t1")
            with lock:
                wins.append(lease.generation)
        except LeaseBusy:
            with lock:
                busy.append(1)

    ts = [threading.Thread(target=go) for _ in range(24)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert wins == [2] and len(busy) == 23


def test_hold_waits_for_release_and_gives_up_after_the_wait():
    repo, clock, leases, _ = make(wait=2)
    slept = []
    leases._sleep = slept.append
    first = leases.acquire("t1")
    with pytest.raises(LeaseBusy), leases.hold("t1"):
        pass
    assert slept  # it did wait before giving up
    leases.release(first)
    with leases.hold("t1") as lease:
        assert lease.generation == 2


def test_hold_releases_on_error_and_keeps_the_table_small():
    repo, clock, leases, _ = make()
    for _ in range(6):
        with pytest.raises(RuntimeError), leases.hold("t1"):
            raise RuntimeError("boom")
    assert len(repo.list("teacher_schedule_lease")) <= 2
    assert leases.acquire("t1").generation == 7


def test_release_after_losing_the_lease_does_not_disturb_the_new_holder():
    repo, clock, leases, _ = make(ttl=30)
    old = leases.acquire("t1")
    clock.advance(seconds=31)
    new = leases.acquire("t1")
    leases.release(old)  # a late release from the stalled holder
    new.check(remote=True)
    with pytest.raises(LeaseBusy):
        leases.acquire("t1")


# ---------------------------------------------------------------- writer: basic rules
def test_create_inserts_and_reports_overlap_without_writing():
    repo, clock, leases, w = make()
    first = w.create(srow())
    assert first["teacher_slot_key"] and first["status"] == "scheduled"
    n = len(repo.list("session"))
    with pytest.raises(rules.SessionConflict) as e:
        w.create(srow(start=D + timedelta(minutes=30)))
    assert e.value.conflicting_id == first["$id"]
    assert len(repo.list("session")) == n


def test_back_to_back_is_allowed_and_other_teachers_do_not_conflict():
    repo, clock, leases, w = make()
    w.create(srow())
    w.create(srow(start=D + timedelta(hours=1)))
    w.create(srow(teacher="t2"))
    assert len(repo.list("session")) == 3


def test_cancelled_and_deleted_sessions_do_not_block():
    repo, clock, leases, w = make()
    s = w.create(srow())
    repo.update("session", s["$id"], rules.transition_patch(s, "cancelled_by_teacher", iso(NOW)))
    w.create(srow())
    assert len(live_held(repo, "t1")) == 1


def test_end_must_be_after_start():
    repo, clock, leases, w = make()
    with pytest.raises(rules.InvalidSession):
        w.create(srow(minutes=0))
    with pytest.raises(rules.InvalidSession):
        w.create(srow(minutes=-30))
    assert not repo.list("session")


def test_same_generation_key_is_reported_as_already_generated_not_a_conflict():
    repo, clock, leases, w = make()
    a = w.create(srow(assignment_id="a1", slot_ref="2-960:2026-12-01"))
    with pytest.raises(AlreadyGenerated) as e:
        w.create(srow(assignment_id="a1", slot_ref="2-960:2026-12-01"))
    assert e.value.existing["$id"] == a["$id"]
    assert len(repo.list("session")) == 1


def test_same_start_is_caught_by_the_unique_backstop_when_the_check_is_bypassed():
    repo, clock, leases, w = make()
    w.create(srow())
    w._find_conflict = lambda *a, **k: None  # the service check misses it (concurrent request)
    with pytest.raises(rules.SessionConflict):
        w.create(srow())
    assert len(live_held(repo, "t1")) == 1


def test_lease_is_released_after_create_even_on_conflict():
    repo, clock, leases, w = make()
    w.create(srow())
    with pytest.raises(rules.SessionConflict):
        w.create(srow(start=D + timedelta(minutes=15)))
    assert leases.acquire("t1")  # not left locked


def test_busy_lease_surfaces_as_LeaseBusy_and_writes_nothing():
    repo, clock, leases, w = make()
    leases.acquire("t1")
    with pytest.raises(LeaseBusy):
        w.create(srow())
    assert not repo.list("session")


# ---------------------------------------------------------------- the residual race and its two
# defences
def run_race(writer_factory, n=50, step_minutes=10):
    """n threads, one teacher, each wants a 60-minute session starting a different multiple of
    `step_minutes` apart:
    most pairs overlap. All threads start together (barrier)."""
    repo = UniqueRepo()
    clock = FakeClock(NOW)
    leases = TeacherLeases(repo, clock, ttl=timedelta(seconds=30), wait=timedelta(seconds=20))
    writer = writer_factory(repo, leases, clock)
    gate = threading.Barrier(n)
    results = []
    lock = threading.Lock()

    def worker(i):
        row = srow(start=D + timedelta(minutes=i * step_minutes))
        gate.wait()
        try:
            writer.create(row)
            out = "ok"
        except rules.SessionConflict:
            out = "conflict"
        except LeaseBusy:
            out = "busy"
        with lock:
            results.append(out)

    ts = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    return repo, results


def pause_between_check_and_insert(repo, gate_n):
    """Hold every thread after its service check (before the insert) until all of them arrived:
    forces
    the race."""
    barrier = threading.Barrier(gate_n, timeout=2)

    def before(op, table, row_id, data):
        if op == "create" and table == "session":
            try:
                barrier.wait()
            except threading.BrokenBarrierError:
                pass

    repo.before = before


def test_fifty_threads_on_one_teacher_never_leave_overlapping_sessions():
    repo, results = run_race(lambda r, ls, c: SessionWriter(r, ls, c), n=50)
    assert overlapping_pairs(repo, "t1") == []
    assert results.count("ok") == len(live_held(repo, "t1")) >= 1
    assert results.count("ok") + results.count("conflict") == 50 and "busy" not in results
    # the lease did its job: nobody had to be rolled back (a rollback means two writers interleaved)
    assert not [r for r in repo.list("session") if r.get("deleted_at")]


def test_without_the_lease_the_recheck_still_rolls_back_the_later_overlapping_row(monkeypatch):
    """Lease disabled (e.g. expired mid-operation): all threads pass the check and insert; the
    re-check
    must clean up."""
    import contextlib

    monkeypatch.setattr(
        TeacherLeases, "hold", lambda self, teacher_id: contextlib.nullcontext(None)
    )
    repo = UniqueRepo()
    n = 12
    pause_between_check_and_insert(repo, n)
    clock = FakeClock(NOW)
    writer = SessionWriter(repo, TeacherLeases(repo, clock), clock)
    gate = threading.Barrier(n)
    outcomes = []

    def worker(i):
        gate.wait()
        try:
            writer.create(srow(start=D + timedelta(minutes=i * 10)))
            outcomes.append("ok")
        except rules.SessionConflict as e:
            outcomes.append("raced" if e.raced else "conflict")

    ts = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert overlapping_pairs(repo, "t1") == []
    assert "raced" in outcomes  # the insert-then-recheck path really ran
    rolled_back = [r for r in repo.list("session") if r.get("deleted_at")]
    assert rolled_back and all(
        r["teacher_slot_key"] is None for r in rolled_back
    )  # soft-deleted, slot released


def test_the_earliest_created_row_wins_the_recheck():
    repo, clock, leases, w = make()
    first = w.create(srow(start=D))
    # a rogue writer (no lease, no check) inserts an overlapping row later
    later = repo.create(
        "session", "late", rules.with_session_keys(srow(start=D + timedelta(minutes=20)))
    )
    with pytest.raises(rules.SessionConflict) as e:
        w._recheck_or_rollback(later, clock())
    assert e.value.raced
    assert repo.get("session", "late").get("deleted_at")
    assert not repo.get("session", first["$id"]).get("deleted_at")


def test_mutation_without_lease_and_without_recheck_the_race_leaves_overlaps(monkeypatch):
    """Proves the race tests have teeth: remove both defences and the invariant is violated."""
    import contextlib

    monkeypatch.setattr(
        TeacherLeases, "hold", lambda self, teacher_id: contextlib.nullcontext(None)
    )
    monkeypatch.setattr(SessionWriter, "_recheck_or_rollback", lambda self, *a, **k: None)
    repo = UniqueRepo()
    n = 10
    pause_between_check_and_insert(repo, n)
    clock = FakeClock(NOW)
    writer = SessionWriter(repo, TeacherLeases(repo, clock), clock)
    gate = threading.Barrier(n)

    def worker(i):
        gate.wait()
        try:
            writer.create(srow(start=D + timedelta(minutes=i * 10)))
        except rules.SessionConflict:
            pass

    ts = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert overlapping_pairs(repo, "t1"), "the harness failed to reproduce the M02 residual race"


def test_a_stalled_holder_whose_lease_expired_cannot_insert():
    repo, clock, leases, w = make(ttl=30)
    stale = leases.acquire("t1")
    clock.advance(seconds=31)
    leases.acquire("t1")  # takeover by another worker
    with pytest.raises(LeaseLost):
        w.create(srow(), lease=stale)
    assert not repo.list("session")


def test_crash_after_insert_before_recheck_is_cleaned_by_the_next_writer():
    """Worker dies right after its insert (no recheck, lease unreleased). Another overlapping insert
    later: the
    survivor check still keeps one."""
    repo, clock, leases, w = make(ttl=30)
    repo.create("session", "orphan", rules.with_session_keys(srow(start=D + timedelta(minutes=20))))
    clock.advance(seconds=31)  # the crashed worker's lease (if any) expired
    with pytest.raises(rules.SessionConflict):
        w.create(srow(start=D))
    assert overlapping_pairs(repo, "t1") == []


# ---------------------------------------------------------------- reschedule through the same path
def test_reschedule_checks_overlap_excludes_itself_and_marks_overridden():
    repo, clock, leases, w = make()
    a = w.create(srow(start=D, assignment_id="a1", slot_ref="2-960:2026-12-01"))
    w.create(srow(start=D + timedelta(hours=3)))
    moved = w.reschedule(
        a["$id"], D + timedelta(minutes=30), D + timedelta(minutes=90)
    )  # overlaps only itself
    assert moved["overridden"] is True and moved["scheduled_start_at"] == iso(
        D + timedelta(minutes=30)
    )
    assert (
        moved["slot_ref"] == "2-960:2026-12-01" and moved["generation_key"] == "a1:2-960:2026-12-01"
    )
    assert moved["teacher_slot_key"].endswith("20261201T133000Z")
    with pytest.raises(rules.SessionConflict):
        w.reschedule(
            a["$id"], D + timedelta(hours=2, minutes=30), D + timedelta(hours=3, minutes=30)
        )


def test_reschedule_races_do_not_overlap_either():
    repo, clock, leases, w = make(wait=20)
    leases._wait = timedelta(seconds=20)
    ids = [w.create(srow(start=D + timedelta(hours=3 * i)))["$id"] for i in range(10)]
    n = 10
    gate = threading.Barrier(n)

    def worker(i):
        gate.wait()
        try:
            w.reschedule(ids[i], D + timedelta(hours=1), D + timedelta(hours=2))
        except (rules.SessionConflict, LeaseBusy):
            pass

    ts = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert overlapping_pairs(repo, "t1") == []


def test_reschedule_end_must_follow_start():
    repo, clock, leases, w = make()
    a = w.create(srow())
    with pytest.raises(rules.InvalidSession):
        w.reschedule(a["$id"], D, D)


def test_soft_delete_releases_both_keys_and_restore_recomputes_them():
    repo, clock, leases, w = make()
    a = w.create(srow(assignment_id="a1", slot_ref="2-960:2026-12-01"))
    w.soft_delete(a["$id"], clock())
    gone = repo.get("session", a["$id"])
    assert gone["teacher_slot_key"] is None and gone["generation_key"] is None
    b = w.create(
        srow(assignment_id="a1", slot_ref="2-960:2026-12-01")
    )  # the slot and the identity are free again
    with pytest.raises(rules.SessionConflict):  # restoring while taken: 409, never two active rows
        w.restore(a["$id"])
    w.soft_delete(b["$id"], clock())
    restored = w.restore(a["$id"])
    assert restored["teacher_slot_key"] and restored["generation_key"] == "a1:2-960:2026-12-01"


def test_list_all_pages_with_cursors_and_returns_every_row_once_in_order():
    from hs_core.scheduling.writer import list_all

    repo = UniqueRepo()
    for i in range(257):
        repo.create("scheduling_issue", f"r{i:04d}", {"assignment_id": "a1", "n": i})
    rows = list_all(repo, "scheduling_issue", page=100)
    assert [r["n"] for r in rows] == list(range(257))
    assert (
        list_all(repo, "scheduling_issue", page=257)[-1]["n"] == 256
    )  # exact multiple of the page size ends cleanly
    assert list_all(repo, "teacher_schedule_lease") == []
