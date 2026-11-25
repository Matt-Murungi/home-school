"""The anonymised history row (issue edge case: ended assignment, first name and class only)."""

import pytest

from hs_core.permissions.history import build_history_row, first_name
from hs_core.permissions.service import row_created
from jobs_helpers import T0
from perm_world import H, World, iso


def test_first_name_only_never_the_surname():
    assert first_name("Amina Nakato") == "Amina"
    assert first_name("  Amina   Grace Nakato ") == "Amina"
    assert first_name("Cher") == "Cher"
    assert first_name("") == "Child" and first_name(None) == "Child"


def test_history_row_has_only_name_class_date_and_duration():
    child = {
        "$id": "a1",
        "name": "Amina Nakato",
        "date_of_birth": "2018-03-01",
        "school": "St Mary",
        "class_level": "P3",
        "parent_id": "PA",
        "areas_of_concern": "x",
    }
    session = {
        "$id": "sA1",
        "scheduled_start_at": "2027-01-18T20:30:00+00:00",
        "scheduled_end_at": "2027-01-18T21:45:00+00:00",
    }
    log = {"$id": "slA1", "teacher_id": "T1", "note": "private note", "homework": "secret"}
    rid, data = build_history_row(log, session, child)
    assert data == {
        "teacher_id": "T1",
        "session_id": "sA1",
        "session_log_id": "slA1",
        "child_first_name": "Amina",
        "class_level": "P3",
        # 20:30 UTC is 23:30 in Kampala: same calendar day; the DATE is the Kampala one
        "session_date": "2027-01-18",
        "duration_minutes": 75,
    }
    flat = str(data) + rid
    for secret in ("Nakato", "2018-03-01", "St Mary", "PA", "private note", "secret", 'a1"'):
        assert secret not in flat
    assert build_history_row(log, session, child)[0] == rid  # deterministic: one row per session


def test_session_date_is_the_kampala_date_across_utc_midnight():
    _, data = build_history_row(
        {"$id": "l", "teacher_id": "T"},
        {
            "$id": "s",
            "scheduled_start_at": "2027-01-18T22:30:00+00:00",
            "scheduled_end_at": "2027-01-18T23:30:00+00:00",
        },
        {"name": "Zed Y", "class_level": None},
    )
    assert data["session_date"] == "2027-01-19" and data["class_level"] is None


def test_invalid_session_is_rejected():
    with pytest.raises(ValueError):
        build_history_row(
            {"$id": "l", "teacher_id": "T"},
            {
                "$id": "s",
                "scheduled_start_at": "2027-01-18T10:00:00+00:00",
                "scheduled_end_at": "2027-01-18T10:00:00+00:00",
            },
            {"name": "A"},
        )


def test_teacher_keeps_the_history_after_the_assignment_ends_and_loses_the_log():
    w = World().seed()
    w.patch("assignment", "asg1", end_at=iso(T0 - 40 * H), end_reason="admin")
    w.patch("session", "sA1", status="confirmed")
    log, session, child = (
        w.repo.get("session_log", "slA1"),
        w.repo.get("session", "sA1"),
        w.repo.get("child", "a1"),
    )
    rid, data = build_history_row(log, session, child)
    w.add("teacher_session_history", "tshNew", **{k: v for k, v in data.items()})
    w.svc.apply(row_created("e", "teacher_session_history", "tshNew"))
    from hs_core.permissions.service import assignment_changed

    w.svc.apply(assignment_changed("e2", "a1", "T1", ended=True))
    t1 = w.as_("uT1")
    assert t1.can_read("teacher_session_history", "tshNew")
    assert not t1.can_read("session_log", "slA1") and not t1.can_read("child", "a1")
    row = t1.get("teacher_session_history", "tshNew")
    assert row["child_first_name"] == "Amina" and "name" not in row and "child_id" not in row
