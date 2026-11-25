"""The anonymised history row a teacher keeps after an assignment ends (ADR 0012, SR 6.2).

When a session log is submitted the core service also writes one `teacher_session_history`
row: child FIRST name only, class level, the session's Kampala calendar date and its length in
minutes. It carries no surname, date of birth, school, address, note, homework or ids of the
child, and is readable only by the teacher (and ops admin). The full `session_log` row loses the
teacher's read when the assignment ends; this row never does.
"""

from __future__ import annotations

from datetime import datetime

from ..jobs.idempotency import row_id
from ..jobs.model import KAMPALA
from .context import parse_dt

FALLBACK_NAME = "Child"


def first_name(full_name: str | None) -> str:
    parts = (full_name or "").split()
    return (parts[0][:60] if parts else FALLBACK_NAME) or FALLBACK_NAME


def build_history_row(log: dict, session: dict, child: dict) -> tuple[str, dict]:
    """Returns (row id, data). Deterministic id per session: a resubmitted log updates one row."""
    start, end = parse_dt(session["scheduled_start_at"]), parse_dt(session["scheduled_end_at"])
    if start is None or end is None or end <= start:
        raise ValueError("session needs a valid start and end")
    minutes = int((end - start).total_seconds() // 60)
    local: datetime = start.astimezone(KAMPALA)
    data = {
        "teacher_id": log["teacher_id"],
        "session_id": session["$id"],
        "session_log_id": log["$id"],
        "child_first_name": first_name(child.get("name")),
        "class_level": (child.get("class_level") or None),
        "session_date": local.date().isoformat(),
        "duration_minutes": minutes,
    }
    return row_id("teacher_session_history", session["$id"]), data
