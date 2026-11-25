"""S01 (#93): SessionGenerator. Every acceptance criterion and edge case of the issue maps to a test
here (see memory.md).

NOW = Monday 2026-11-30 09:00 Africa/Kampala (06:00 UTC). A "Tuesday 16:00" slot is (2, 960, 1020):
13:00-14:00 UTC.
"""

import json
from datetime import UTC, date, datetime, timedelta

import pytest

from hs_core.audit import AuditedRepository
from hs_core.audit.context import AuditContext
from hs_core.audit.errors import ReasonRequired
from hs_core.jobs.alerts import FakeAlertSink
from hs_core.scheduling import rules
from hs_core.scheduling.generator import SessionGenerator
from hs_core.scheduling.pattern import Slot
from sched_helpers import NOW, World, iso, overlapping_pairs, snapshot

TUE_4PM = (2, 960, 1020)
THU_4PM = (4, 960, 1020)
MON_4PM = (1, 960, 1020)


def gen_for(w: World, **kw) -> SessionGenerator:
    kw.setdefault("alert", FakeAlertSink())
    return SessionGenerator(w.audited, clock=w.clock, **kw)


def starts(w, aid, **where):
    return sorted(
        s["scheduled_start_at"]
        for s in w.sessions(assignment_id=aid, **where)
        if not s.get("deleted_at")
    )


def writes(repo):
    return [c for c in repo.calls if c[0] in ("create", "update", "delete")]


# ---------------------------------------------------------------- acceptance criteria
def test_AC_generation_is_idempotent_rerunning_creates_nothing_and_yields_the_identical_set():
    w = World()
    t = w.teacher()
    aid = w.assignment(t, [TUE_4PM, THU_4PM])
    g = gen_for(w)
    first = g.generate(aid)
    assert (
        first.status == "generated" and first.created == 8
    )  # 4 Tuesdays + 4 Thursdays in 28 days from Mon 30 Nov
    before = snapshot(w.repo)
    mark = len(w.repo.calls)
    again = g.generate(aid)
    assert again.created == 0 and again.existing == 8
    assert snapshot(w.repo) == before
    assert not [
        c for c in w.repo.calls[mark:] if c[0] in ("create", "update", "delete")
    ]  # no writes at all, not even leases


def test_AC_running_generation_many_times_equals_running_once():
    runs = []
    for n in (1, 2, 5):
        w = World()
        t = w.teacher()
        aid = w.assignment(t, [TUE_4PM, THU_4PM], trial=True)
        g = gen_for(w)
        for _ in range(n):
            g.generate(aid)
        runs.append(snapshot(w.repo))
    assert runs[0] == runs[1] == runs[2]


def test_AC_sessions_are_kampala_wall_clock_stored_as_utc():
    w = World()
    aid = w.assignment(w.teacher(), [TUE_4PM])
    gen_for(w).generate(aid)
    first = w.sessions(assignment_id=aid)[0]
    assert first["scheduled_start_at"] == "2026-12-01T13:00:00.000+00:00"  # 16:00 EAT
    assert first["scheduled_end_at"] == "2026-12-01T14:00:00.000+00:00"
    assert (
        first["slot_ref"] == "2-960:2026-12-01"
        and first["generation_key"] == f"{aid}:2-960:2026-12-01"
    )
    assert (
        first["status"] == "scheduled"
        and first["subscription_id"]
        and first["is_substitute"] is False
    )
    for s in w.sessions(assignment_id=aid):
        assert s["scheduled_start_at"].endswith("+00:00")


def test_AC_first_session_of_a_new_pairing_is_flagged_trial_and_only_that_one():
    w = World()
    aid = w.assignment(w.teacher(), [TUE_4PM, THU_4PM], trial=True)
    g = gen_for(w)
    g.generate(aid)
    rows = sorted(w.sessions(assignment_id=aid), key=lambda s: s["scheduled_start_at"])
    assert [r["is_trial"] for r in rows][:3] == [True, False, False]
    assert rows[0]["scheduled_start_at"] == "2026-12-01T13:00:00.000+00:00"
    g.generate(aid)  # a rerun (or the next night's extension) never flags a second one
    assert sum(1 for r in w.sessions(assignment_id=aid) if r["is_trial"]) == 1


def test_AC_a_regular_assignment_has_no_trial_session():
    w = World()
    aid = w.assignment(w.teacher(), [TUE_4PM], trial=False)
    gen_for(w).generate(aid)
    assert not any(r["is_trial"] for r in w.sessions(assignment_id=aid))


def test_trial_flag_moves_on_when_the_trial_session_could_not_be_created():
    w = World()
    t = w.teacher()
    other = w.assignment(t, [TUE_4PM])  # takes the slot first
    trial = w.assignment(t, [TUE_4PM, THU_4PM], trial=True)
    g = gen_for(w)
    g.generate(other)
    g.generate(trial)
    rows = sorted(w.sessions(assignment_id=trial), key=lambda s: s["scheduled_start_at"])
    assert (
        rows[0]["scheduled_start_at"].startswith("2026-12-03") and rows[0]["is_trial"]
    )  # first Thursday, Tuesdays clash
    assert sum(1 for r in rows if r["is_trial"]) == 1


# ---------------------------------------------------------------- edge cases: periods and horizon
def test_EC_assignment_starting_mid_week_generates_only_the_remaining_slots_of_that_week():
    w = World()
    # starts Wednesday 2 Dec 12:00 EAT; slots Mon/Wed 16:00/Fri
    aid = w.assignment(
        w.teacher(),
        [MON_4PM, (3, 960, 1020), (5, 960, 1020)],
        start=datetime(2026, 12, 2, 9, 0, tzinfo=UTC),
    )
    gen_for(w).generate(aid)
    first_days = [s[:10] for s in starts(w, aid)][:3]
    assert first_days == [
        "2026-12-02",
        "2026-12-04",
        "2026-12-07",
    ]  # Mon 30 Nov and nothing before the start


def test_EC_assignment_starting_in_the_future_generates_nothing_before_its_start():
    w = World()
    aid = w.assignment(w.teacher(), [TUE_4PM], start=datetime(2026, 12, 14, 0, 0, tzinfo=UTC))
    gen_for(w).generate(aid)
    assert starts(w, aid)[0].startswith("2026-12-15")


def test_EC_horizon_boundary_mid_week_and_daily_extension_without_churn():
    w = World()
    aid = w.assignment(w.teacher(), [TUE_4PM, THU_4PM])
    g = gen_for(w, horizon_days=10)  # ends Thursday 10 Dec (inclusive)
    g.generate(aid)
    assert starts(w, aid)[-1].startswith("2026-12-10")
    ids = {s["$id"]: s["scheduled_start_at"] for s in w.sessions(assignment_id=aid)}
    w.clock.advance(days=1)  # the nightly job a day later: horizon now ends Fri 11 Dec: nothing new
    mark = len(w.repo.calls)
    r = g.generate(aid)
    assert r.created == 0 and not [
        c for c in w.repo.calls[mark:] if c[0] in ("create", "update", "delete")
    ]
    w.clock.advance(days=4)  # Sat 5 Dec: the horizon (10 days) now ends Tue 15 Dec
    g.generate(aid)
    after = {s["$id"]: s["scheduled_start_at"] for s in w.sessions(assignment_id=aid)}
    assert ids.items() <= after.items()  # nothing existing was touched or replaced
    assert starts(w, aid)[-1].startswith("2026-12-15") and len(starts(w, aid)) == 5


def test_horizon_is_configurable_and_defaults_to_28_days():
    w = World()
    aid = w.assignment(w.teacher(), [TUE_4PM])
    gen_for(w).generate(aid)
    assert (
        len(w.sessions(assignment_id=aid)) == 4
    )  # Tue 1, 8, 15, 22 Dec: 28 days from Mon 30 Nov ends Mon 28 Dec
    assert starts(w, aid)[-1].startswith("2026-12-22")
    w2 = World()
    aid2 = w2.assignment(w2.teacher(), [TUE_4PM])
    gen_for(w2, horizon_days=56).generate(aid2)
    assert len(w2.sessions(assignment_id=aid2)) == 8


def test_EC_assignment_ending_mid_horizon_stops_at_its_end():
    w = World()
    end = datetime(2026, 12, 9, 12, 0, tzinfo=UTC)  # Wed 9 Dec 15:00 EAT
    aid = w.assignment(w.teacher(), [TUE_4PM, THU_4PM], end=end)
    gen_for(w).generate(aid)
    assert starts(w, aid)[-1].startswith("2026-12-08")
    # a session starting exactly at the end instant is not generated
    w2 = World()
    aid2 = w2.assignment(w2.teacher(), [TUE_4PM], end=datetime(2026, 12, 8, 13, 0, tzinfo=UTC))
    gen_for(w2).generate(aid2)
    assert starts(w2, aid2) == ["2026-12-01T13:00:00.000+00:00"]


def test_ended_or_deleted_assignment_and_inactive_subscriptions_generate_nothing():
    w = World()
    t = w.teacher()
    ended = w.assignment(t, [TUE_4PM], end=NOW - timedelta(days=1))
    deleted = w.assignment(t, [THU_4PM])
    w.repo.update("assignment", deleted, {"deleted_at": iso(NOW), "active_key": None})
    g = gen_for(w)
    assert g.generate(ended).reason == "assignment_ended"
    assert g.generate(deleted).reason == "assignment_deleted"
    for status in ("pending_payment", "matching", "paused", "past_due", "cancelled"):
        w2 = World()
        aid = w2.assignment(w2.teacher(), [TUE_4PM], status=status)
        r = gen_for(w2).generate(aid)
        assert r.status == "skipped" and r.reason == f"subscription_{status}" and not w2.sessions()
    assert not w.sessions()


def test_past_sessions_are_never_generated():
    w = World()
    aid = w.assignment(w.teacher(), [MON_4PM], start=NOW - timedelta(days=30))
    gen_for(w).generate(aid)
    assert all(s > iso(NOW) for s in starts(w, aid))


def test_session_starting_after_now_today_is_generated_one_already_started_is_not():
    w = World()  # NOW = Mon 09:00 EAT
    aid = w.assignment(
        w.teacher(), [(1, 600, 660), (1, 480, 540)]
    )  # 10:00 (future) and 08:00 (past) today
    gen_for(w).generate(aid)
    assert starts(w, aid)[0] == "2026-11-30T07:00:00.000+00:00"  # 10:00 EAT today; 08:00 skipped


# ---------------------------------------------------------------- Kampala date vs UTC date
def midnight_case():
    """00:30 EAT Tuesday is 21:30 UTC MONDAY. 'now' is Tuesday 01:30 EAT (Monday 22:30 UTC)."""
    w = World(now=datetime(2026, 11, 30, 22, 30, tzinfo=UTC))
    t = w.teacher(availability=[(d, 0, 1440) for d in range(1, 8)])  # works round the clock
    aid = w.assignment(t, [(2, 30, 90), (2, 120, 180)], start=datetime(2026, 11, 1, tzinfo=UTC))
    g = gen_for(w, horizon_days=28)
    g.generate(aid)
    return w, aid


def midnight_ok() -> bool:
    w, aid = midnight_case()
    got = starts(w, aid)
    # 00:30 EAT Tue 1 Dec is already in the past; 02:00 EAT today is not. Horizon covers Kampala
    # days to Tue 29 Dec.
    return (
        got[0] == "2026-11-30T23:00:00.000+00:00"
        and got[-1] == "2026-12-28T23:00:00.000+00:00"
        and "2026-12-28T21:30:00.000+00:00" in got
        and len(got) == 9
        and all(
            s["slot_ref"].endswith(("-01", "-08", "-15", "-22", "-29"))
            for s in w.sessions(assignment_id=aid)
        )
    )


def test_EC_midnight_boundary_session_is_the_previous_utc_day_but_keyed_by_the_kampala_date():
    assert midnight_ok()
    w, aid = midnight_case()
    row = next(s for s in w.sessions(assignment_id=aid) if s["slot_ref"] == "2-30:2026-12-08")
    assert (
        row["scheduled_start_at"] == "2026-12-07T21:30:00.000+00:00"
    )  # UTC date is the 7th, key date is the 8th
    assert row["generation_key"].endswith("2-30:2026-12-08")
    gen_for(w).generate(aid)  # rerun at the same instant: no duplicates
    assert len(w.sessions(assignment_id=aid)) == 9


def test_mutation_utc_date_instead_of_kampala_date_is_detected(monkeypatch):
    monkeypatch.setattr(rules, "local_date", lambda dt: dt.astimezone(UTC).date())
    assert not midnight_ok()


# ---------------------------------------------------------------- idempotency mutation
def idempotent_ok() -> bool:
    w = World()
    aid = w.assignment(w.teacher(), [TUE_4PM, THU_4PM])
    g = gen_for(w)
    g.generate(aid)
    first = snapshot(w.repo)
    g.generate(aid)
    same = snapshot(w.repo) == first and len(w.sessions()) == len(first)
    # a worker with a STALE read (the concurrent-run case): the cancelled slot must still not be
    # recreated, the unique
    # generation_key is what protects it because a cancelled session releases the teacher slot
    victim = sorted(w.sessions(), key=lambda r: r["scheduled_start_at"])[1]
    w.repo.update(
        "session",
        victim["$id"],
        rules.transition_patch(victim, "cancelled_by_parent_late", iso(NOW)),
    )
    n = len(w.sessions())
    g2 = gen_for(w)
    g2._live_sessions = lambda assignment_id: []
    g2.generate(aid)
    return same and len(w.sessions()) == n


def test_idempotency_check_is_green_on_the_real_implementation():
    assert idempotent_ok()


def test_mutation_non_deterministic_generation_key_is_detected(monkeypatch):
    import itertools

    counter = itertools.count()
    real = rules.with_session_keys

    def broken(row):
        out = real(row)
        if out.get("generation_key"):
            out["generation_key"] += (
                # a key that is not a function of (assignment, slot, local date)
                f"#{next(counter)}"
            )
        return out

    monkeypatch.setattr(rules, "with_session_keys", broken)
    assert not idempotent_ok()


# ---------------------------------------------------------------- never regenerate what people
# changed
def test_cancelled_sessions_are_never_regenerated():
    w = World()
    aid = w.assignment(w.teacher(), [TUE_4PM])
    g = gen_for(w)
    g.generate(aid)
    s = sorted(w.sessions(assignment_id=aid), key=lambda r: r["scheduled_start_at"])[1]
    w.repo.update(
        "session", s["$id"], rules.transition_patch(s, "cancelled_by_parent_late", iso(NOW))
    )
    n = len(w.sessions(assignment_id=aid))
    r = g.generate(aid)
    assert r.created == 0 and len(w.sessions(assignment_id=aid)) == n
    g.regenerate(aid, date(2026, 11, 30))
    assert len(w.sessions(assignment_id=aid)) == n
    row = w.repo.get("session", s["$id"])
    assert (
        row["status"] == "cancelled_by_parent_late" and row["generation_key"] == s["generation_key"]
    )


def test_void_and_no_show_sessions_are_not_regenerated():
    w = World()
    aid = w.assignment(w.teacher(), [TUE_4PM])
    g = gen_for(w)
    g.generate(aid)
    rows = sorted(w.sessions(assignment_id=aid), key=lambda r: r["scheduled_start_at"])
    w.repo.update("session", rows[0]["$id"], {"status": "void", "teacher_slot_key": None})
    w.repo.update("session", rows[1]["$id"], rules.transition_patch(rows[1], "no_show", iso(NOW)))
    n = len(w.sessions(assignment_id=aid))
    g.generate(aid)
    g.regenerate(aid, date(2026, 11, 30))
    assert len(w.sessions(assignment_id=aid)) == n


def test_rescheduled_session_is_not_recreated_at_its_old_time_and_survives_regenerate():
    w = World()
    aid = w.assignment(w.teacher(), [TUE_4PM])
    g = gen_for(w)
    g.generate(aid)
    s = sorted(w.sessions(assignment_id=aid), key=lambda r: r["scheduled_start_at"])[0]
    moved_start = datetime(2026, 12, 2, 7, 0, tzinfo=UTC)  # Wed 10:00 EAT
    g.writer.reschedule(s["$id"], moved_start, moved_start + timedelta(hours=1))
    n = len(w.sessions(assignment_id=aid))
    g.generate(aid)
    g.regenerate(aid, date(2026, 11, 30))
    assert len(w.sessions(assignment_id=aid)) == n
    assert "2026-12-01T13:00:00.000+00:00" not in starts(w, aid)
    row = w.repo.get("session", s["$id"])
    assert (
        row["overridden"] is True
        and row["scheduled_start_at"] == iso(moved_start)
        and not row.get("deleted_at")
    )


def test_reschedule_where_the_old_row_is_cancelled_and_a_new_one_created_is_also_not_recreated():
    w = World()
    aid = w.assignment(w.teacher(), [TUE_4PM])
    g = gen_for(w)
    g.generate(aid)
    s = sorted(w.sessions(assignment_id=aid), key=lambda r: r["scheduled_start_at"])[0]
    w.repo.update(
        "session", s["$id"], rules.transition_patch(s, "cancelled_by_parent_ontime", iso(NOW))
    )
    g.writer.create(
        {
            "child_id": s["child_id"],
            "teacher_id": s["teacher_id"],
            "subscription_id": s["subscription_id"],
            "home_location_id": s["home_location_id"],
            "assignment_id": aid,
            "scheduled_start_at": "2026-12-02T07:00:00.000+00:00",
            "scheduled_end_at": "2026-12-02T08:00:00.000+00:00",
            "status": "scheduled",
            "status_changed_at": iso(NOW),
            "overridden": True,
        }
    )
    n = len(w.sessions(assignment_id=aid))
    g.generate(aid)
    assert len(w.sessions(assignment_id=aid)) == n


def test_substitute_rows_are_left_untouched():
    w = World()
    t = w.teacher()
    aid = w.assignment(t, [TUE_4PM])
    g = gen_for(w)
    sub = w.repo.create(
        "session",
        "sub1",
        rules.with_session_keys(
            {
                "child_id": "c9",
                "teacher_id": "t-sub",
                "subscription_id": "s9",
                "home_location_id": "h9",
                "is_substitute": True,
                "substitute_for_session_id": "orig",
                "scheduled_start_at": "2026-12-08T13:00:00.000+00:00",
                "scheduled_end_at": "2026-12-08T14:00:00.000+00:00",
                "status": "scheduled",
                "status_changed_at": iso(NOW),
            }
        ),
    )
    g.generate(aid)
    g.regenerate(aid, date(2026, 11, 30))
    g.cancel_future(aid, date(2026, 11, 30), "cancelled")
    assert w.repo.get("session", sub["$id"]) == sub


# ---------------------------------------------------------------- subscription status, B03/B04
# entry points
def test_cancel_future_removes_only_future_scheduled_generated_sessions_and_releases_slot_and_key():
    w = World()
    t = w.teacher()
    aid = w.assignment(t, [TUE_4PM])
    g = gen_for(w)
    g.generate(aid)
    rows = sorted(w.sessions(assignment_id=aid), key=lambda r: r["scheduled_start_at"])
    done = rows[0]  # already delivered
    w.repo.update("session", done["$id"], {"status": "confirmed", "teacher_slot_key": None})
    res = g.cancel_future(aid, date(2026, 12, 8), "paused")
    assert res.removed == 3  # 8, 15, 22 Dec; 1 Dec is before from_date
    live = [s for s in w.sessions(assignment_id=aid) if not s.get("deleted_at")]
    assert sorted(s["scheduled_start_at"][:10] for s in live) == ["2026-12-01"]
    for gone in (s for s in w.sessions(assignment_id=aid) if s.get("deleted_at")):
        assert gone["teacher_slot_key"] is None and gone["generation_key"] is None
    # idempotent
    assert g.cancel_future(aid, date(2026, 12, 8), "paused").removed == 0
    actions = [r["action"] for r in w.audit_rows()]
    assert actions.count("scheduling.cancel_future") == 2  # one row per call, reason recorded
    assert actions.count("session.cancel_future") == 3  # one audited change per removed session


def test_cancel_future_keeps_started_sessions():
    w = World()
    aid = w.assignment(w.teacher(), [TUE_4PM])
    g = gen_for(w)
    g.generate(aid)
    first = sorted(w.sessions(assignment_id=aid), key=lambda r: r["scheduled_start_at"])[0]
    w.repo.update("session", first["$id"], rules.transition_patch(first, "checked_in", iso(NOW)))
    g.cancel_future(aid, date(2026, 11, 30), "cancelled")
    assert w.repo.get("session", first["$id"]).get("deleted_at") is None


def test_cancel_future_requires_a_reason():
    w = World()
    aid = w.assignment(w.teacher(), [TUE_4PM])
    with pytest.raises(ReasonRequired):
        gen_for(w).cancel_future(aid, date(2026, 11, 30), "  ")


def test_pause_then_resume_regenerates_the_removed_sessions_but_not_parent_cancellations():
    w = World()
    aid = w.assignment(w.teacher(), [TUE_4PM])
    g = gen_for(w)
    g.generate(aid)
    rows = sorted(w.sessions(assignment_id=aid), key=lambda r: r["scheduled_start_at"])
    w.repo.update(
        "session",
        rows[1]["$id"],
        rules.transition_patch(rows[1], "cancelled_by_parent_ontime", iso(NOW)),
    )
    w.repo.update(
        "subscription", w.repo.get("assignment", aid)["subscription_id"], {"status": "paused"}
    )
    g.cancel_future(aid, date(2026, 11, 30), "paused")
    assert g.generate(aid).reason == "subscription_paused"  # nightly job does nothing while paused
    w.repo.update(
        "subscription", w.repo.get("assignment", aid)["subscription_id"], {"status": "active"}
    )
    r = g.regenerate(aid, date(2026, 11, 30))
    live = [s for s in w.sessions(assignment_id=aid) if not s.get("deleted_at")]
    assert (
        r.created == 3 and len(live) == 4
    )  # 8 Dec stays cancelled by the parent and is not regenerated
    assert sum(1 for s in live if s["status"] == "cancelled_by_parent_ontime") == 1


def test_generation_stops_when_the_subscription_becomes_past_due():
    w = World()
    aid = w.assignment(w.teacher(), [TUE_4PM])
    g = gen_for(w)
    g.generate(aid)
    n = len(w.sessions())
    w.repo.update(
        "subscription", w.repo.get("assignment", aid)["subscription_id"], {"status": "past_due"}
    )
    w.clock.advance(days=7)
    assert g.generate(aid).status == "skipped" and len(w.sessions()) == n


# ---------------------------------------------------------------- plan changes
def test_plan_down_regenerate_prunes_the_surplus_future_sessions_only():
    w = World()
    aid = w.assignment(w.teacher(), [MON_4PM, TUE_4PM, THU_4PM], per_week=3)
    g = gen_for(w)
    g.generate(aid)
    assert len(w.sessions(assignment_id=aid)) == 13  # 5 Mondays + 4 Tuesdays + 4 Thursdays
    sub = w.repo.get("assignment", aid)["subscription_id"]
    w.repo.update("subscription", sub, {"plan_id": w.plan(2)})
    r = g.regenerate(aid, date(2026, 12, 7))  # effective from the next period
    assert r.pruned == 3  # Thursdays 10, 17, 24 Dec; Thu 3 Dec is before from_date
    live = [s for s in w.sessions(assignment_id=aid) if not s.get("deleted_at")]
    assert len(live) == 10 and any(s["slot_ref"] == "4-960:2026-12-03" for s in live)
    assert not any(
        s["slot_ref"].startswith("4-") and s["slot_ref"] > "4-960:2026-12-07" for s in live
    )
    assert g.regenerate(aid, date(2026, 12, 7)).pruned == 0  # idempotent


def test_plan_up_generates_the_extra_weekly_session():
    w = World()
    aid = w.assignment(w.teacher(), [MON_4PM, TUE_4PM, THU_4PM], per_week=2)
    g = gen_for(w)
    g.generate(aid)
    assert len(w.sessions(assignment_id=aid)) == 9  # Mon + Tue
    sub = w.repo.get("assignment", aid)["subscription_id"]
    w.repo.update("subscription", sub, {"plan_id": w.plan(3)})
    r = g.regenerate(aid, date(2026, 12, 7))
    assert r.created == 3  # Thursdays 10, 17, 24 Dec (3 Dec is before from_date)
    assert (
        g.generate(aid).created == 1
    )  # the nightly job (no from_date) also fills the rest of the horizon: Thu 3 Dec


def test_plan_down_does_not_prune_rescheduled_or_cancelled_sessions():
    w = World()
    aid = w.assignment(w.teacher(), [MON_4PM, THU_4PM], per_week=2)
    g = gen_for(w)
    g.generate(aid)
    thu = sorted(
        (s for s in w.sessions(assignment_id=aid) if s["slot_ref"].startswith("4-")),
        key=lambda s: s["scheduled_start_at"],
    )
    g.writer.reschedule(
        thu[1]["$id"],
        datetime(2026, 12, 12, 7, 0, tzinfo=UTC),
        datetime(2026, 12, 12, 8, 0, tzinfo=UTC),
    )
    w.repo.update(
        "session",
        thu[2]["$id"],
        rules.transition_patch(thu[2], "cancelled_by_parent_ontime", iso(NOW)),
    )
    w.repo.update(
        "subscription", w.repo.get("assignment", aid)["subscription_id"], {"plan_id": w.plan(1)}
    )
    r = g.regenerate(aid, date(2026, 11, 30))
    assert r.pruned == 2  # the two untouched Thursdays; the rescheduled and the cancelled one stay
    assert not w.repo.get("session", thu[1]["$id"]).get("deleted_at") and not w.repo.get(
        "session", thu[2]["$id"]
    ).get("deleted_at")


def test_fewer_preferred_slots_than_the_plan_is_flagged_once():
    w = World()
    aid = w.assignment(w.teacher(), [TUE_4PM], per_week=3)
    g = gen_for(w)
    g.generate(aid)
    g.generate(aid)
    assert [i["kind"] for i in w.issues(assignment_id=aid)] == ["slot_shortfall"]
    assert len(w.sessions(assignment_id=aid)) == 4  # what can be generated still is


def test_more_preferred_slots_than_the_plan_uses_the_first_n_by_weekday():
    w = World()
    aid = w.assignment(w.teacher(), [THU_4PM, MON_4PM, TUE_4PM], per_week=2)
    gen_for(w).generate(aid)
    assert {s["slot_ref"][0] for s in w.sessions(assignment_id=aid)} == {"1", "2"}


# ---------------------------------------------------------------- teacher leave and availability
def test_EC_teacher_leave_on_a_session_day_creates_a_cancelled_session_and_a_substitute_request():
    w = World()
    t = w.teacher()
    aid = w.assignment(t, [TUE_4PM])
    w.leave(t, datetime(2026, 12, 8, 0, 0, tzinfo=UTC), datetime(2026, 12, 9, 0, 0, tzinfo=UTC))
    res = gen_for(w).generate(aid)
    assert res.leave_cancelled == 1 and res.created == 4
    row = next(s for s in w.sessions(assignment_id=aid) if s["slot_ref"] == "2-960:2026-12-08")
    assert row["status"] == "cancelled_by_teacher" and row["teacher_slot_key"] is None
    reqs = w.repo.list("match_request")
    assert (
        len(reqs) == 1
        and reqs[0]["type"] == "substitute"
        and reqs[0]["session_id"] == row["$id"]
        and reqs[0]["status"] == "open"
    )
    assert reqs[0]["subscription_id"] == row["subscription_id"]
    c = w.repo.list("cancellation")
    assert (
        len(c) == 1
        and c[0]["session_id"] == row["$id"]
        and c[0]["by_role"] == "teacher"
        and c[0]["is_late"] is False
    )
    assert c[0]["reason"] == "teacher_leave"
    # not silently dropped, not duplicated on rerun, never regenerated
    before = snapshot(w.repo), len(w.repo.list("match_request")), len(w.repo.list("cancellation"))
    gen_for(w).generate(aid)
    assert (
        snapshot(w.repo),
        len(w.repo.list("match_request")),
        len(w.repo.list("cancellation")),
    ) == before


def test_leave_logged_after_generation_cancels_the_session_and_requests_a_substitute_once():
    w = World()
    t = w.teacher()
    aid = w.assignment(t, [TUE_4PM])
    g = gen_for(w)
    g.generate(aid)
    w.leave(t, datetime(2026, 12, 14, 0, 0, tzinfo=UTC), datetime(2026, 12, 16, 0, 0, tzinfo=UTC))
    r = g.generate(aid)
    assert r.leave_cancelled == 1
    row = next(s for s in w.sessions(assignment_id=aid) if s["slot_ref"] == "2-960:2026-12-15")
    assert row["status"] == "cancelled_by_teacher"
    assert g.generate(aid).leave_cancelled == 0
    assert len(w.repo.list("match_request")) == 1


def test_leave_partially_overlapping_the_session_counts():
    w = World()
    t = w.teacher()
    aid = w.assignment(t, [TUE_4PM])
    w.leave(t, datetime(2026, 12, 8, 13, 30, tzinfo=UTC), datetime(2026, 12, 8, 18, 0, tzinfo=UTC))
    assert gen_for(w).generate(aid).leave_cancelled == 1


def test_leave_ending_exactly_when_the_session_starts_does_not_count():
    w = World()
    t = w.teacher()
    aid = w.assignment(t, [TUE_4PM])
    w.leave(t, datetime(2026, 12, 7, 0, 0, tzinfo=UTC), datetime(2026, 12, 8, 13, 0, tzinfo=UTC))
    assert gen_for(w).generate(aid).leave_cancelled == 0


def test_leave_artifacts_are_repaired_after_a_crash_between_the_writes():
    w = World()
    t = w.teacher()
    aid = w.assignment(t, [TUE_4PM])
    w.leave(t, datetime(2026, 12, 8, 0, 0, tzinfo=UTC), datetime(2026, 12, 9, 0, 0, tzinfo=UTC))
    g = gen_for(w)

    class Boom(BaseException):
        pass

    def before(op, table, row_id, data):
        if op == "create" and table == "match_request":
            raise Boom

    w.repo.before = before
    with pytest.raises(Boom):
        g.generate(aid)
    w.repo.before = None
    g.generate(aid)
    assert len(w.repo.list("match_request")) == 1 and len(w.repo.list("cancellation")) == 1
    assert (
        next(s for s in w.sessions(assignment_id=aid) if s["slot_ref"] == "2-960:2026-12-08")[
            "status"
        ]
        == "cancelled_by_teacher"
    )


def test_soft_deleted_leave_is_ignored():
    w = World()
    t = w.teacher()
    aid = w.assignment(t, [TUE_4PM])
    lv = w.leave(
        t, datetime(2026, 12, 8, 0, 0, tzinfo=UTC), datetime(2026, 12, 9, 0, 0, tzinfo=UTC)
    )
    w.repo.update("availability_exception", lv, {"deleted_at": iso(NOW)})
    assert gen_for(w).generate(aid).leave_cancelled == 0


def test_slot_outside_the_teachers_availability_is_not_generated_and_is_flagged():
    w = World()
    t = w.teacher(availability=[(2, 600, 900)])  # Tuesday 10:00-15:00 only
    aid = w.assignment(t, [TUE_4PM, (2, 660, 720)])
    g = gen_for(w)
    r = g.generate(aid)
    assert {s["slot_ref"].split(":")[0] for s in w.sessions(assignment_id=aid)} == {"2-660"}
    flags = w.issues(assignment_id=aid, kind="outside_availability")
    assert len(flags) == 4 and r.flagged >= 4
    g.generate(aid)
    assert len(w.issues(assignment_id=aid, kind="outside_availability")) == 4  # not duplicated


def test_adjacent_availability_slots_are_merged_for_coverage():
    w = World()
    t = w.teacher(availability=[(2, 900, 990), (2, 990, 1100)])
    aid = w.assignment(t, [TUE_4PM])
    gen_for(w).generate(aid)
    assert len(w.sessions(assignment_id=aid)) == 4 and not w.issues()


def test_availability_removed_after_generation_flags_existing_sessions_but_does_not_cancel_them():
    w = World()
    t = w.teacher()
    aid = w.assignment(t, [TUE_4PM])
    g = gen_for(w)
    g.generate(aid)
    for av in w.repo.list("availability_slot"):
        if av["weekday"] == 2:
            w.repo.update("availability_slot", av["$id"], {"deleted_at": iso(NOW)})
    g.generate(aid)
    assert all(s["status"] == "scheduled" for s in w.sessions(assignment_id=aid))
    flagged = w.issues(assignment_id=aid, kind="outside_availability")
    assert len(flagged) == 4 and all(f["session_id"] for f in flagged)
    g.generate(aid)
    assert len(w.issues(assignment_id=aid, kind="outside_availability")) == 4


def test_availability_restored_resolves_the_flags_automatically():
    w = World()
    t = w.teacher(availability=[])
    aid = w.assignment(t, [TUE_4PM])
    g = gen_for(w)
    g.generate(aid)
    assert w.issues(kind="outside_availability", status="open")
    w.repo.create(
        "availability_slot",
        "avx",
        {"teacher_id": t, "weekday": 2, "start_minute": 360, "end_minute": 1320},
    )
    g.generate(aid)
    assert (
        not w.issues(kind="outside_availability", status="open")
        and len(w.sessions(assignment_id=aid)) == 4
    )


# ---------------------------------------------------------------- conflicts: report, never
# overwrite
def test_EC_teacher_already_booked_at_that_time_is_reported_and_the_existing_session_is_untouched():
    w = World()
    t = w.teacher()
    existing = w.repo.create(
        "session",
        "manual1",
        rules.with_session_keys(
            {
                "child_id": "cx",
                "teacher_id": t,
                "subscription_id": "sx",
                "home_location_id": "hx",
                "scheduled_start_at": "2026-12-01T13:30:00.000+00:00",
                "scheduled_end_at": "2026-12-01T14:30:00.000+00:00",
                "status": "scheduled",
                "status_changed_at": iso(NOW),
            }
        ),
    )
    aid = w.assignment(t, [TUE_4PM])
    g = gen_for(w)
    r = g.generate(aid)
    assert r.conflicts == 1 and r.created == 3
    assert w.repo.get("session", "manual1") == existing
    issue = w.issues(assignment_id=aid, kind="teacher_conflict")
    assert (
        len(issue) == 1
        and issue[0]["slot_ref"] == "2-960:2026-12-01"
        and issue[0]["session_id"] == "manual1"
    )
    assert issue[0]["status"] == "open" and issue[0]["teacher_id"] == t
    # the nightly rerun neither duplicates the issue nor writes anything
    mark = len(w.repo.calls)
    g.generate(aid)
    assert len(w.issues(assignment_id=aid, kind="teacher_conflict")) == 1
    assert not [c for c in w.repo.calls[mark:] if c[0] in ("create", "update", "delete")]


def test_conflict_issue_resolves_itself_when_the_clash_goes_away_and_the_session_is_generated():
    w = World()
    t = w.teacher()
    w.repo.create(
        "session",
        "manual1",
        rules.with_session_keys(
            {
                "child_id": "cx",
                "teacher_id": t,
                "subscription_id": "sx",
                "home_location_id": "hx",
                "scheduled_start_at": "2026-12-01T13:00:00.000+00:00",
                "scheduled_end_at": "2026-12-01T14:00:00.000+00:00",
                "status": "scheduled",
                "status_changed_at": iso(NOW),
            }
        ),
    )
    aid = w.assignment(t, [TUE_4PM])
    g = gen_for(w)
    g.generate(aid)
    w.repo.update(
        "session",
        "manual1",
        rules.transition_patch(w.repo.get("session", "manual1"), "cancelled_by_teacher", iso(NOW)),
    )
    r = g.generate(aid)
    assert r.created == 1
    assert w.issues(kind="teacher_conflict")[0]["status"] == "resolved"


def test_EC_two_assignments_for_one_teacher_at_the_same_slot_the_second_is_flagged():
    w = World()
    t = w.teacher()
    a1 = w.assignment(t, [TUE_4PM])
    a2 = w.assignment(t, [TUE_4PM])
    g = gen_for(w)
    r1, r2 = g.generate(a1), g.generate(a2)
    assert r1.created == 4 and r1.conflicts == 0
    assert r2.created == 0 and r2.conflicts == 4
    assert len(w.issues(assignment_id=a2, kind="teacher_conflict")) == 4
    assert overlapping_pairs(w.repo, t) == []


def test_the_assignments_own_overlapping_slots_are_a_conflict_not_a_double_booking():
    w = World()
    t = w.teacher()
    aid = w.assignment(t, [TUE_4PM, (2, 990, 1050)])  # 16:30-17:30 overlaps 16:00-17:00
    gen_for(w).generate(aid)
    assert overlapping_pairs(w.repo, t) == [] and w.issues(kind="teacher_conflict")


# ---------------------------------------------------------------- flags for things the generator
# will not decide
def test_holidays_are_not_skipped_by_default_but_can_be_flagged_for_the_admin():
    w = World()
    aid = w.assignment(w.teacher(), [TUE_4PM])
    gen_for(w).generate(aid)
    assert (
        len(w.sessions(assignment_id=aid)) == 4 and not w.issues()
    )  # default policy: no auto-skip
    w2 = World()
    aid2 = w2.assignment(w2.teacher(), [TUE_4PM])
    gen_for(w2, holidays=lambda d: d == date(2026, 12, 8)).generate(aid2)
    assert len(w2.sessions(assignment_id=aid2)) == 4  # still created: the admin decides
    h = w2.issues(kind="holiday")
    assert len(h) == 1 and h[0]["slot_ref"] == "2-960:2026-12-08"


def test_sunday_slots_are_generated_like_any_other_day():
    w = World()
    aid = w.assignment(w.teacher(), [(7, 600, 660)])
    gen_for(w).generate(aid)
    assert starts(w, aid)[0] == "2026-12-06T07:00:00.000+00:00"


def test_inactive_teacher_generates_nothing_and_flags():
    for status in ("pending", "suspended", "deactivated"):
        w = World()
        aid = w.assignment(w.teacher(status=status), [TUE_4PM])
        r = gen_for(w).generate(aid)
        assert r.status == "skipped" and r.reason == "teacher_inactive" and not w.sessions()
        assert [i["kind"] for i in w.issues()] == ["teacher_inactive"]


def test_no_home_location_no_slots_and_invalid_slots_are_flagged_not_crashed():
    w = World()
    t = w.teacher()
    no_home = w.assignment(t, [TUE_4PM], home=False)
    no_slots = w.assignment(t, [])
    bad = w.assignment(t, [(9, 0, 60), (2, 600, 600)])
    g = gen_for(w)
    assert g.generate(no_home).reason == "no_home_location"
    assert g.generate(no_slots).reason == "no_slots"
    assert g.generate(bad).reason == "no_slots"
    kinds = sorted(i["kind"] for i in w.issues())
    assert kinds == ["invalid_slot", "no_home_location", "no_slots", "no_slots"]
    assert not w.sessions()


def test_default_home_location_is_used_and_a_single_non_default_one_is_accepted():
    w = World()
    t = w.teacher()
    aid = w.assignment(t, [TUE_4PM], parent="p1", home=False)
    w.repo.create("home_location", "hA", {"parent_id": "p1", "is_default": False})
    w.repo.create(
        "home_location", "hB", {"parent_id": "p1", "is_default": True, "default_key": "p1"}
    )
    gen_for(w).generate(aid)
    assert {s["home_location_id"] for s in w.sessions(assignment_id=aid)} == {"hB"}
    w2 = World()
    aid2 = w2.assignment(w2.teacher(), [TUE_4PM], parent="p2", home=False)
    w2.repo.create("home_location", "only", {"parent_id": "p2", "is_default": False})
    gen_for(w2).generate(aid2)
    assert {s["home_location_id"] for s in w2.sessions(assignment_id=aid2)} == {"only"}


def test_issues_auto_resolve_when_the_condition_clears():
    w = World()
    t = w.teacher()
    aid = w.assignment(t, [TUE_4PM], home=False)
    g = gen_for(w)
    g.generate(aid)
    assert w.issues(kind="no_home_location", status="open")
    parent = w.repo.get("subscription", w.repo.get("assignment", aid)["subscription_id"])[
        "parent_id"
    ]
    w.home(parent)
    g.generate(aid)
    assert not w.issues(status="open") and len(w.sessions(assignment_id=aid)) == 4


# ---------------------------------------------------------------- manual generate-now, audit,
# errors
def admin_ctx(actor="admin1", request_id="req-1"):
    return AuditContext.for_principal(actor, ["opsadmin"], request_id=request_id)


def unbound(w, **kw):
    """The production wiring: the audited repository carries no context, the caller's scope does."""
    return SessionGenerator(AuditedRepository(w.repo, w.audit), clock=w.clock, **kw)


def test_generate_now_requires_a_reason_and_records_an_audit_entry():
    w = World()
    aid = w.assignment(w.teacher(), [TUE_4PM])
    g = unbound(w)
    for bad in ("", "  ", "x"):
        with pytest.raises(ReasonRequired):
            g.generate_now(aid, ctx=admin_ctx(), reason=bad)
    assert not w.sessions() and not w.audit_rows()
    res = g.generate_now(aid, ctx=admin_ctx(), reason="parent asked for earlier start")
    assert res.created == 4
    head = w.audit_rows(action="scheduling.generate_now")
    assert len(head) == 1
    assert (head[0]["actor_id"], head[0]["entity"], head[0]["entity_id"]) == (
        "admin1",
        "assignment",
        aid,
    )
    assert head[0]["reason"] == "parent asked for earlier start"
    rows = w.audit_rows(action="session.generate")
    assert len(rows) == 4  # one audited create per session, same actor and reason
    assert {r["actor_id"] for r in rows} == {"admin1"}
    assert {r["reason"] for r in rows} == {"parent asked for earlier start"}


def test_generate_now_can_reconcile_from_a_date():
    w = World()
    aid = w.assignment(w.teacher(), [MON_4PM, THU_4PM], per_week=2)
    g = unbound(w)
    g.generate_now(aid, ctx=admin_ctx(request_id="r1"), reason="initial")
    w.repo.update(
        "subscription", w.repo.get("assignment", aid)["subscription_id"], {"plan_id": w.plan(1)}
    )
    res = g.generate_now(
        aid, ctx=admin_ctx(request_id="r2"), reason="plan changed", reconcile_from=date(2026, 12, 7)
    )
    assert res.pruned == 3
    assert len(w.audit_rows(action="session.prune")) == 3


def test_unknown_assignment_raises_a_clear_error():
    from hs_core.scheduling.generator import AssignmentNotFound

    with pytest.raises(AssignmentNotFound):
        gen_for(World()).generate("nope")


def test_logs_are_structured_with_ids_only_and_report_the_lag(caplog):
    import logging

    w = World()
    aid = w.assignment(w.teacher(), [TUE_4PM])
    with caplog.at_level(logging.INFO, logger="hs_core.scheduling"):
        gen_for(w).generate(aid)
    rec = next(r for r in caplog.records if r.getMessage() == "session generation")
    assert rec.assignment_id == aid and rec.rows_created == 4 and rec.status == "generated"
    assert (
        rec.lag_days == 27 and rec.horizon_days == 28 and rec.duration_ms >= 0
    )  # first run: everything is new
    stock = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__)
    extras = [k for k in rec.__dict__ if k not in stock and k != "message"]
    assert not [
        k
        for k in extras
        if any(f in k for f in ("name", "phone", "email", "address", "dob", "location"))
    ]
    assert all(isinstance(rec.__dict__[k], (str, int, float, bool, type(None))) for k in extras)
    w.clock.advance(days=1)
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="hs_core.scheduling"):
        gen_for(w).generate(aid)
    again = next(r for r in caplog.records if r.getMessage() == "session generation")
    assert (
        again.lag_days == 0 and again.rows_created == 1
    )  # the nightly run only extended the edge of the horizon


def test_preferred_slots_json_is_read_from_the_subscription_text_column():
    w = World()
    aid = w.assignment(w.teacher(), [TUE_4PM])
    sub = w.repo.get("assignment", aid)["subscription_id"]
    assert isinstance(w.repo.get("subscription", sub)["preferred_slots"], str)
    assert json.loads(w.repo.get("subscription", sub)["preferred_slots"]) == [
        {"weekday": 2, "start_minute": 960, "end_minute": 1020}
    ]
    assert Slot(2, 960, 1020)


# ------------------------------------------------------------- M05: permissions for new sessions
def test_new_sessions_get_permissions_and_a_failure_does_not_fail_generation():
    w = World()
    aid = w.assignment(w.teacher(), [TUE_4PM])

    class Perms:
        def __init__(self, fail=False):
            self.events, self.fail = [], fail

        def apply(self, event):
            self.events.append(event)
            if self.fail:
                raise RuntimeError("appwrite down")

    perms = Perms()
    g = gen_for(w, permissions=perms)
    assert g.generate(aid).created == 4
    assert len(perms.events) == 4 and all(e.kind == "row_created" for e in perms.events)
    assert gen_for(w, permissions=perms).generate(aid).created == 0 and len(perms.events) == 4
    w2 = World()
    aid2 = w2.assignment(w2.teacher(), [TUE_4PM])
    assert gen_for(w2, permissions=Perms(fail=True)).generate(aid2).created == 4
