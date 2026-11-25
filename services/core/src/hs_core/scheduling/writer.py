"""Overlap-safe session writes, serialised per teacher (closes the M02 residual risk).

M02: the schema only backstops two sessions with the SAME start (unique `teacher_slot_key`). Two
writers with different but overlapping starts can both pass the service check and both insert.
Defences, in order:

1. LEASE  every create / reschedule runs under the teacher's lease (`leases.py`): the
   check-then-insert of two writers for one teacher can no longer interleave. A stalled holder is
   fenced out (`LeaseLost`) before it writes.
2. CHECK  list the teacher's sessions around the new interval; any slot-holding overlap ->
   `SessionConflict` (nothing written).
3. BACKSTOP  the unique `teacher_slot_key` (same start) and `generation_key` (same generated slot)
   answer 409; a 409 is classified by re-reading: same generation_key -> `AlreadyGenerated`,
   otherwise a slot clash -> `SessionConflict`.
4. RE-CHECK  after the insert, list again. If another slot-holding session overlaps ours and ranks
   before ours (`$createdAt`, then `$id`), ours is the later-created one: it is soft-deleted (keys
   nulled, slot freed) and the caller gets `SessionConflict(raced=True)`. This runs even when the
   lease was lost or a writer ignores leases (defence in depth). Pairwise-overlapping chains may
   lose a row that would have survived (A<B<C, A~B, B~C: B and C both roll back); that is safe
   (never a double booking) and self-heals: the next generation run recreates the slot if it is
   free.

Only sessions that HOLD the slot count (`teacher_slot_key` set): cancelled, no_show, void and
soft-deleted rows do not.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from contextlib import nullcontext
from contextvars import ContextVar
from datetime import UTC, datetime, timedelta

from appwrite.query import Query

from ..audit.context import AuditContext, audit_scope, current_audit_context
from ..jobs.idempotency import row_id
from ..repository import Conflict, NotFound, Repository
from . import rules
from .leases import Lease, TeacherLeases

log = logging.getLogger("hs_core.scheduling.writer")
# Free-text reason of the admin action in progress (generate-now, cancel_future): copied onto every
# audited write the action makes, so each audit row carries it.
write_reason: ContextVar[str | None] = ContextVar("scheduling_write_reason", default=None)


def audit_kw(repo: Repository, action: str) -> dict:
    """Keyword arguments for a write through the M06 AuditedRepository (action name + reason).
    A plain repository (no audit layer) takes none."""
    if not hasattr(repo, "service"):
        return {}
    kw: dict = {"action": action}
    reason = write_reason.get()
    if reason:
        kw["reason"] = reason
    return kw


def scope_of(ctx: AuditContext | None):
    """Make `ctx` the audit context of the audited writes made inside the block (or leave the
    ambient one: a job's `job_scope`, a request's scope)."""
    return audit_scope(ctx) if ctx is not None else nullcontext()


PAGE = 100
# Sessions are shorter than a day; looking back one day finds every session that could still overlap
# a new one.
LOOKBACK = timedelta(days=1)


class AlreadyGenerated(Exception):
    """A live session with this generation_key exists (idempotent no-op for the generator)."""

    def __init__(self, existing: dict) -> None:
        super().__init__("session already generated")
        self.existing = existing


def list_all(
    repo: Repository, table: str, queries: list[str] | None = None, page: int = PAGE
) -> list[dict]:
    """Every row matching `queries`, paged with cursors (Appwrite caps offsets and list totals)."""
    out: list[dict] = []
    cursor = None
    while True:
        q = [*(queries or []), Query.limit(page)]
        if cursor:
            q.append(Query.cursor_after(cursor))
        rows = repo.list(table, q)
        out.extend(rows)
        if len(rows) < page:
            return out
        cursor = rows[-1]["$id"]


def _holds_slot(row: dict) -> bool:
    return bool(row.get("teacher_slot_key")) and not row.get("deleted_at")


def _rank(row: dict) -> tuple[str, str]:
    """Total order of creation used by the re-check: earlier ranks first and wins."""
    return (row.get("$createdAt") or row.get("status_changed_at") or "", row["$id"])


class SessionWriter:
    def __init__(
        self, repo: Repository, leases: TeacherLeases, clock: Callable[[], datetime] | None = None
    ) -> None:
        self.audited_repo = repo
        self.leases = leases
        self.clock = clock or (lambda: datetime.now(UTC))

    # ------------------------------------------------------------------ reads
    def teacher_sessions(self, teacher_id: str, start: datetime, end: datetime) -> list[dict]:
        """The teacher's sessions that may overlap [start, end) (index by_teacher_day: teacher_id +
        scheduled_start_at)."""
        return list_all(
            self.audited_repo,
            "session",
            [
                Query.equal("teacher_id", teacher_id),
                Query.greater_than_equal("scheduled_start_at", rules.iso_utc(start - LOOKBACK)),
                Query.less_than("scheduled_start_at", rules.iso_utc(end)),
            ],
        )

    def _find_conflict(
        self, teacher_id: str, start: str, end: str, ignore_id: str | None = None
    ) -> dict | None:
        for other in self.teacher_sessions(
            teacher_id, rules.parse_utc(start), rules.parse_utc(end)
        ):
            if (
                other["$id"] != ignore_id
                and _holds_slot(other)
                and rules.overlaps(
                    start, end, other["scheduled_start_at"], other["scheduled_end_at"]
                )
            ):
                return other
        return None

    def find_conflict(self, teacher_id: str, start: str, end: str) -> dict | None:
        """Read-only pre-check (no lease): the slot-holding session in the way, if any."""
        return self._find_conflict(teacher_id, start, end)

    # ------------------------------------------------------------------ create
    def create(
        self,
        row: dict,
        *,
        lease: Lease | None = None,
        action: str = "session.create",
        ctx: AuditContext | None = None,
    ) -> dict:
        """Insert a `scheduled` session. Raises InvalidSession, SessionConflict, AlreadyGenerated,
        LeaseBusy, LeaseLost."""
        rules.check_session_times(row["scheduled_start_at"], row["scheduled_end_at"])
        row = rules.with_session_keys(row)
        if row["status"] in rules.SLOT_RELEASED:
            raise rules.InvalidTransition("a session is created as scheduled")
        with scope_of(ctx):
            if lease is not None:
                return self._create_locked(row, lease, action)
            with self.leases.hold(row["teacher_id"]) as held:
                return self._create_locked(row, held, action)

    def _create_locked(self, row: dict, lease: Lease | None, action: str) -> dict:
        teacher = row["teacher_id"]
        other = self._find_conflict(teacher, row["scheduled_start_at"], row["scheduled_end_at"])
        if other:
            if row.get("generation_key") and other.get("generation_key") == row["generation_key"]:
                raise AlreadyGenerated(
                    other
                )  # the very same generated slot, not a clash with someone else
            raise rules.SessionConflict(
                f"teacher {teacher} already has a session in this period", other["$id"]
            )
        if lease is not None:
            lease.check(remote=True)  # fenced out? expired between the check and now?
        created = self.insert(row, action)
        return self._recheck_or_rollback(created, self.clock())

    def new_id(self, row: dict) -> str:
        """Deterministic row id for a generated session within one audit context (a job chunk's
        effect key or a request's server key): a retried chunk replays the SAME audit intent and
        the same row (exactly-once audit), instead of minting a second row."""
        eff = getattr(self.audited_repo, "effective_context", None)
        ctx = eff() if eff else current_audit_context.get()
        key = row.get("generation_key")
        if key and ctx is not None:
            return row_id("session", key, ctx.key_base)
        return uuid.uuid4().hex[:32]

    def insert(self, row: dict, action: str) -> dict:
        """Create the row (audited when the repository is the audited wrapper). A unique-index
        409 is classified: same generation_key -> AlreadyGenerated, otherwise a slot clash."""
        rid = self.new_id(row)
        try:
            return self.audited_repo.create(
                "session", rid, row, **audit_kw(self.audited_repo, action)
            )
        except Conflict:
            try:
                old = self.audited_repo.get("session", rid)
            except NotFound:
                old = None
            if old is not None and old.get("deleted_at"):
                # the deterministic id belongs to a soft-deleted earlier incarnation of this slot
                try:
                    return self.audited_repo.create(
                        "session", uuid.uuid4().hex[:32], row, **audit_kw(self.audited_repo, action)
                    )
                except Conflict:
                    pass
            raise self._classify_conflict(row) from None

    def _classify_conflict(self, row: dict) -> Exception:
        key = row.get("generation_key")
        if key:
            existing = self.audited_repo.list(
                "session", [Query.equal("generation_key", key), Query.limit(1)]
            )
            if existing:
                return AlreadyGenerated(existing[0])
        other = self._find_conflict(
            row["teacher_id"], row["scheduled_start_at"], row["scheduled_end_at"]
        )
        return rules.SessionConflict(
            "teacher slot was taken concurrently", other["$id"] if other else None
        )

    def _lost_to(self, mine: dict) -> dict | None:
        """The overlapping slot-holding session that was created before `mine` (so `mine` lost the
        race), if any."""
        if not _holds_slot(mine):
            return None
        for other in self.teacher_sessions(
            mine["teacher_id"],
            rules.parse_utc(mine["scheduled_start_at"]),
            rules.parse_utc(mine["scheduled_end_at"]),
        ):
            if other["$id"] == mine["$id"] or not _holds_slot(other):
                continue
            if rules.overlaps(
                mine["scheduled_start_at"],
                mine["scheduled_end_at"],
                other["scheduled_start_at"],
                other["scheduled_end_at"],
            ) and _rank(other) < _rank(mine):
                return other
        return None

    def _recheck_or_rollback(self, created: dict, now: datetime) -> dict:
        """After the insert: is ours the later-created of a set of overlapping slot-holding
        sessions?
        Then roll it back."""
        winner = self._lost_to(created)
        if winner is None:
            return created
        self.audited_repo.update(
            "session",
            created["$id"],
            rules.soft_delete_session_patch(rules.iso_utc(now)),
            **audit_kw(self.audited_repo, "session.race_rollback"),
        )
        log.warning(
            "session rolled back after losing an overlap race",
            extra={
                "session_id": created["$id"],
                "teacher_id": created["teacher_id"],
                "winner_session_id": winner["$id"],
            },
        )
        raise rules.SessionConflict(
            "lost an overlap race with a concurrent session", winner["$id"], raced=True
        )

    # ------------------------------------------------------------------ reschedule / delete /
    # restore
    def reschedule(
        self,
        session_id: str,
        new_start: datetime,
        new_end: datetime,
        *,
        lease: Lease | None = None,
        ctx: AuditContext | None = None,
    ) -> dict:
        """Move a session (parent/teacher reschedule): same overlap rules, `overridden` = true so
        the generator never touches it again. The generation identity (slot_ref, generation_key)
        is kept: the old slot is NOT regenerated. Audited as `session.reschedule` with the actor
        of `ctx` (the verified principal of the request)."""
        start, end = rules.iso_utc(new_start), rules.iso_utc(new_end)
        rules.check_session_times(start, end)
        with scope_of(ctx):
            current = self.audited_repo.get("session", session_id)
            if lease is not None:
                return self._move_locked(current, start, end, lease)
            with self.leases.hold(current["teacher_id"]) as held:
                return self._move_locked(
                    self.audited_repo.get("session", session_id), start, end, held
                )

    def _move_locked(self, current: dict, start: str, end: str, lease: Lease | None) -> dict:
        other = self._find_conflict(current["teacher_id"], start, end, ignore_id=current["$id"])
        if other:
            raise rules.SessionConflict(
                f"teacher {current['teacher_id']} already has a session in this period",
                other["$id"],
            )
        if lease is not None:
            lease.check(remote=True)
        moved = rules.with_session_keys(
            {**current, "scheduled_start_at": start, "scheduled_end_at": end}
        )
        patch = {
            "scheduled_start_at": start,
            "scheduled_end_at": end,
            "overridden": True,
            "teacher_slot_key": moved["teacher_slot_key"],
            "generation_key": moved["generation_key"],
        }
        try:
            updated = self.audited_repo.update(
                "session",
                current["$id"],
                patch,
                **audit_kw(self.audited_repo, "session.reschedule"),
            )
        except Conflict:
            raise rules.SessionConflict("teacher slot was taken concurrently") from None
        winner = self._lost_to(updated)
        if winner is not None:  # the move lost a race: put the session back where it was
            self.audited_repo.update(
                "session",
                current["$id"],
                {
                    "scheduled_start_at": current["scheduled_start_at"],
                    "scheduled_end_at": current["scheduled_end_at"],
                    "overridden": current.get("overridden", False),
                    "teacher_slot_key": current.get("teacher_slot_key"),
                    "generation_key": current.get("generation_key"),
                },
                **audit_kw(self.audited_repo, "session.reschedule_revert"),
            )
            raise rules.SessionConflict(
                "lost an overlap race with a concurrent session", winner["$id"], raced=True
            )
        return updated

    def soft_delete(
        self,
        session_id: str,
        now: datetime,
        *,
        action: str = "session.soft_delete",
        ctx: AuditContext | None = None,
    ) -> dict:
        with scope_of(ctx):
            return self.audited_repo.update(
                "session",
                session_id,
                rules.soft_delete_session_patch(rules.iso_utc(now)),
                **audit_kw(self.audited_repo, action),
            )

    def restore(self, session_id: str, *, ctx: AuditContext | None = None) -> dict:
        """Undo a soft delete with recomputed keys; a taken slot or identity -> SessionConflict,
        never two active rows."""
        row = self.audited_repo.get("session", session_id)
        patch = rules.restore_session_patch(row)
        with scope_of(ctx), self.leases.hold(row["teacher_id"]):
            if patch["teacher_slot_key"]:
                other = self._find_conflict(
                    row["teacher_id"],
                    row["scheduled_start_at"],
                    row["scheduled_end_at"],
                    ignore_id=session_id,
                )
                if other:
                    raise rules.SessionConflict("the slot was taken meanwhile", other["$id"])
            try:
                restored = self.audited_repo.update(
                    "session", session_id, patch, **audit_kw(self.audited_repo, "session.restore")
                )
            except Conflict:
                raise rules.SessionConflict(
                    "the slot or generated identity was taken meanwhile"
                ) from None
            return self._recheck_or_rollback(restored, self.clock())
