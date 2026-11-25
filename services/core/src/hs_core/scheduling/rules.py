"""Port of appwrite/session_rules.py (M02) for the core service, plus Africa/Kampala helpers.

DECISION (S01): the core service PORTS the reference module instead of importing it. `appwrite/`
is not a core dependency (the Docker image contains only services/core) and a top-level
`appwrite` directory would shadow the Appwrite SDK package of the same name.
`tests/test_sched_rules.py` loads the reference file and proves equivalence (state tables, every
transition pair, overlap, time validation, key builders, patches), so the two cannot drift
silently. The reference stays the readable spec used by schema tests; this module is what
production code calls.

Additions over the reference: `generation_key` / `slot_ref` (S01 identity of a generated session),
soft-delete and restore patches for sessions that handle BOTH derived keys, and the Kampala <-> UTC
helpers (no DST in EAT, but always go through the zone so that a date is always the Kampala date,
never the UTC date).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

KAMPALA = ZoneInfo("Africa/Kampala")

STATES = (
    "scheduled",
    "checked_in",
    "checked_out",
    "awaiting_confirmation",
    "confirmed",
    "disputed",
    "void",
    "incomplete",
    "cancelled_by_parent_late",
    "cancelled_by_parent_ontime",
    "cancelled_by_teacher",
    "no_show",
)

# SR 7.4. incomplete -> confirmed|void is the "admin review" outcome (the diagram does not name it):
# owner question.
ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    "scheduled": frozenset(
        {
            "checked_in",
            "cancelled_by_parent_late",
            "cancelled_by_parent_ontime",
            "cancelled_by_teacher",
            "no_show",
        }
    ),
    "checked_in": frozenset({"checked_out", "incomplete"}),
    "checked_out": frozenset({"awaiting_confirmation"}),
    "awaiting_confirmation": frozenset({"confirmed", "disputed"}),
    "disputed": frozenset({"confirmed", "void"}),
    "incomplete": frozenset({"confirmed", "void"}),
    "confirmed": frozenset(),
    "void": frozenset(),
    "no_show": frozenset(),
    "cancelled_by_parent_late": frozenset(),
    "cancelled_by_parent_ontime": frozenset(),
    "cancelled_by_teacher": frozenset(),
}
# Statuses that free the teacher's time slot (teacher_slot_key is null): the session no longer takes
# place.
SLOT_RELEASED = frozenset(
    {
        "cancelled_by_parent_late",
        "cancelled_by_parent_ontime",
        "cancelled_by_teacher",
        "no_show",
        "void",
    }
)
LATE_CANCELLATION_NOTICE_HOURS = 24  # ASSUMPTION (not in SR): owner question


class InvalidTransition(ValueError):
    pass


class InvalidSession(ValueError):
    """A session with zero or negative length (end <= start)."""


class SessionConflict(ValueError):
    """The teacher already has a session overlapping this one.

    `conflicting_id`: the session in the way (when known). `raced`: this row WAS inserted and then
    rolled back because a
    concurrent overlapping insert won the re-check."""

    def __init__(
        self, message: str, conflicting_id: str | None = None, raced: bool = False
    ) -> None:
        super().__init__(message)
        self.conflicting_id = conflicting_id
        self.raced = raced


def check_transition(old: str, new: str) -> None:
    if old not in ALLOWED_TRANSITIONS or new not in ALLOWED_TRANSITIONS:
        raise InvalidTransition(f"unknown session state in {old!r} -> {new!r}")
    if new not in ALLOWED_TRANSITIONS[old]:
        raise InvalidTransition(f"session transition {old} -> {new} is not allowed (SR 7.4)")


def parse_utc(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return dt.astimezone(UTC) if dt.tzinfo else dt.replace(tzinfo=UTC)


def iso_utc(dt: datetime) -> str:
    """Fixed-width UTC text (`2026-12-01T13:00:00.000+00:00`): such strings sort like instants, so
    Appwrite range queries
    on datetime columns and plain string comparison agree."""
    if dt.tzinfo is None:
        raise ValueError("naive datetime: pass timezone-aware values")
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.000+00:00")


def _stamp(value: str) -> str:
    return parse_utc(value).strftime("%Y%m%dT%H%M%SZ")


def check_session_times(start: str, end: str) -> None:
    if parse_utc(end) <= parse_utc(start):
        raise InvalidSession("scheduled_end_at must be after scheduled_start_at")


def overlaps(a_start: str, a_end: str, b_start: str, b_end: str) -> bool:
    """Half-open intervals: back-to-back sessions do not overlap."""
    return parse_utc(a_start) < parse_utc(b_end) and parse_utc(b_start) < parse_utc(a_end)


def is_late_cancellation(
    scheduled_start: str, cancelled_at: str, notice_hours: int = LATE_CANCELLATION_NOTICE_HOURS
) -> bool:
    return parse_utc(scheduled_start) - parse_utc(cancelled_at) < timedelta(hours=notice_hours)


def parent_cancellation_status(is_late: bool) -> str:
    return "cancelled_by_parent_late" if is_late else "cancelled_by_parent_ontime"


# --- Africa/Kampala wall clock <-> UTC
# ------------------------------------------------------------------------------
def local_date(dt: datetime) -> date:
    """The Africa/Kampala calendar date of an instant (00:30 EAT is the PREVIOUS day in UTC)."""
    return dt.astimezone(KAMPALA).date()


def local_to_utc(d: date, minute: int) -> datetime:
    """Wall clock `minute` minutes after Kampala midnight of `d` as a UTC instant. 1440 = next local
    midnight."""
    midnight = datetime.combine(d, time(0, 0), tzinfo=KAMPALA)
    return (midnight + timedelta(minutes=minute)).astimezone(UTC)


# --- derived unique keys (null = the row no longer holds the value)
# --------------------------------------------------
def generation_key_for(assignment_id: str | None, slot_ref: str | None) -> str | None:
    return f"{assignment_id}:{slot_ref}" if assignment_id and slot_ref else None


def with_session_keys(row: dict) -> dict:
    """`teacher_slot_key`: while the session holds the teacher's slot. `generation_key` (S01): the
    identity of a generated
    session; it stays set for cancelled/void/no_show rows (so they are never regenerated) and is
    null only when the row is
    soft-deleted or was not generated (no slot_ref)."""
    row = dict(row)
    deleted = bool(row.get("deleted_at"))
    held = row["status"] not in SLOT_RELEASED and not deleted
    row["teacher_slot_key"] = (
        f"{row['teacher_id']}|{_stamp(row['scheduled_start_at'])}" if held else None
    )
    row["generation_key"] = (
        None if deleted else generation_key_for(row.get("assignment_id"), row.get("slot_ref"))
    )
    return row


def soft_delete_session_patch(now: str) -> dict:
    """Soft delete: deleted_at plus BOTH derived keys nulled (the slot and the generated identity
    become free)."""
    return {"deleted_at": now, "teacher_slot_key": None, "generation_key": None}


# A restore must never be a bare "clear deleted_at": a newer row may hold the slot or the identity
# now.
CLEARED = None


def restore_session_patch(row: dict) -> dict:
    """Restore a soft-deleted session: deleted_at cleared AND both keys recomputed from the row, so
    the
    unique indexes
    answer 409 when the slot/identity was taken meanwhile (mirror of
    schema_build.soft_restore_patch)."""
    rebuilt = with_session_keys({**row, "deleted_at": CLEARED})
    return {
        "deleted_at": CLEARED,
        "teacher_slot_key": rebuilt["teacher_slot_key"],
        "generation_key": rebuilt["generation_key"],
    }


def transition_patch(session: dict, new_status: str, now: str) -> dict:
    check_transition(session["status"], new_status)
    merged = with_session_keys({**session, "status": new_status})
    return {
        "status": new_status,
        "status_changed_at": now,
        "teacher_slot_key": merged["teacher_slot_key"],
    }
