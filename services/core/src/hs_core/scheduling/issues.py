"""Admin-visible scheduling issues (`scheduling_issue`): things the generator will not decide alone.

Idempotent: `issue_key` (`<kind>:<assignment_id>:<slot_ref or *>`) is unique while the issue is
open, so re-running generation never duplicates an open issue; resolving nulls the key, so a
recurrence opens a new issue.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime

from appwrite.query import Query

from ..repository import Conflict, Repository
from . import rules
from .writer import list_all

TABLE = "scheduling_issue"
# Resolved automatically when the condition is gone (assignment level) or the slot is fine again.
AUTO_KINDS = frozenset(
    {
        "teacher_conflict",
        "outside_availability",
        "no_home_location",
        "no_slots",
        "slot_shortfall",
        "invalid_slot",
        "teacher_inactive",
        "generation_error",
    }
)


def issue_key(kind: str, assignment_id: str, slot_ref: str | None) -> str:
    return f"{kind}:{assignment_id}:{slot_ref or '*'}"


class IssueStore:
    def __init__(self, repo: Repository, clock: Callable[[], datetime] | None = None) -> None:
        self.repo = repo
        self.clock = clock or (lambda: datetime.now(UTC))

    def open_for(self, assignment_id: str) -> dict[str, dict]:
        """{issue_key: row} of the assignment's open issues."""
        rows = list_all(
            self.repo,
            TABLE,
            [Query.equal("assignment_id", assignment_id), Query.equal("status", "open")],
        )
        return {r["issue_key"]: r for r in rows if r.get("issue_key") and not r.get("deleted_at")}

    def flag(
        self,
        kind: str,
        assignment_id: str,
        *,
        slot_ref: str | None = None,
        teacher_id: str | None = None,
        session_id: str | None = None,
        detail: str = "",
        known: dict[str, dict] | None = None,
    ) -> bool:
        """Open the issue unless it is already open. Returns True when a row was created. `known` =
        open_for() result."""
        key = issue_key(kind, assignment_id, slot_ref)
        if known is not None and key in known:
            return False
        row = {
            "assignment_id": assignment_id,
            "teacher_id": teacher_id,
            "session_id": session_id,
            "kind": kind,
            "slot_ref": slot_ref,
            "detail": detail[:500],
            "status": "open",
            "issue_key": key,
        }
        try:
            created = self.repo.create(TABLE, uuid.uuid4().hex[:32], row)
        except Conflict:
            return False  # a concurrent run opened it first
        if known is not None:
            known[key] = created
        return True

    def resolve(self, row: dict, actor_id: str | None = None) -> None:
        self.repo.update(
            TABLE,
            row["$id"],
            {
                "status": "resolved",
                "resolved_at": rules.iso_utc(self.clock()),
                "resolved_by_id": actor_id,
                "issue_key": None,
            },
        )
