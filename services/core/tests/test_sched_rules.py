"""S01 (#93): hs_core.scheduling.rules is a PORT of appwrite/session_rules.py (appwrite/ is not a
core
dependency and
its top-level package name clashes with the Appwrite SDK). These tests prove the two stay
equivalent, and cover the
calendar helpers (Africa/Kampala wall clock <-> UTC, no DST) and the slot pattern."""

import importlib.util
import random
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from hs_core.scheduling import pattern, rules
from hs_core.scheduling.pattern import Slot

REF_PATH = Path(__file__).resolve().parents[3] / "appwrite" / "session_rules.py"


def load_reference():
    spec = importlib.util.spec_from_file_location("ref_session_rules", REF_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ref = load_reference()


# ---------------------------------------------------------------- equivalence with the reference
# module
def test_state_tables_are_identical_to_the_reference():
    assert rules.STATES == ref.STATES
    assert rules.ALLOWED_TRANSITIONS == ref.ALLOWED_TRANSITIONS
    assert rules.SLOT_RELEASED == ref.SLOT_RELEASED
    assert rules.LATE_CANCELLATION_NOTICE_HOURS == ref.LATE_CANCELLATION_NOTICE_HOURS


def test_every_transition_pair_agrees_with_the_reference():
    for a in rules.STATES + ("bogus",):
        for b in rules.STATES + ("bogus",):
            try:
                ref.check_transition(a, b)
                expected = True
            except ref.InvalidTransition:
                expected = False
            try:
                rules.check_transition(a, b)
                got = True
            except rules.InvalidTransition:
                got = False
            assert got == expected, (a, b)


def test_overlap_and_time_validation_agree_with_the_reference_on_random_intervals():
    rnd = random.Random(7)
    base = datetime(2026, 12, 1, tzinfo=UTC)

    def stamp():
        return (base + timedelta(minutes=rnd.randrange(0, 600, 15))).isoformat()

    for _ in range(400):
        a, b, c, d = stamp(), stamp(), stamp(), stamp()
        assert rules.overlaps(a, b, c, d) == ref.overlaps(a, b, c, d)
        try:
            ref.check_session_times(a, b)
            ok = True
        except ref.InvalidSession:
            ok = False
        try:
            rules.check_session_times(a, b)
            ok2 = True
        except rules.InvalidSession:
            ok2 = False
        assert ok == ok2


def test_session_keys_and_patches_agree_with_the_reference():
    rnd = random.Random(11)
    for _ in range(200):
        row = {
            "teacher_id": f"t{rnd.randrange(3)}",
            "status": rnd.choice(rules.STATES),
            "scheduled_start_at": (
                f"2026-12-0{rnd.randrange(1, 9)}T0{rnd.randrange(9)}:00:00.000+00:00"
            ),
            "scheduled_end_at": "2026-12-09T23:00:00.000+00:00",
            "deleted_at": rnd.choice([None, "2026-12-01T00:00:00.000+00:00"]),
        }
        assert (
            rules.with_session_keys(row)["teacher_slot_key"]
            == ref.with_session_keys(row)["teacher_slot_key"]
        )
        if row["status"] != "scheduled":
            continue
        for new in rules.STATES:
            if new in rules.ALLOWED_TRANSITIONS["scheduled"]:
                assert rules.transition_patch(row, new, "n") == ref.transition_patch(row, new, "n")


def test_late_cancellation_and_parent_status_agree_with_the_reference():
    s = "2026-12-02T10:00:00+00:00"
    for hours in (0, 1, 23, 24, 25, 100):
        c = (datetime.fromisoformat(s) - timedelta(hours=hours)).isoformat()
        assert rules.is_late_cancellation(s, c) == ref.is_late_cancellation(s, c)
    assert rules.parent_cancellation_status(True) == ref.parent_cancellation_status(True)


def test_equal_or_negative_length_is_rejected():
    for end in ("2026-12-01T06:00:00+00:00", "2026-12-01T05:00:00+00:00"):
        with pytest.raises(rules.InvalidSession):
            rules.check_session_times("2026-12-01T06:00:00+00:00", end)


# ---------------------------------------------------------------- keys, soft delete and restore
def row(**kw):
    r = {
        "teacher_id": "t1",
        "status": "scheduled",
        "assignment_id": "a1",
        "slot_ref": "2-960:2026-12-01",
        "scheduled_start_at": "2026-12-01T13:00:00.000+00:00",
        "scheduled_end_at": "2026-12-01T14:00:00.000+00:00",
    }
    r.update(kw)
    return r


def test_generation_key_is_assignment_plus_slot_ref_and_survives_cancellation():
    assert rules.with_session_keys(row())["generation_key"] == "a1:2-960:2026-12-01"
    cancelled = rules.with_session_keys(row(status="cancelled_by_parent_late"))
    assert (
        cancelled["teacher_slot_key"] is None
        and cancelled["generation_key"] == "a1:2-960:2026-12-01"
    )
    assert rules.with_session_keys(row(status="void"))["generation_key"] == "a1:2-960:2026-12-01"


def test_rows_without_a_slot_ref_have_no_generation_key():
    assert rules.with_session_keys(row(slot_ref=None))["generation_key"] is None
    assert (
        rules.with_session_keys(row(assignment_id=None))["generation_key"] is None
    )  # substitute rows


def test_soft_delete_nulls_both_keys():
    p = rules.soft_delete_session_patch("2026-12-01T00:00:00.000+00:00")
    assert p == {
        "deleted_at": "2026-12-01T00:00:00.000+00:00",
        "teacher_slot_key": None,
        "generation_key": None,
    }


def test_restore_recomputes_both_keys_never_a_bare_clear():
    deleted = {
        **rules.with_session_keys(row()),
        **rules.soft_delete_session_patch("2026-12-01T00:00:00.000+00:00"),
    }
    assert deleted["teacher_slot_key"] is None and deleted["generation_key"] is None
    patch = rules.restore_session_patch(deleted)
    assert patch["teacher_slot_key"] == "t1|20261201T130000Z"
    assert patch["generation_key"] == "a1:2-960:2026-12-01"
    assert set(patch) == {"deleted_at", "teacher_slot_key", "generation_key"}


def test_restoring_a_cancelled_session_keeps_the_slot_released():
    deleted = row(status="cancelled_by_teacher", deleted_at="2026-12-01T00:00:00.000+00:00")
    patch = rules.restore_session_patch(deleted)
    assert patch["teacher_slot_key"] is None and patch["generation_key"] == "a1:2-960:2026-12-01"


def test_restore_patch_matches_the_reference_helper():
    import sys

    sys.path.insert(0, str(REF_PATH.parent))
    try:
        import schema_build as sb
    finally:
        sys.path.pop(0)
    deleted = row(deleted_at="2026-12-01T00:00:00.000+00:00")
    expected = sb.soft_restore_patch(deleted, ref.with_session_keys)
    got = rules.restore_session_patch(deleted)
    assert got["teacher_slot_key"] == expected["teacher_slot_key"]


# ---------------------------------------------------------------- Africa/Kampala wall clock <-> UTC
def test_kampala_wall_clock_is_utc_plus_three_all_year():
    for d in (
        date(2026, 1, 1),
        date(2026, 3, 29),
        date(2026, 7, 1),
        date(2026, 10, 25),
        date(2026, 12, 31),
    ):
        assert rules.local_to_utc(d, 600) == datetime(d.year, d.month, d.day, 7, 0, tzinfo=UTC)


def test_00_30_kampala_is_the_previous_utc_day():
    utc = rules.local_to_utc(date(2026, 12, 1), 30)
    assert utc == datetime(2026, 11, 30, 21, 30, tzinfo=UTC)
    assert rules.local_date(utc) == date(2026, 12, 1)  # the Kampala date, not the UTC date
    assert utc.date() == date(2026, 11, 30)


def test_end_minute_1440_is_the_next_local_midnight():
    assert rules.local_to_utc(date(2026, 12, 1), 1440) == datetime(2026, 12, 1, 21, 0, tzinfo=UTC)


def test_iso_utc_format_is_fixed_width_so_strings_sort_like_instants():
    a = rules.iso_utc(datetime(2026, 12, 1, 6, 0, tzinfo=UTC))
    b = rules.iso_utc(datetime(2026, 12, 1, 9, 0, tzinfo=rules.KAMPALA))  # 06:00 UTC
    assert a == b == "2026-12-01T06:00:00.000+00:00"


# ---------------------------------------------------------------- slot pattern
def test_parse_slots_accepts_json_text_and_reports_invalid_entries():
    raw = (
        '[{"weekday":2,"start_minute":960,"end_minute":1020},{"weekday":9,"start_minute":0,"end_minute":60},'
        '{"weekday":3,"start_minute":600,"end_minute":600},{"weekday":4,"start_minute":-5,"end_minute":60},'
        '{"weekday":2,"start_minute":960,"end_minute":1020},"junk",{"weekday":"x"}]'
    )
    slots, invalid = pattern.parse_slots(raw)
    assert slots == [Slot(2, 960, 1020)]  # duplicate dropped
    assert len(invalid) == 5


@pytest.mark.parametrize("raw", [None, "", "not json", "{}", "[]", 5])
def test_parse_slots_tolerates_missing_or_malformed_input(raw):
    slots, invalid = pattern.parse_slots(raw)
    assert slots == []


def test_select_slots_takes_the_first_n_by_weekday_and_reports_shortfall():
    slots = [Slot(5, 600, 660), Slot(2, 960, 1020), Slot(2, 600, 660)]
    chosen, short = pattern.select_slots(slots, 2)
    assert chosen == [Slot(2, 600, 660), Slot(2, 960, 1020)] and short == 0
    chosen, short = pattern.select_slots(slots[:1], 3)
    assert chosen == [Slot(5, 600, 660)] and short == 2


def test_occurrences_by_local_date_with_slot_ref():
    occ = pattern.occurrences([Slot(2, 960, 1020)], date(2026, 12, 1), date(2026, 12, 15))
    assert [o.date for o in occ] == [date(2026, 12, 1), date(2026, 12, 8), date(2026, 12, 15)]
    assert occ[0].ref == "2-960:2026-12-01"
    assert occ[0].start == datetime(2026, 12, 1, 13, 0, tzinfo=UTC) and occ[0].end == datetime(
        2026, 12, 1, 14, 0, tzinfo=UTC
    )


def test_sunday_is_weekday_7():
    occ = pattern.occurrences([Slot(7, 600, 660)], date(2026, 12, 6), date(2026, 12, 6))  # a Sunday
    assert len(occ) == 1


def test_midnight_boundary_slot_uses_the_local_date_in_the_ref():
    occ = pattern.occurrences([Slot(2, 30, 90)], date(2026, 12, 1), date(2026, 12, 1))[0]
    assert occ.ref == "2-30:2026-12-01"
    assert occ.start == datetime(2026, 11, 30, 21, 30, tzinfo=UTC)
