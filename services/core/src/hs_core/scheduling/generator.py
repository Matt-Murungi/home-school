"""SessionGenerator: concrete `session` rows from assignments (SR 7.3, SCH-1).

Entry points
- `generate(assignment_id)`            nightly extension for ONE assignment (create-only +
  leave/availability reconcile).
- `regenerate(assignment_id, from_date)`  B03/B04 after a plan/slot/status change: also PRUNES
  surplus future sessions.
- `cancel_future(assignment_id, from_date, reason)`  B03/B04 on pause / cancel / past_due.
- `generate_now(...)`                  admin entry point (reason required, audited).
- `run_chunk(cursor, ...)`             what the nightly job calls: active subscriptions, one page,
  error-isolated.

Rules (each has a test in tests/test_sched_*.py, mapped in memory.md)
- Identity: a generated session is `slot_ref = <ISO weekday>-<start minute>:<Kampala local date>` of
  its assignment; `generation_key = <assignment_id>:<slot_ref>` is unique, so re-running never
  duplicates. Cancelled/void/no_show/rescheduled rows keep the key and are never recreated; soft
  delete (cancel_future, race rollback) frees it.
- Times are Africa/Kampala wall clock stored as UTC; every date is the Kampala date.
- Only subscriptions with status `active` generate. Sessions start strictly after `now`, at/after
  assignment.start_at and before assignment.end_at. The first session of a trial pairing
  (assignment.is_trial) carries is_trial (MAT-6).
- Teacher leave (availability_exception): the session is created already `cancelled_by_teacher` with
  a cancellation row (reason teacher_leave) and an OPEN substitute match_request (issue #93 edge
  case), idempotently and crash-repairable.
- A slot outside the teacher's weekly availability is not generated and is flagged; existing
  sessions that become unavailable are flagged, never cancelled. A teacher clash is reported
  (scheduling_issue), never overwritten.
- Public holidays: SR is silent. Default policy = no auto-skip; an optional calendar only FLAGS
  (admin decides).
- Writes per teacher are serialised by the lease + re-check in `writer.py`.
"""

from __future__ import annotations

import contextlib
import logging
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from threading import Event

from appwrite.query import Query

from ..jobs.alerts import Alert, AlertSink, LogAlertSink
from ..jobs.idempotency import row_id
from ..repository import Conflict, NotFound, Repository
from . import rules
from .audit import AuditEntry, AuditSink, LogAuditSink
from .issues import AUTO_KINDS, IssueStore
from .leases import LeaseBusy, LeaseLost, TeacherLeases
from .pattern import Occurrence, occurrences, parse_slots, select_slots
from .writer import AlreadyGenerated, SessionWriter, list_all

log = logging.getLogger("hs_core.scheduling")
JOB_NAME = "session-generate"
MIN_REASON = 3
DEFAULT_HORIZON_DAYS = 28


class AssignmentNotFound(LookupError):
    pass


class ReasonRequired(ValueError):
    """Admin and B03/B04 actions must say why (at least 3 characters)."""


def _need_reason(reason: str | None) -> str:
    text = (reason or "").strip()
    if len(text) < MIN_REASON:
        raise ReasonRequired("a reason of at least 3 characters is required")
    return text


@dataclass
class GenerationResult:
    assignment_id: str
    status: str = "generated"  # generated | skipped | deferred | cancelled
    reason: str | None = None
    created: int = 0  # new session rows (including leave-cancelled ones)
    existing: int = 0  # occurrences that already had a session
    conflicts: int = 0  # slots not generated because the teacher is already booked
    flagged: int = 0  # new scheduling_issue rows
    leave_cancelled: int = 0
    pruned: int = 0
    resolved: int = 0
    # horizon end minus the earliest date this run had to create (0 = only the edge was extended)
    lag_days: int = 0


@dataclass
class CancelResult:
    assignment_id: str
    removed: int = 0
    substitute_requests_cancelled: int = 0


@dataclass
class ChunkResult:
    assignments: int = 0
    created: int = 0
    conflicts: int = 0
    flagged: int = 0
    failures: int = 0
    deferred: int = 0
    skipped: int = 0
    max_lag_days: int = 0
    next_cursor: str | None = None
    more: bool = False  # True = call again with next_cursor

    @property
    def items(self) -> int:
        return self.assignments


class _Lease:
    """Lazily acquired lease for one teacher, released when the run ends."""

    def __init__(self, leases: TeacherLeases, teacher_id: str) -> None:
        self._leases, self._teacher, self._stack, self.lease = (
            leases,
            teacher_id,
            contextlib.ExitStack(),
            None,
        )
        self._open = False

    def get(self):
        if not self._open:
            self.lease = self._stack.enter_context(self._leases.hold(self._teacher))
            self._open = True
        return self.lease

    def reset(self) -> None:
        self._stack.close()
        self._stack, self.lease, self._open = contextlib.ExitStack(), None, False

    close = reset


def _slot_date(ref: str) -> date:
    return date.fromisoformat(ref.split(":", 1)[1])


class SessionGenerator:
    def __init__(
        self,
        repo: Repository,
        *,
        clock: Callable[[], datetime] | None = None,
        horizon_days: int = DEFAULT_HORIZON_DAYS,
        lease_ttl: timedelta = timedelta(seconds=30),
        lock_wait: timedelta = timedelta(0),
        holidays: Callable[[date], bool] | None = None,
        alert: AlertSink | None = None,
        audit: AuditSink | None = None,
        leases: TeacherLeases | None = None,
        writer: SessionWriter | None = None,
        issues: IssueStore | None = None,
    ) -> None:
        self.repo = repo
        self.clock = clock or (lambda: datetime.now(UTC))
        self.horizon_days = horizon_days
        self.holidays = holidays
        self.alert = alert or LogAlertSink()
        self.audit = audit or LogAuditSink()
        self.leases = leases or TeacherLeases(repo, self.clock, ttl=lease_ttl, wait=lock_wait)
        self.writer = writer or SessionWriter(repo, self.leases, self.clock)
        self.issues = issues or IssueStore(repo, self.clock)

    # ================================================================== public entry points
    def generate(
        self,
        assignment_id: str,
        *,
        now: datetime | None = None,
        horizon_days: int | None = None,
        cancelled: Event | None = None,
    ) -> GenerationResult:
        """Nightly extension: create missing sessions up to the horizon, reconcile leave, flag
        availability."""
        return self._run(
            assignment_id,
            now=now,
            horizon_days=horizon_days,
            from_date=None,
            reconcile=False,
            cancelled=cancelled,
        )

    def regenerate(
        self,
        assignment_id: str,
        from_date: date,
        *,
        now: datetime | None = None,
        horizon_days: int | None = None,
        cancelled: Event | None = None,
    ) -> GenerationResult:
        """After a plan/slot change from `from_date`: generate what is missing AND prune future
        generated sessions that are
        no longer wanted (never cancelled, rescheduled, started or substitute sessions)."""
        return self._run(
            assignment_id,
            now=now,
            horizon_days=horizon_days,
            from_date=from_date,
            reconcile=True,
            cancelled=cancelled,
        )

    def generate_now(
        self,
        assignment_id: str,
        *,
        actor_id: str,
        reason: str,
        reconcile_from: date | None = None,
        now: datetime | None = None,
    ) -> GenerationResult:
        text = _need_reason(reason)
        if not actor_id:
            raise ReasonRequired("an actor is required")
        if self._get("assignment", assignment_id) is None:
            raise AssignmentNotFound(assignment_id)
        self.audit.record(
            AuditEntry(
                actor_id,
                "scheduling.generate_now",
                "assignment",
                assignment_id,
                text,
                {"reconcile_from": reconcile_from.isoformat() if reconcile_from else None},
            )
        )
        if reconcile_from:
            return self.regenerate(assignment_id, reconcile_from, now=now)
        return self.generate(assignment_id, now=now)

    def cancel_future(
        self,
        assignment_id: str,
        from_date: date,
        reason: str,
        *,
        actor_id: str | None = None,
        now: datetime | None = None,
    ) -> CancelResult:
        """Remove (soft delete) the assignment's future SCHEDULED generated sessions from
        `from_date`
        (pause, cancel,
        past_due, assignment ended). Started/confirmed/cancelled sessions and substitute rows are
        untouched. Open
        substitute requests for removed leave sessions are cancelled. Idempotent."""
        text = _need_reason(reason)
        now = now or self.clock()
        res = CancelResult(assignment_id)
        from_start = rules.local_to_utc(from_date, 0)
        for s in self._live_sessions(assignment_id):
            if s.get("is_substitute") or rules.parse_utc(s["scheduled_start_at"]) <= now:
                continue
            if rules.parse_utc(s["scheduled_start_at"]) < from_start:
                continue
            if s["status"] == "scheduled":
                self.writer.soft_delete(s["$id"], now)
                res.removed += 1
            elif s["status"] == "cancelled_by_teacher" and self._close_substitute_request(
                s["$id"], now
            ):
                res.substitute_requests_cancelled += 1
        self.audit.record(
            AuditEntry(
                actor_id,
                "scheduling.cancel_future",
                "assignment",
                assignment_id,
                text,
                {"from_date": from_date.isoformat(), "removed": res.removed},
            )
        )
        log.info(
            "session generation cancel_future",
            extra={
                "assignment_id": assignment_id,
                "from_date": from_date.isoformat(),
                "removed": res.removed,
                "substitute_requests_cancelled": res.substitute_requests_cancelled,
                "actor_id": actor_id,
                "cause": text[:60],
            },
        )
        return res

    # ================================================================== the nightly sweep
    def run_chunk(
        self,
        cursor: str | None,
        *,
        now: datetime | None = None,
        budget_s: float = 6.0,
        page: int = 20,
        cancelled: Event | None = None,
        run_id: str | None = None,
    ) -> ChunkResult:
        """One page of active subscriptions. Stops at a subscription boundary when the budget is
        spent
        (always finishes at
        least one subscription, so a zero budget still progresses) or the cancel flag is set. A
        failing assignment never
        blocks the others (isolated, alerted, flagged)."""
        now = now or self.clock()
        deadline = time.monotonic() + budget_s
        q = [Query.equal("status", "active"), Query.limit(page)]
        if cursor:
            q.append(Query.cursor_after(cursor))
        subs = self.repo.list("subscription", q)
        out = ChunkResult()
        last_done, stopped = cursor, False
        for n, sub in enumerate(subs):
            if (cancelled is not None and cancelled.is_set()) or (
                n > 0 and time.monotonic() > deadline
            ):
                stopped = True
                break
            if sub.get("deleted_at"):
                last_done = sub["$id"]
                continue
            for a in list_all(
                self.repo, "assignment", [Query.equal("subscription_id", sub["$id"])]
            ):
                if a.get("deleted_at") or (a.get("end_at") and rules.parse_utc(a["end_at"]) <= now):
                    continue
                if cancelled is not None and cancelled.is_set():
                    stopped = True
                    break
                self._sweep_one(a["$id"], now, cancelled, out)
            if stopped:
                break
            last_done = sub["$id"]
        out.more = stopped or len(subs) == page
        out.next_cursor = last_done if out.more else None
        log.info(
            "session generation chunk",
            extra={
                "run_id": run_id,
                "assignments": out.assignments,
                "rows_created": out.created,
                "conflicts": out.conflicts,
                "flagged": out.flagged,
                "failures": out.failures,
                "deferred": out.deferred,
                "skipped": out.skipped,
                "max_lag_days": out.max_lag_days,
                "more": out.more,
            },
        )
        return out

    def _sweep_one(
        self, assignment_id: str, now: datetime, cancelled: Event | None, out: ChunkResult
    ) -> None:
        out.assignments += 1
        try:
            r = self.generate(assignment_id, now=now, cancelled=cancelled)
        # noqa: BLE001 - per-assignment isolation; BaseException (a crash) still propagates
        except Exception as exc:
            out.failures += 1
            self._record_failure(assignment_id, exc)
            return
        out.created += r.created
        out.conflicts += r.conflicts
        out.flagged += r.flagged
        out.max_lag_days = max(out.max_lag_days, r.lag_days)
        if r.status == "deferred":
            out.deferred += 1
        elif r.status == "skipped":
            out.skipped += 1

    def _record_failure(self, assignment_id: str, exc: Exception) -> None:
        kind = type(exc).__name__  # class name only: messages may carry data
        log.error(
            "session generation failed", extra={"assignment_id": assignment_id, "error": kind}
        )
        with contextlib.suppress(Exception):
            self.issues.flag(
                "generation_error",
                assignment_id,
                detail=f"unexpected error: {kind}",
                known=self.issues.open_for(assignment_id),
            )
        with contextlib.suppress(Exception):
            self.alert.send(
                Alert("session_generation_failed", JOB_NAME, f"{JOB_NAME}:{assignment_id}", kind)
            )

    # ================================================================== one assignment
    def _get(self, table: str, row_id_: str) -> dict | None:
        try:
            return self.repo.get(table, row_id_)
        except NotFound:
            return None

    def _live_sessions(self, assignment_id: str) -> list[dict]:
        return [
            s
            for s in list_all(self.repo, "session", [Query.equal("assignment_id", assignment_id)])
            if not s.get("deleted_at")
        ]

    def _run(
        self, assignment_id: str, *, now, horizon_days, from_date, reconcile, cancelled
    ) -> GenerationResult:
        t0 = time.perf_counter()
        now = now or self.clock()
        horizon = horizon_days or self.horizon_days
        a = self._get("assignment", assignment_id)
        if a is None:
            raise AssignmentNotFound(assignment_id)
        res = GenerationResult(assignment_id)
        try:
            self._run_inner(a, res, now, horizon, from_date, reconcile, cancelled)
        finally:
            log.info(
                "session generation",
                extra={
                    "assignment_id": assignment_id,
                    "teacher_id": a.get("teacher_id"),
                    "status": res.status,
                    "reason": res.reason,
                    "rows_created": res.created,
                    "existing": res.existing,
                    "conflicts": res.conflicts,
                    "flagged": res.flagged,
                    "leave_cancelled": res.leave_cancelled,
                    "pruned": res.pruned,
                    "resolved": res.resolved,
                    "lag_days": res.lag_days,
                    "horizon_days": horizon,
                    "reconcile": reconcile,
                    "duration_ms": round((time.perf_counter() - t0) * 1000, 1),
                },
            )
        return res

    def _skip(self, res: GenerationResult, reason: str) -> None:
        res.status, res.reason = "skipped", reason

    def _run_inner(
        self,
        a: dict,
        res: GenerationResult,
        now: datetime,
        horizon: int,
        from_date: date | None,
        reconcile: bool,
        cancelled: Event | None,
    ) -> None:
        aid = a["$id"]
        end_at = rules.parse_utc(a["end_at"]) if a.get("end_at") else None
        if a.get("deleted_at"):
            return self._skip(res, "assignment_deleted")
        if end_at and end_at <= now:
            if reconcile:
                res.pruned += self._prune(
                    self._live_sessions(aid), now, from_date, None, set(), end_at
                )
            return self._skip(res, "assignment_ended")
        sub = self._get("subscription", a["subscription_id"])
        if sub is None or sub.get("deleted_at"):
            return self._skip(res, "subscription_missing")
        if sub["status"] != "active":
            if reconcile and end_at:
                res.pruned += self._prune(
                    self._live_sessions(aid), now, from_date, None, set(), end_at
                )
            return self._skip(res, f"subscription_{sub['status']}")

        known = self.issues.open_for(aid)
        current: set[str] = set()
        teacher_id = a["teacher_id"]

        def flag(
            kind: str,
            *,
            slot_ref: str | None = None,
            session_id: str | None = None,
            detail: str = "",
        ) -> None:
            from .issues import issue_key

            current.add(issue_key(kind, aid, slot_ref))
            if self.issues.flag(
                kind,
                aid,
                slot_ref=slot_ref,
                teacher_id=teacher_id,
                session_id=session_id,
                detail=detail,
                known=known,
            ):
                res.flagged += 1

        teacher = self._get("teacher_profile", teacher_id)
        if teacher is None or teacher.get("status") != "active":
            flag(
                "teacher_inactive",
                detail=f"teacher status {teacher.get('status') if teacher else 'missing'}",
            )
            return self._skip(res, "teacher_inactive")
        plan = self._get("plan", sub["plan_id"])
        if plan is None:
            return self._skip(res, "plan_missing")
        slots, invalid = parse_slots(sub.get("preferred_slots"))
        if invalid:
            flag("invalid_slot", detail="; ".join(invalid)[:300])
        chosen, shortfall = select_slots(slots, plan["sessions_per_week"])
        if not chosen:
            flag("no_slots", detail="the subscription has no usable preferred slots")
            return self._skip(res, "no_slots")
        if shortfall:
            flag(
                "slot_shortfall",
                detail=f"{shortfall} fewer preferred slots than the plan's sessions per week",
            )
        home = self._home_location(sub["parent_id"])
        if home is None:
            flag("no_home_location", detail="the parent has no usable home location")
            return self._skip(res, "no_home_location")

        today = rules.local_date(now)
        last = today + timedelta(days=horizon)
        if end_at:
            last = min(last, rules.local_date(end_at))
        first = max(from_date or today, rules.local_date(rules.parse_utc(a["start_at"])))
        occs = [
            o
            for o in occurrences(chosen, first, last)
            if o.start > now
            and o.start >= rules.parse_utc(a["start_at"])
            and (end_at is None or o.start < end_at)
        ]
        existing = self._live_sessions(aid)
        existing_refs = {s["slot_ref"] for s in existing if s.get("slot_ref")}
        leaves = self._leaves(teacher_id, now, rules.local_to_utc(last, 1440))
        avail = self._availability(teacher_id)

        # --- reconcile what already exists
        # --------------------------------------------------------------------------
        for s in existing:
            start = rules.parse_utc(s["scheduled_start_at"])
            if s.get("is_substitute") or start <= now:
                continue
            on_leave = self._leave_overlap(leaves, s["scheduled_start_at"], s["scheduled_end_at"])
            if s["status"] == "scheduled" and on_leave:
                self._cancel_for_leave(s, now)
                res.leave_cancelled += 1
            elif s["status"] == "cancelled_by_teacher" and on_leave and s.get("slot_ref"):
                self._repair_leave_artifacts(s, now)
            elif (
                s["status"] == "scheduled"
                and s.get("slot_ref")
                and not s.get("overridden")
                and not self._covered(avail, s)
            ):
                flag(
                    "outside_availability",
                    slot_ref=s["slot_ref"],
                    session_id=s["$id"],
                    detail="the teacher's weekly availability no longer covers this session",
                )
        if reconcile:
            desired = {o.ref for o in occurrences(chosen, first, last)}
            res.pruned += self._prune(existing, now, from_date, last, desired, end_at)

        # --- create what is missing
        # ---------------------------------------------------------------------------------
        pending = [o for o in occs if o.ref not in existing_refs]
        res.existing = len(occs) - len(pending)
        trial_open = bool(a.get("is_trial")) and not any(
            s.get("is_trial") and s["status"] not in rules.SLOT_RELEASED for s in existing
        )
        held = _Lease(self.leases, teacher_id)
        created_dates: list[date] = []
        try:
            for occ in pending:
                if cancelled is not None and cancelled.is_set():
                    res.status = "cancelled"
                    break
                row = {
                    "child_id": a["child_id"],
                    "teacher_id": teacher_id,
                    "subscription_id": sub["$id"],
                    "home_location_id": home,
                    "assignment_id": aid,
                    "slot_ref": occ.ref,
                    "scheduled_start_at": rules.iso_utc(occ.start),
                    "scheduled_end_at": rules.iso_utc(occ.end),
                    "status": "scheduled",
                    "status_changed_at": rules.iso_utc(now),
                    "is_substitute": False,
                    "is_trial": False,
                    "overridden": False,
                }
                if self._leave_overlap(leaves, row["scheduled_start_at"], row["scheduled_end_at"]):
                    if self._create_leave_session(row, now):
                        res.created += 1
                        res.leave_cancelled += 1
                        created_dates.append(occ.date)
                    continue
                if not self._covered(avail, row):
                    flag(
                        "outside_availability",
                        slot_ref=occ.ref,
                        detail="outside the teacher's weekly availability: not generated",
                    )
                    continue
                other = self.writer.find_conflict(
                    teacher_id, row["scheduled_start_at"], row["scheduled_end_at"]
                )
                if other and other.get("generation_key") == rules.generation_key_for(aid, occ.ref):
                    res.existing += 1  # a concurrent run of this very assignment just created it
                    continue
                if other:
                    flag(
                        "teacher_conflict",
                        slot_ref=occ.ref,
                        session_id=other["$id"],
                        detail="the teacher already has a session then",
                    )
                    res.conflicts += 1
                    continue
                row["is_trial"] = trial_open
                outcome = self._create_one(row, held)
                if outcome == "created":
                    res.created += 1
                    created_dates.append(occ.date)
                    trial_open = False
                    if self.holidays and self.holidays(occ.date):
                        flag(
                            "holiday",
                            slot_ref=occ.ref,
                            detail="public holiday: created, the admin decides",
                        )
                elif outcome == "exists":
                    res.existing += 1
                elif outcome == "busy":
                    res.status, res.reason = "deferred", "teacher_busy"
                    break
                else:  # conflict
                    res.conflicts += 1
                    flag(
                        "teacher_conflict",
                        slot_ref=occ.ref,
                        session_id=outcome[1] if isinstance(outcome, tuple) else None,
                        detail="the teacher already has a session then",
                    )
        finally:
            held.close()

        if created_dates:
            res.lag_days = (last - min(created_dates)).days
        if res.status == "generated":
            res.resolved = self._auto_resolve(known, current, today, first, last)

    # ================================================================== creating
    def _create_one(self, row: dict, held: _Lease):
        """'created' | 'exists' | 'busy' | ('conflict', conflicting_session_id)."""
        for attempt in (1, 2):
            try:
                self.writer.create(row, lease=held.get())
                return "created"
            except AlreadyGenerated:
                return "exists"
            except rules.SessionConflict as exc:
                return ("conflict", exc.conflicting_id)
            except LeaseBusy:
                return "busy"
            except LeaseLost:
                held.reset()  # expired mid-run: take a fresh lease once
                if attempt == 2:
                    return "busy"
        return "busy"

    def _create_leave_session(self, row: dict, now: datetime) -> bool:
        """Teacher leave on a generated slot: the session exists as cancelled_by_teacher (slot
        released), with its
        cancellation row and an open substitute request. Created session-first; a crash before the
        artifacts is repaired
        by the next run (`_repair_leave_artifacts`)."""
        row = rules.with_session_keys({**row, "status": "cancelled_by_teacher"})
        try:
            created = self.repo.create("session", uuid.uuid4().hex[:32], row)
        except Conflict:
            return False  # a concurrent worker created it (generation_key is unique)
        self._leave_artifacts(created, now)
        return True

    def _cancel_for_leave(self, s: dict, now: datetime) -> None:
        """Existing scheduled session now on leave. Commit point = the status update (last),
        artifacts
        first."""
        self._leave_artifacts(s, now)
        self.repo.update(
            "session",
            s["$id"],
            rules.transition_patch(s, "cancelled_by_teacher", rules.iso_utc(now)),
        )

    def _repair_leave_artifacts(self, s: dict, now: datetime) -> None:
        existing = self.repo.list(
            "cancellation", [Query.equal("session_id", s["$id"]), Query.limit(1)]
        )
        if existing and existing[0].get("reason") != "teacher_leave":
            return  # cancelled by the teacher themselves: that flow owns its substitute request
        self._leave_artifacts(s, now)

    def _leave_artifacts(self, s: dict, now: datetime) -> None:
        sid = s["$id"]
        self._ensure(
            "cancellation",
            row_id("cancellation", sid),
            {
                "session_id": sid,
                "by_role": "teacher",
                "cancelled_by_id": s["teacher_id"],
                "reason": "teacher_leave",
                "is_late": False,
                "cancelled_at": rules.iso_utc(now),
            },
        )
        self._ensure(
            "match_request",
            row_id("match_request", "substitute", sid),
            {
                "subscription_id": s["subscription_id"],
                "session_id": sid,
                "type": "substitute",
                "status": "open",
                "attempts": 0,
            },
        )

    def _ensure(self, table: str, id_: str, data: dict) -> None:
        if self._get(table, id_) is not None:
            return
        with contextlib.suppress(Conflict):  # a concurrent run (or the unique index) already has it
            self.repo.create(table, id_, data)

    def _close_substitute_request(self, session_id: str, now: datetime) -> bool:
        req = self._get("match_request", row_id("match_request", "substitute", session_id))
        if req is None or req["status"] not in ("open", "offering", "admin_queue"):
            return False
        self.repo.update(
            "match_request", req["$id"], {"status": "cancelled", "closed_at": rules.iso_utc(now)}
        )
        return True

    # ================================================================== pruning (regenerate)
    def _prune(
        self,
        existing: list[dict],
        now: datetime,
        from_date: date | None,
        last: date | None,
        desired: set[str],
        end_at: datetime | None,
    ) -> int:
        """Soft-delete future SCHEDULED generated sessions that the pattern no longer wants.
        Rescheduled (overridden), substitute, started and cancelled sessions stay; sessions
        starting at/after an assignment end go regardless."""
        n = 0
        for s in existing:
            if s.get("is_substitute") or not s.get("slot_ref") or s["status"] != "scheduled":
                continue
            start = rules.parse_utc(s["scheduled_start_at"])
            ref_date = _slot_date(s["slot_ref"])
            if start <= now or (from_date and ref_date < from_date):
                continue
            beyond_end = end_at is not None and start >= end_at
            unwanted = (
                last is not None
                and ref_date <= last
                and s["slot_ref"] not in desired
                and not s.get("overridden")
            )
            if beyond_end or unwanted:
                self.writer.soft_delete(s["$id"], now)
                n += 1
        return n

    # ================================================================== reads
    def _home_location(self, parent_id: str) -> str | None:
        homes = [
            h
            for h in list_all(self.repo, "home_location", [Query.equal("parent_id", parent_id)])
            if not h.get("deleted_at")
        ]
        defaults = [h for h in homes if h.get("is_default")]
        pick = defaults or (homes if len(homes) == 1 else [])
        return pick[0]["$id"] if pick else None

    def _leaves(
        self, teacher_id: str, start: datetime, end: datetime
    ) -> list[tuple[datetime, datetime]]:
        rows = list_all(
            self.repo,
            "availability_exception",
            [
                Query.equal("teacher_id", teacher_id),
                Query.less_than("starts_at", rules.iso_utc(end)),
            ],
        )
        out = []
        for r in rows:
            lo, hi = rules.parse_utc(r["starts_at"]), rules.parse_utc(r["ends_at"])
            if not r.get("deleted_at") and hi > start and lo < end:
                out.append((lo, hi))
        return out

    @staticmethod
    def _leave_overlap(leaves: list[tuple[datetime, datetime]], start: str, end: str) -> bool:
        s, e = rules.parse_utc(start), rules.parse_utc(end)
        return any(lo < e and s < hi for lo, hi in leaves)

    def _availability(self, teacher_id: str) -> dict[int, list[tuple[int, int]]]:
        by_day: dict[int, list[tuple[int, int]]] = {}
        for r in list_all(self.repo, "availability_slot", [Query.equal("teacher_id", teacher_id)]):
            if not r.get("deleted_at"):
                by_day.setdefault(r["weekday"], []).append((r["start_minute"], r["end_minute"]))
        merged: dict[int, list[tuple[int, int]]] = {}
        for wd, spans in by_day.items():
            out: list[tuple[int, int]] = []
            for lo, hi in sorted(spans):
                if out and lo <= out[-1][1]:
                    out[-1] = (out[-1][0], max(out[-1][1], hi))
                else:
                    out.append((lo, hi))
            merged[wd] = out
        return merged

    @staticmethod
    def _covered(avail: dict[int, list[tuple[int, int]]], row: dict) -> bool:
        """Is the session inside one merged availability span of its Kampala weekday?"""
        start = rules.parse_utc(row["scheduled_start_at"]).astimezone(rules.KAMPALA)
        end = rules.parse_utc(row["scheduled_end_at"]).astimezone(rules.KAMPALA)
        lo = start.hour * 60 + start.minute
        hi = lo + int((end - start).total_seconds() // 60)
        return any(a <= lo and hi <= b for a, b in avail.get(start.isoweekday(), []))

    # ================================================================== issues
    def _auto_resolve(
        self, known: dict[str, dict], current: set[str], today: date, first: date, last: date
    ) -> int:
        n = 0
        for key, row in list(known.items()):
            kind, ref = row["kind"], row.get("slot_ref")
            if key in current or (kind not in AUTO_KINDS and kind != "holiday"):
                continue
            if ref:
                d = _slot_date(ref)
                ok = d < today or (
                    kind in ("teacher_conflict", "outside_availability") and first <= d <= last
                )
            else:
                ok = True
            if ok:
                self.issues.resolve(row)
                known.pop(key, None)
                n += 1
        return n


def build_generator(
    settings, repo: Repository, alert: AlertSink | None = None, audit: AuditSink | None = None
) -> SessionGenerator:
    return SessionGenerator(
        repo,
        horizon_days=settings.session_gen_horizon_days,
        lease_ttl=timedelta(seconds=settings.session_gen_lease_ttl_s),
        lock_wait=timedelta(seconds=settings.session_gen_lock_wait_s),
        alert=alert,
        audit=audit,
    )


__all__ = [
    "AssignmentNotFound",
    "CancelResult",
    "ChunkResult",
    "GenerationResult",
    "ReasonRequired",
    "SessionGenerator",
    "build_generator",
    "Occurrence",
]
