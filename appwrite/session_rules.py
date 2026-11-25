"""M02 (#37): reference rules for the sessions domain (stdlib only, no I/O of its own).

Appwrite has no exclusion constraints, conditional updates or triggers (ADR 0002/0012), so the schema carries only
backstops (unique indexes over derived `*_key` columns). This module is the executable specification the core service
(W01 #97) ports: the state machine of SR 7.4 as data, the derived-key builders, and the check-then-write path for sessions
(service overlap check, then insert, with the unique `teacher_slot_key` index as the backstop for concurrent requests).
It takes any client with `list_rows`, `create_row` and `update_row` (the fake in tests, the Appwrite adapter in production).

Restore of a soft-deleted row: use schema_build.soft_restore_patch(row, with_*_keys) (recomputes the keys, 409 if taken).

Known residual risk: the backstop catches two requests for the SAME start; two concurrent requests with different but
overlapping starts can both pass the check. The core service must serialise per teacher (e.g. one writer per teacher key
in the job runner/queue) or re-check after insert and void the later row; tracked as a W01 follow-up.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

STATES = ("scheduled", "checked_in", "checked_out", "awaiting_confirmation", "confirmed", "disputed", "void", "incomplete",
          "cancelled_by_parent_late", "cancelled_by_parent_ontime", "cancelled_by_teacher", "no_show")

# SR 7.4. incomplete -> confirmed|void is the "admin review" outcome (the diagram does not name it): owner question.
ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    "scheduled": frozenset({"checked_in", "cancelled_by_parent_late", "cancelled_by_parent_ontime", "cancelled_by_teacher", "no_show"}),
    "checked_in": frozenset({"checked_out", "incomplete"}),
    "checked_out": frozenset({"awaiting_confirmation"}),
    "awaiting_confirmation": frozenset({"confirmed", "disputed"}),
    "disputed": frozenset({"confirmed", "void"}),
    "incomplete": frozenset({"confirmed", "void"}),
    "confirmed": frozenset(), "void": frozenset(), "no_show": frozenset(),
    "cancelled_by_parent_late": frozenset(), "cancelled_by_parent_ontime": frozenset(), "cancelled_by_teacher": frozenset(),
}
# Statuses that free the teacher's time slot (teacher_slot_key is null): the session no longer takes place.
SLOT_RELEASED = frozenset({"cancelled_by_parent_late", "cancelled_by_parent_ontime", "cancelled_by_teacher", "no_show", "void"})
LATE_CANCELLATION_NOTICE_HOURS = 24  # ASSUMPTION (not in SR): owner question


class InvalidTransition(ValueError):
    pass


class InvalidSession(ValueError):
    """A session with zero or negative length (end <= start)."""


class SessionConflict(ValueError):
    """The teacher already has a session overlapping this one."""


def check_transition(old: str, new: str) -> None:
    if old not in ALLOWED_TRANSITIONS or new not in ALLOWED_TRANSITIONS:
        raise InvalidTransition(f"unknown session state in {old!r} -> {new!r}")
    if new not in ALLOWED_TRANSITIONS[old]:
        raise InvalidTransition(f"session transition {old} -> {new} is not allowed (SR 7.4)")


def parse_utc(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _stamp(value: str) -> str:
    return parse_utc(value).strftime("%Y%m%dT%H%M%SZ")


def check_session_times(start: str, end: str) -> None:
    if parse_utc(end) <= parse_utc(start):
        raise InvalidSession("scheduled_end_at must be after scheduled_start_at")


def overlaps(a_start: str, a_end: str, b_start: str, b_end: str) -> bool:
    """Half-open intervals: back-to-back sessions do not overlap."""
    return parse_utc(a_start) < parse_utc(b_end) and parse_utc(b_start) < parse_utc(a_end)


def is_late_cancellation(scheduled_start: str, cancelled_at: str, notice_hours: int = LATE_CANCELLATION_NOTICE_HOURS) -> bool:
    return parse_utc(scheduled_start) - parse_utc(cancelled_at) < timedelta(hours=notice_hours)


def parent_cancellation_status(is_late: bool) -> str:
    return "cancelled_by_parent_late" if is_late else "cancelled_by_parent_ontime"


# --- derived unique keys (null = the row no longer holds the slot) ------------------------------------------------------
def with_session_keys(row: dict) -> dict:
    row = dict(row)
    held = row["status"] not in SLOT_RELEASED and not row.get("deleted_at")
    row["teacher_slot_key"] = f"{row['teacher_id']}|{_stamp(row['scheduled_start_at'])}" if held else None
    return row


def with_offer_keys(row: dict) -> dict:
    row = dict(row)
    pending = row.get("response", "pending") == "pending" and not row.get("deleted_at")
    row["active_key"] = f"{row['match_request_id']}:{row['teacher_id']}" if pending else None
    return row


def resolve_offer_patch(response: str, now: str) -> dict:
    return {"response": response, "responded_at": now, "active_key": None}


def with_assignment_keys(row: dict) -> dict:
    row = dict(row)
    active = not row.get("end_at") and not row.get("deleted_at")
    row["active_key"] = f"{row['child_id']}:{row['subscription_id']}" if active else None
    return row


def end_assignment_patch(end_reason: str, now: str) -> dict:
    return {"end_reason": end_reason, "end_at": now, "active_key": None}


def with_dispute_keys(row: dict) -> dict:
    row = dict(row)
    row["open_key"] = row["session_id"] if row.get("status", "open") != "resolved" and not row.get("deleted_at") else None
    return row


def resolve_dispute_patch(resolution: str, resolved_by_id: str, now: str) -> dict:
    return {"status": "resolved", "resolution": resolution, "resolved_by_id": resolved_by_id, "resolved_at": now, "open_key": None}


def with_plan_keys(row: dict) -> dict:
    row = dict(row)
    on = row.get("active", True) and not row.get("deleted_at")
    row["active_key"] = f"{row['curriculum_id']}:{row['sessions_per_week']}" if on else None
    return row


# --- sessions ----------------------------------------------------------------------------------------------------------
def transition_patch(session: dict, new_status: str, now: str) -> dict:
    check_transition(session["status"], new_status)
    merged = with_session_keys({**session, "status": new_status})
    return {"status": new_status, "status_changed_at": now, "teacher_slot_key": merged["teacher_slot_key"]}


def create_session(client, row: dict, pause=None) -> dict:
    """Service check then insert. `pause` (tests) runs between the two to model a concurrent request."""
    check_session_times(row["scheduled_start_at"], row["scheduled_end_at"])
    row = with_session_keys(row)
    if row["status"] in SLOT_RELEASED:
        raise InvalidTransition("a session is created as scheduled")
    for other in client.list_rows("session", teacher_id=row["teacher_id"]):
        if other.get("teacher_slot_key") and overlaps(row["scheduled_start_at"], row["scheduled_end_at"],
                                                      other["scheduled_start_at"], other["scheduled_end_at"]):
            raise SessionConflict(f"teacher {row['teacher_id']} already has a session in this period")
    if pause:
        pause()
    return client.create_row("session", row)  # unique teacher_slot_key is the backstop (UniqueViolation)


def substitute_session_row(original: dict, substitute_teacher_id: str, now: str | None = None) -> dict:
    """New session for the same slot taught by a substitute: no assignment (the pairing stays with the original teacher)."""
    if original["status"] != "cancelled_by_teacher":
        raise InvalidTransition("a substitute is only found for a session cancelled by the teacher")
    check_session_times(original["scheduled_start_at"], original["scheduled_end_at"])
    row = {k: original[k] for k in ("child_id", "subscription_id", "home_location_id", "scheduled_start_at", "scheduled_end_at")}
    row.update(teacher_id=substitute_teacher_id, status="scheduled", is_substitute=True,
               substitute_for_session_id=original["$id"], status_changed_at=now or original["status_changed_at"])
    return with_session_keys(row)
