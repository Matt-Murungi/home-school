"""S01 (#93): property tests (seeded random worlds and operation sequences) and 50-thread races on
one
teacher.

Invariants after ANY sequence of generate / regenerate / cancel_future / parent cancel / reschedule
/ leave / plan and
status changes / manual bookings:
  I1 no teacher has two overlapping sessions that hold their slot;
  I2 no two live sessions share a generation_key and no live generated session shares (assignment,
  slot_ref);
  I3 running the nightly generation again changes nothing (N runs == 1 run);
  I4 cancelled/void/no_show sessions are never resurrected and the session count of an assignment
  never loses a cancelled row.
"""

import contextlib
import random
import threading
from datetime import UTC, date, datetime, timedelta

import pytest

from hs_core.jobs.alerts import FakeAlertSink
from hs_core.scheduling import rules
from hs_core.scheduling.generator import SessionGenerator
from hs_core.scheduling.leases import LeaseBusy, TeacherLeases
from hs_core.scheduling.writer import SessionWriter
from sched_helpers import NOW, World, iso, live_held, overlapping_pairs, snapshot

TUE_4PM = (2, 960, 1020)


def gen_for(w, **kw):
    kw.setdefault("alert", FakeAlertSink())
    return SessionGenerator(w.audited, clock=w.clock, **kw)


def state(repo):
    """Everything generation may write, order independent."""
    return (
        snapshot(repo),
        snapshot(repo, "match_request", ("session_id", "status", "type")),
        snapshot(repo, "scheduling_issue", ("assignment_id", "kind", "slot_ref", "status")),
        snapshot(repo, "cancellation", ("session_id", "reason")),
    )


def check_invariants(w, teachers):
    for t in teachers:
        assert overlapping_pairs(w.repo, t) == [], f"overlap for {t}"
    live = [s for s in w.repo.list("session") if not s.get("deleted_at")]
    keys = [s["generation_key"] for s in live if s.get("generation_key")]
    assert len(keys) == len(set(keys)), "duplicate generation_key"
    refs = [
        (s["assignment_id"], s["slot_ref"])
        for s in live
        if s.get("slot_ref") and s.get("assignment_id")
    ]
    assert len(refs) == len(set(refs)), "duplicate slot_ref"
    slot_keys = [s["teacher_slot_key"] for s in live if s.get("teacher_slot_key")]
    assert len(slot_keys) == len(set(slot_keys))


def random_world(rnd):
    w = World()
    teachers = [w.teacher() for _ in range(rnd.randint(1, 3))]
    aids = []
    for _ in range(rnd.randint(3, 8)):
        slots = {
            (rnd.randint(1, 7), rnd.choice([600, 630, 660, 900]), 0)
            for _ in range(rnd.randint(1, 3))
        }
        slots = [(wd, s, s + rnd.choice([30, 60, 90])) for wd, s, _ in slots]
        aids.append(
            w.assignment(
                rnd.choice(teachers),
                slots,
                per_week=rnd.randint(1, 3),
                trial=rnd.random() < 0.3,
                start=NOW - timedelta(days=rnd.randint(0, 20)),
            )
        )
    return w, teachers, aids


def apply_op(rnd, w, g, teachers, aids):
    op = rnd.choice(
        [
            "gen",
            "gen",
            "gen",
            "regen",
            "cancel_future",
            "parent_cancel",
            "reschedule",
            "leave",
            "advance",
            "plan",
            "status",
            "manual",
        ]
    )
    aid = rnd.choice(aids)
    today = (w.clock() + timedelta(hours=3)).date()
    sessions = [s for s in w.repo.list("session") if not s.get("deleted_at")]
    sub = w.repo.get("assignment", aid)["subscription_id"]
    if op == "gen":
        g.generate(aid)
    elif op == "regen":
        g.regenerate(aid, today + timedelta(days=rnd.randint(0, 10)))
    elif op == "cancel_future":
        g.cancel_future(aid, today + timedelta(days=rnd.randint(0, 10)), "paused")
    elif op == "parent_cancel" and sessions:
        s = rnd.choice(sessions)
        if s["status"] == "scheduled":
            w.repo.update(
                "session",
                s["$id"],
                rules.transition_patch(
                    s, rnd.choice(["cancelled_by_parent_late", "no_show"]), iso(w.clock())
                ),
            )
    elif op == "reschedule" and sessions:
        s = rnd.choice(sessions)
        if s["status"] == "scheduled" and s["teacher_id"] in teachers:
            start = w.clock() + timedelta(days=rnd.randint(1, 20), hours=rnd.randint(0, 8))
            with contextlib.suppress(rules.SessionConflict, LeaseBusy):
                g.writer.reschedule(
                    s["$id"], start, start + timedelta(minutes=rnd.choice([30, 60]))
                )
    elif op == "leave":
        t = rnd.choice(teachers)
        st = w.clock() + timedelta(days=rnd.randint(0, 25))
        w.leave(t, st, st + timedelta(hours=rnd.randint(1, 30)))
    elif op == "advance":
        w.clock.advance(days=rnd.randint(1, 3))
    elif op == "plan":
        w.repo.update("subscription", sub, {"plan_id": w.plan(rnd.randint(1, 3))})
    elif op == "status":
        w.repo.update(
            "subscription", sub, {"status": rnd.choice(["active", "active", "paused", "past_due"])}
        )
    elif op == "manual":
        t = rnd.choice(teachers)
        st = w.clock() + timedelta(days=rnd.randint(0, 20), hours=rnd.randint(0, 10))
        with contextlib.suppress(rules.SessionConflict, LeaseBusy):
            g.writer.create(
                {
                    "child_id": "cm",
                    "teacher_id": t,
                    "subscription_id": "sm",
                    "home_location_id": "hm",
                    "scheduled_start_at": iso(st),
                    "scheduled_end_at": iso(st + timedelta(hours=1)),
                    "status": "scheduled",
                    "status_changed_at": iso(w.clock()),
                }
            )
    return op


def generate_all(g, aids):
    for a in aids:
        g.generate(a)


@pytest.mark.parametrize("seed", range(40))
def test_random_operation_sequences_never_break_the_invariants(seed):
    rnd = random.Random(seed)
    w, teachers, aids = random_world(rnd)
    g = gen_for(w)
    cancelled_ids: set[str] = set()
    for step in range(30):
        apply_op(rnd, w, g, teachers, aids)
        check_invariants(w, teachers)
        for s in w.repo.list("session"):
            if s["status"] in (
                "cancelled_by_parent_late",
                "no_show",
                "void",
                "cancelled_by_teacher",
            ):
                cancelled_ids.add(s["$id"])
        if rnd.random() < 0.25:  # I3: N runs == 1 run
            generate_all(g, aids)
            before = state(w.repo)
            generate_all(g, aids)
            generate_all(g, aids)
            assert state(w.repo) == before, (
                f"seed {seed} step {step}: re-running generation changed something"
            )
    # I4: nothing cancelled ever came back to scheduled
    for sid in cancelled_ids:
        assert w.repo.get("session", sid)["status"] in (
            "cancelled_by_parent_late",
            "no_show",
            "void",
            "cancelled_by_teacher",
        )


@pytest.mark.parametrize("seed", range(10))
def test_two_independent_generators_in_turn_agree(seed):
    """Two worker processes (separate generator objects, one repo) alternating on the same
    assignments
    never duplicate."""
    rnd = random.Random(1000 + seed)
    w, teachers, aids = random_world(rnd)
    g1, g2 = gen_for(w), gen_for(w)
    for a in aids:
        g1.generate(a)
        g2.generate(a)
    before = state(w.repo)
    for a in rnd.sample(aids, len(aids)):
        g2.generate(a)
        g1.generate(a)
    assert state(w.repo) == before
    check_invariants(w, teachers)


# ---------------------------------------------------------------- threads
def lease_gen(w):
    return gen_for(w, lock_wait=timedelta(seconds=30))


def staggered_assignments(w, teacher, n):
    """n assignments whose Tuesday slots start 5 minutes apart and last 60: nearly all overlap
    pairwise."""
    return [w.assignment(teacher, [(2, 960 + 5 * i, 1020 + 5 * i)]) for i in range(n)]


def run_threads(fn, items):
    gate = threading.Barrier(len(items))
    errors = []

    def go(x):
        gate.wait()
        try:
            fn(x)
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    ts = [threading.Thread(target=go, args=(x,)) for x in items]
    [t.start() for t in ts]
    [t.join() for t in ts]
    return errors


def test_fifty_threads_generating_overlapping_assignments_for_one_teacher_never_double_book():
    w = World()
    t = w.teacher()
    aids = staggered_assignments(w, t, 50)
    g = lease_gen(w)
    errors = run_threads(g.generate, aids)
    assert not errors
    assert overlapping_pairs(w.repo, t) == []
    check_invariants(w, [t])
    held = live_held(w.repo, t)
    assert held  # somebody won every date
    assert not [
        r for r in w.repo.list("session") if r.get("deleted_at")
    ]  # the lease: no rollbacks needed
    assert len({s["scheduled_start_at"][:10] for s in held}) == 4  # every Tuesday has winners
    # which slots win depends on the order the threads arrive (never overlapping): 1 to 5 per date
    assert 4 <= len(held) <= 20
    # the losers were reported, not dropped: every candidate got a slot or was flagged once
    assert len(w.issues(kind="teacher_conflict")) == 50 * 4 - len(held)


def test_fifty_threads_on_the_same_assignment_produce_exactly_one_set():
    w = World()
    t = w.teacher()
    aid = w.assignment(t, [TUE_4PM, (4, 960, 1020)], trial=True)
    g = lease_gen(w)
    errors = run_threads(lambda _: g.generate(aid), list(range(50)))
    assert not errors
    live = [s for s in w.sessions(assignment_id=aid) if not s.get("deleted_at")]
    assert len(live) == 8 and len({s["generation_key"] for s in live}) == 8
    assert sum(1 for s in live if s["is_trial"]) == 1
    assert not w.issues()


def test_threads_mixing_generate_cancel_and_reschedule_on_one_teacher_keep_the_invariants():
    w = World()
    t = w.teacher()
    aids = staggered_assignments(w, t, 12)
    g = lease_gen(w)
    for a in aids[:4]:
        g.generate(a)
    movable = [s["$id"] for s in w.repo.list("session")][:3]

    def op(i):
        if i % 3 == 0:
            g.generate(aids[i % len(aids)])
        elif i % 3 == 1:
            g.cancel_future(aids[i % len(aids)], date(2026, 11, 30), "paused")
        else:
            with contextlib.suppress(rules.SessionConflict, LeaseBusy):
                st = datetime(2026, 12, 2, 7, 0, tzinfo=UTC) + timedelta(minutes=5 * i)
                g.writer.reschedule(movable[i % 3], st, st + timedelta(hours=1))

    assert not run_threads(op, list(range(30)))
    assert overlapping_pairs(w.repo, t) == []
    check_invariants(w, [t])


def test_mutation_without_lease_and_recheck_the_threaded_generation_double_books(monkeypatch):
    monkeypatch.setattr(
        TeacherLeases, "hold", lambda self, teacher_id: contextlib.nullcontext(None)
    )
    monkeypatch.setattr(SessionWriter, "_recheck_or_rollback", lambda self, *a, **k: None)
    w = World()
    t = w.teacher()
    aids = staggered_assignments(w, t, 12)
    barrier = threading.Barrier(12, timeout=3)

    def before(op, table, row_id, data):
        if op == "create" and table == "session":
            with contextlib.suppress(threading.BrokenBarrierError):
                barrier.wait()

    w.repo.before = before
    g = gen_for(w)
    # one date only so every thread parks on the barrier at its first insert
    run_threads(lambda a: g.generate(a, horizon_days=1), aids)
    assert overlapping_pairs(w.repo, t), (
        "the harness could not reproduce the race: the invariant tests have no teeth"
    )


def test_mutation_lease_only_recheck_removed_is_safe_but_recheck_only_is_safe_too(monkeypatch):
    """Either defence alone keeps the invariant (defence in depth); both removed fails (above)."""
    for variant in ("no_lease", "no_recheck"):
        with monkeypatch.context() as m:
            if variant == "no_lease":
                m.setattr(
                    TeacherLeases, "hold", lambda self, teacher_id: contextlib.nullcontext(None)
                )
            else:
                m.setattr(SessionWriter, "_recheck_or_rollback", lambda self, *a, **k: None)
            w = World()
            t = w.teacher()
            aids = staggered_assignments(w, t, 12)
            g = lease_gen(w)
            run_threads(g.generate, aids)
            assert overlapping_pairs(w.repo, t) == [], variant
