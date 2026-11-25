"""`PermissionService`: the only writer of row and file permissions (ADR 0012).

On a business event it recomputes `desired_permissions` for every affected row and writes only
the DIFFERENCE through the repository (replace the list; never add or remove one entry). Because
the result is computed from state, applying twice is a no-op and a late or repeated run is safe.

Order of operations for a business change (ADR 0012 "Order of operations", `run`):
1. enqueue a `permission-reconcile` job for the event (idempotent by dedupe key);
2. write the business change;
3. recompute and write permissions straight away. If step 3 fails, the queued job retries it
   within about a minute. If step 2 fails, the job finds nothing to change.
Grants are therefore never written before the business write, and a failed revoke is retried.

The `permissions` argument of `Repository.update` replaces `$permissions` and `data={}` changes
no column, so the append-only tables (audit_log, safeguarding_action, ledger_*) only ever get
their permission list rewritten, never their content.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from ..jobs.alerts import Alert, AlertSink, LogAlertSink
from ..jobs.idempotency import row_id
from .context import PermissionContext, RepoFacts
from .policy import DEFAULT_POLICY, Policy
from .queries import equal, rows_where
from .repo_errors import Conflict, NotFound, RepositoryError
from .rules import FILE_BUCKETS, SPECS, desired_file_permissions, desired_permissions
from .strings import canonical, forbidden_in

log = logging.getLogger("hs_core.permissions")

RETRY_DELAY = timedelta(seconds=60)  # "within about a minute" (ADR 0012)

# ------------------------------------------------------------------ scopes and events
SCOPE_KINDS = ("child", "parent", "teacher", "user", "case", "catalogue_version", "table", "row")

EVENT_KINDS = (
    "assignment_started",
    "assignment_ended",
    "substitute_cover",
    "case_created",
    "case_assigned",
    "case_closed",
    "role_changed",
    "guardian_added",
    "guardian_removed",
    "account_deleted",
    "account_deactivated",
    "row_created",
    "catalogue_changed",
    "policy_changed",
    "manual",
)


@dataclass(frozen=True)
class Scope:
    kind: str
    id: str  # for kind "row": "<table>:<row id>"; for "table": the table name

    def __post_init__(self) -> None:
        if self.kind not in SCOPE_KINDS:
            raise ValueError(f"unknown scope kind {self.kind!r}")


@dataclass(frozen=True)
class PermissionEvent:
    kind: str
    event_id: str  # deterministic from the business operation (e.g. "assignment:<id>:end")
    scopes: tuple[Scope, ...]

    def __post_init__(self) -> None:
        if self.kind not in EVENT_KINDS:
            raise ValueError(f"unknown event kind {self.kind!r}")
        if not self.event_id or not self.scopes:
            raise ValueError("an event needs an id and at least one scope")

    @property
    def dedupe_key(self) -> str:
        return f"permission-reconcile:event:{self.event_id}"

    def to_payload(self) -> dict:
        return {
            "event": {
                "kind": self.kind,
                "event_id": self.event_id,
                "scopes": [[s.kind, s.id] for s in self.scopes],
            }
        }

    @staticmethod
    def from_payload(payload: dict) -> PermissionEvent:
        e = payload["event"]
        return PermissionEvent(e["kind"], e["event_id"], tuple(Scope(k, i) for k, i in e["scopes"]))


# Constructors for the events the ticket names. Each returns the scopes whose rows can change.
def assignment_changed(
    event_id: str, child_id: str, teacher_id: str, *, ended: bool = False
) -> PermissionEvent:
    return PermissionEvent(
        "assignment_ended" if ended else "assignment_started",
        event_id,
        (Scope("child", child_id), Scope("teacher", teacher_id)),
    )


def substitute_cover(event_id: str, child_id: str, teacher_id: str) -> PermissionEvent:
    return PermissionEvent(
        "substitute_cover", event_id, (Scope("child", child_id), Scope("teacher", teacher_id))
    )


def case_changed(event_id: str, case_id: str, kind: str = "case_assigned") -> PermissionEvent:
    return PermissionEvent(kind, event_id, (Scope("case", case_id),))


def role_changed(event_id: str, user_id: str) -> PermissionEvent:
    return PermissionEvent("role_changed", event_id, (Scope("user", user_id),))


def guardian_changed(
    event_id: str, parent_id: str, user_id: str, *, removed: bool = False
) -> PermissionEvent:
    return PermissionEvent(
        "guardian_removed" if removed else "guardian_added",
        event_id,
        (Scope("parent", parent_id), Scope("user", user_id)),
    )


def account_changed(event_id: str, user_id: str, *, deleted: bool = False) -> PermissionEvent:
    return PermissionEvent(
        "account_deleted" if deleted else "account_deactivated", event_id, (Scope("user", user_id),)
    )


def row_created(event_id: str, table: str, row_id_: str) -> PermissionEvent:
    return PermissionEvent("row_created", event_id, (Scope("row", f"{table}:{row_id_}"),))


# ------------------------------------------------------------------ reports
class Seen(set):
    """Rows already visited in one run (a row reachable from two scopes is written once). It also
    remembers whether any write failed in the run: a later scope that skips an already-failed row
    must not conclude that everything is fine (access windows advance only on a clean run)."""

    failed = False


@dataclass
class ApplyReport:
    checked: int = 0
    changed: int = 0
    grants: int = 0  # permission entries added
    revokes: int = 0  # permission entries removed
    files_changed: int = 0
    failed: list[tuple[str, str, str]] = field(default_factory=list)  # (table, id, error)
    failed_revokes: int = 0
    windows_written: int = 0
    # Rows the rules could not evaluate (invalid id, bad data): (table, row id, error type) only.
    malformed: list[tuple[str, str, str]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failed

    def merge(self, other: ApplyReport) -> ApplyReport:
        self.checked += other.checked
        self.changed += other.changed
        self.grants += other.grants
        self.revokes += other.revokes
        self.files_changed += other.files_changed
        self.failed += other.failed
        self.failed_revokes += other.failed_revokes
        self.windows_written += other.windows_written
        self.malformed += other.malformed
        return self


class PartialApplication(RepositoryError):
    """Some rows could not be written; the report says which. Retried by the queued job."""

    def __init__(self, report: ApplyReport) -> None:
        super().__init__(
            f"{len(report.failed)} permission writes failed ({report.failed_revokes} were revokes)"
        )
        self.report = report


class RetryQueue:
    """Step 1 of the order of operations: put the event on the job queue (F04 idempotency)."""

    def __init__(
        self, runner: Any, clock: Callable[[], datetime], delay: timedelta = RETRY_DELAY
    ) -> None:
        self._runner, self._clock, self._delay = runner, clock, delay

    def enqueue(self, event: PermissionEvent) -> bool:
        return self._runner.enqueue(
            "permission-reconcile",
            event.dedupe_key,
            run_at=self._clock() + self._delay,
            payload=event.to_payload(),
            trigger="event",
            reason=event.kind,
        )


class NullQueue:
    def __init__(self) -> None:
        self.events: list[PermissionEvent] = []

    def enqueue(self, event: PermissionEvent) -> bool:
        self.events.append(event)
        return True


# ------------------------------------------------------------------ scope expansion
CHILD_DIRECT = (
    ("session_log", "child_id"),
    ("handover_summary", "child_id"),
    ("topic_progress", "child_id"),
    ("school_upload", "child_id"),
    ("assessment", "child_id"),
    ("consent_record", "child_id"),
    ("subscription", "child_id"),
    ("session", "child_id"),
    ("assignment", "child_id"),
    ("feedback", "child_id"),
    ("safeguarding_case", "child_id"),
)
# (table, column, parent table): rows reached through a parent row of the child
CHILD_VIA = (
    ("session_log_topic", "session_log_id", "session_log"),
    ("session_log_struggle", "session_log_id", "session_log"),
    ("assessment_item", "assessment_id", "assessment"),
    ("session_checkin", "session_id", "session"),
    ("session_confirmation", "session_id", "session"),
    ("cancellation", "session_id", "session"),
    ("dispute", "session_id", "session"),
    ("match_request", "subscription_id", "subscription"),
    ("safeguarding_action", "case_id", "safeguarding_case"),
    ("case_status", "case_id", "safeguarding_case"),
)
PARENT_DIRECT = (
    ("guardian_link", "parent_id"),
    ("home_location", "parent_id"),
    ("consent_record", "parent_id"),
    ("invoice", "parent_id"),
    ("payment", "parent_id"),
    ("subscription", "parent_id"),
    ("feedback", "parent_id"),
)
TEACHER_DIRECT = (
    ("teacher_zone", "teacher_id"),
    ("teacher_document", "teacher_id"),
    ("teacher_approval", "teacher_id"),
    ("availability_slot", "teacher_id"),
    ("availability_exception", "teacher_id"),
    ("earning", "teacher_id"),
    ("payout", "teacher_id"),
    ("tier_history", "teacher_id"),
    ("handover_rating", "teacher_id"),
    ("handover_summary", "teacher_id"),
    ("teacher_session_history", "teacher_id"),
    ("assignment", "teacher_id"),
    ("session", "teacher_id"),
)
USER_DIRECT = (
    ("policy_acceptance", "user_id"),
    ("notification", "user_id"),
    ("notification_preference", "user_id"),
    ("device_token", "user_id"),
    ("case_status", "reporter_id"),
)
CATALOGUE_CHILDREN = ("level", "subject", "strand", "topic")
CHUNK = 40  # ids per `equal` query


def _chunks(items: list[str], n: int = CHUNK) -> Iterator[list[str]]:
    for i in range(0, len(items), n):
        yield items[i : i + n]


class PermissionService:
    def __init__(
        self,
        repo: Any,
        *,
        files: Any | None = None,
        labels: Any | None = None,
        clock: Callable[[], datetime] | None = None,
        policy: Policy = DEFAULT_POLICY,
        queue: Any | None = None,
        alerts: AlertSink | None = None,
    ) -> None:
        self.repo = repo
        self.files = files
        self.labels = labels
        self.clock = clock or (lambda: datetime.now(UTC))
        self.policy = policy
        self.queue = queue if queue is not None else NullQueue()
        self.alerts = alerts or LogAlertSink()

    # -------------------------------------------------------------- context
    def context(self) -> PermissionContext:
        return PermissionContext(RepoFacts(self.repo, self.labels), self.clock(), self.policy)

    # -------------------------------------------------------------- order of operations
    def run(
        self, event: PermissionEvent, business_write: Callable[[], Any]
    ) -> tuple[Any, ApplyReport | None]:
        """ADR 0012 order: enqueue the retry job, write the business change, then recompute and
        write permissions. A failing business write propagates (the job will find nothing to do).
        A failing permission write does not undo the business change: it is reported and the
        queued job retries it."""
        if not self.queue.enqueue(event):
            log.info("permission event already queued", extra={"event_id": event.event_id})
        result = business_write()
        try:
            return result, self.apply(event)
        except PartialApplication as exc:
            log.warning(
                "permission apply incomplete; queued job will retry",
                extra={"event_id": event.event_id},
            )
            return result, exc.report
        except RepositoryError:
            log.warning(
                "permission apply failed; queued job will retry", extra={"event_id": event.event_id}
            )
            return result, None

    # -------------------------------------------------------------- applying
    def apply(self, event: PermissionEvent) -> ApplyReport:
        """Recompute every row the event can affect. Raises PartialApplication when any write
        failed (after trying them all), so a job run fails and is retried with backoff."""
        report = ApplyReport()
        seen = Seen()
        ctx = self.context()
        for scope in event.scopes:
            report.merge(self.sync_scope(scope, ctx, seen))
        self.alert_malformed(event.dedupe_key, report)
        if not report.ok:
            raise PartialApplication(report)
        return report

    def sync_scope(
        self,
        scope: Scope,
        ctx: PermissionContext | None = None,
        seen: set[tuple[str, str]] | None = None,
        should_stop: Callable[[], bool] | None = None,
    ) -> ApplyReport:
        ctx = ctx or self.context()
        seen = seen if seen is not None else Seen()
        report = ApplyReport()
        visited: list[str] = []
        stopped = False
        for table, row in self._scope_rows(scope, ctx, seen, visited):
            if should_stop and should_stop():
                stopped = True
                break
            self._sync_row(table, row, ctx, report)
            if not report.ok and isinstance(seen, Seen):
                seen.failed = True
        # Access windows are advanced only after every row of the scope was written: a failed
        # revoke must leave its window open and due so the job retries it.
        if not stopped and report.ok and not getattr(seen, "failed", False):
            for cid in visited:
                self._sync_windows(cid, ctx, report)
        return report

    def sync_row(
        self, table: str, row_id_: str, ctx: PermissionContext | None = None
    ) -> ApplyReport:
        report = ApplyReport()
        ctx = ctx or self.context()
        try:
            row = self.repo.get(table, row_id_)
        except NotFound:
            return report
        self._sync_row(table, row, ctx, report)
        return report

    def _sync_row(self, table: str, row: dict, ctx: PermissionContext, report: ApplyReport) -> None:
        report.checked += 1
        try:
            want = desired_permissions(table, row, ctx)
        except Exception as exc:  # noqa: BLE001 - one bad row must not stop the run
            report.malformed.append((table, str(row.get("$id")), type(exc).__name__))
            return
        have = canonical(row.get("$permissions"))
        if want != have:
            adds, drops = len(set(want) - set(have)), len(set(have) - set(want))
            try:
                # data={} + permissions: replaces the list, changes no column (append-only safe).
                self.repo.update(table, row["$id"], {}, permissions=list(want))
            except (RepositoryError, OSError) as exc:
                report.failed.append((table, row["$id"], type(exc).__name__))
                if drops:
                    report.failed_revokes += 1
                return
            report.changed += 1
            report.grants += adds
            report.revokes += drops
        self._sync_files(table, row, ctx, report)

    def _sync_files(
        self, table: str, row: dict, ctx: PermissionContext, report: ApplyReport
    ) -> None:
        if self.files is None:
            return
        for bucket, (owner, cols) in FILE_BUCKETS.items():
            if owner != table:
                continue
            try:
                want = desired_file_permissions(bucket, row, ctx)
            except Exception as exc:  # noqa: BLE001
                report.malformed.append((f"file:{bucket}", str(row.get("$id")), type(exc).__name__))
                continue
            for col in cols:
                fid = row.get(col)
                if not fid or not self.files.exists(bucket, fid):
                    continue
                have = canonical(self.files.get_permissions(bucket, fid))
                if want == have:
                    continue
                try:
                    self.files.update_permissions(bucket, fid, list(want))
                except (RepositoryError, OSError) as exc:
                    report.failed.append((f"file:{bucket}", fid, type(exc).__name__))
                    if set(have) - set(want):
                        report.failed_revokes += 1
                    continue
                report.files_changed += 1
                report.grants += len(set(want) - set(have))
                report.revokes += len(set(have) - set(want))

    # -------------------------------------------------------------- scope -> rows
    def _get(self, table: str, row_id_: str) -> dict | None:
        try:
            return self.repo.get(table, row_id_)
        except NotFound:
            return None

    def _emit(
        self, table: str, row: dict | None, seen: set[tuple[str, str]]
    ) -> Iterator[tuple[str, dict]]:
        if row is not None and (table, row["$id"]) not in seen:
            seen.add((table, row["$id"]))
            yield table, row

    def _direct(self, table: str, col: str, value: str, seen: set) -> Iterator[tuple[str, dict]]:
        for r in rows_where(self.repo, table, col, [value]):
            yield from self._emit(table, r, seen)

    def _scope_rows(
        self, scope: Scope, ctx: PermissionContext, seen: set[tuple[str, str]], visited: list[str]
    ) -> Iterator[tuple[str, dict]]:
        k, i = scope.kind, scope.id
        if k == "child":
            yield from self._child_rows(i, ctx, seen, visited)
        elif k == "parent":
            yield from self._parent_rows(i, ctx, seen, visited)
        elif k == "teacher":
            yield from self._teacher_rows(i, ctx, seen, visited)
        elif k == "user":
            yield from self._user_rows(i, ctx, seen, visited)
        elif k == "case":
            yield from self._case_rows(i, ctx, seen, visited)
        elif k == "catalogue_version":
            yield from self._emit("catalogue_version", self._get("catalogue_version", i), seen)
            for t in CATALOGUE_CHILDREN:
                yield from self._direct(t, "catalogue_version_id", i, seen)
        elif k == "table":
            from .queries import iter_rows

            for r in iter_rows(self.repo, i):
                yield from self._emit(i, r, seen)
        elif k == "row":
            table, _, rid = i.partition(":")
            yield from self._emit(table, self._get(table, rid), seen)

    def _via(self, ids_by_table: dict[str, list[str]], seen: set) -> Iterator[tuple[str, dict]]:
        for table, col, parent in CHILD_VIA:
            for chunk in _chunks(ids_by_table.get(parent, [])):
                for r in rows_where(self.repo, table, col, chunk):
                    yield from self._emit(table, r, seen)

    def _child_rows(self, child_id: str, ctx: PermissionContext, seen: set, visited: list[str]):
        child = self._get("child", child_id)
        yield from self._emit("child", child, seen)
        ids: dict[str, list[str]] = {}
        for table, col in CHILD_DIRECT:
            for r in rows_where(self.repo, table, col, [child_id]):
                ids.setdefault(table, []).append(r["$id"])
                yield from self._emit(table, r, seen)
        if child:
            yield from self._direct("home_location", "parent_id", child["parent_id"], seen)
        yield from self._via(ids, seen)
        visited.append(child_id)

    def _parent_rows(self, parent_id: str, ctx: PermissionContext, seen: set, visited: list[str]):
        yield from self._emit("parent_profile", self._get("parent_profile", parent_id), seen)
        for table, col in PARENT_DIRECT:
            yield from self._direct(table, col, parent_id, seen)
        for c in rows_where(self.repo, "child", "parent_id", [parent_id]):
            yield from self._child_rows(c["$id"], ctx, seen, visited)

    def _teacher_rows(self, teacher_id: str, ctx: PermissionContext, seen: set, visited: list[str]):
        yield from self._emit("teacher_profile", self._get("teacher_profile", teacher_id), seen)
        children: set[str] = set()
        for table, col in TEACHER_DIRECT:
            for r in rows_where(self.repo, table, col, [teacher_id]):
                if table in ("assignment", "session") and r.get("child_id"):
                    children.add(r["child_id"])
                yield from self._emit(table, r, seen)
        for case in rows_where(self.repo, "safeguarding_case", "teacher_id", [teacher_id]):
            yield from self._case_rows(case["$id"], ctx, seen, visited)
        for cid in sorted(children):
            yield from self._child_rows(cid, ctx, seen, visited)

    def _user_rows(self, user_id: str, ctx: PermissionContext, seen: set, visited: list[str]):
        yield from self._emit("user_profile", self._get("user_profile", user_id), seen)
        for table, col in USER_DIRECT:
            yield from self._direct(table, col, user_id, seen)
        for p in rows_where(self.repo, "parent_profile", "user_id", [user_id]):
            yield from self._parent_rows(p["$id"], ctx, seen, visited)
        for g in rows_where(self.repo, "guardian_link", "user_id", [user_id]):
            yield from self._parent_rows(g["parent_id"], ctx, seen, visited)
        for t in rows_where(self.repo, "teacher_profile", "user_id", [user_id]):
            yield from self._teacher_rows(t["$id"], ctx, seen, visited)
        for col in ("assigned_to_id", "raised_by_id"):
            for case in rows_where(self.repo, "safeguarding_case", col, [user_id]):
                yield from self._case_rows(case["$id"], ctx, seen, visited)

    def _case_rows(self, case_id: str, ctx: PermissionContext, seen: set, visited: list[str]):
        case = self._get("safeguarding_case", case_id)
        yield from self._emit("safeguarding_case", case, seen)
        for table in ("safeguarding_action", "case_status"):
            yield from self._direct(table, "case_id", case_id, seen)
        if case:
            if case.get("child_id"):
                parent_id = ctx.child_parent(case["child_id"])
                yield from self._emit(
                    "parent_profile", self._get("parent_profile", parent_id or ""), seen
                )
                yield from self._emit(
                    "user_profile",
                    self._get("user_profile", ctx.parent_user(parent_id) or ""),
                    seen,
                )
                yield from self._child_rows(case["child_id"], ctx, seen, visited)
            for uid in (case.get("raised_by_id"), ctx.teacher_user_raw(case.get("teacher_id"))):
                yield from self._emit("user_profile", self._get("user_profile", uid or ""), seen)
            if case.get("teacher_id"):
                yield from self._emit(
                    "teacher_profile", self._get("teacher_profile", case["teacher_id"]), seen
                )
            if case.get("teacher_id"):
                yield from self._direct("teacher_document", "teacher_id", case["teacher_id"], seen)

    # -------------------------------------------------------------- access windows
    def _sync_windows(self, child_id: str, ctx: PermissionContext, report: ApplyReport) -> None:
        """Keep one access_window row per (teacher, child) with the next instant at which the
        teacher's time-limited access lapses (discovery index for address-revoke only)."""
        now = ctx.now.isoformat()
        existing = {
            w["teacher_id"]: w
            for w in rows_where(self.repo, "access_window", "child_id", [child_id])
        }
        teachers = set(ctx.candidate_teachers(child_id)) | set(existing)
        for tid in sorted(teachers):
            lapse = (
                ctx.next_lapse(tid, child_id) if tid in ctx.candidate_teachers(child_id) else None
            )
            row = existing.get(tid)
            rid = row_id("access_window", tid, child_id)
            try:
                if lapse is not None:
                    data = {
                        "teacher_id": tid,
                        "child_id": child_id,
                        "expires_at": lapse.isoformat(),
                        "state": "open",
                        "closed_at": None,
                    }
                    if row is None:
                        self.repo.create("access_window", rid, data)
                    elif row.get("state") != "open" or row.get("expires_at") != data["expires_at"]:
                        self.repo.update("access_window", rid, data)
                    else:
                        continue
                    report.windows_written += 1
                elif row is not None and row.get("state") == "open":
                    self.repo.update("access_window", rid, {"state": "closed", "closed_at": now})
                    report.windows_written += 1
            except Conflict:
                continue  # another worker created it; the next recompute reconciles
            except (RepositoryError, OSError) as exc:
                report.failed.append(("access_window", rid, type(exc).__name__))

    # -------------------------------------------------------------- alerts
    def alert_malformed(
        self, dedupe_key: str, report: ApplyReport, job: str = "permission-reconcile"
    ) -> None:
        """Malformed rows are skipped, never fatal: say so (table and row id only, no content)."""
        if not report.malformed:
            return
        sample = ", ".join(f"{t}/{i}" for t, i, _ in report.malformed[:5])
        try:
            self.alerts.send(
                Alert(
                    "permission_malformed_row",
                    job,
                    dedupe_key,
                    f"{len(report.malformed)} rows could not be evaluated: {sample}",
                    len(report.malformed),
                )
            )
        except Exception:  # noqa: BLE001
            log.exception("alert sink failed")

    def alert_over_grant(
        self, dedupe_key: str, detail: str, count: int = 0, job: str = "permission-reconcile"
    ) -> None:
        try:
            self.alerts.send(Alert("permission_over_grant", job, dedupe_key, detail, count))
        except Exception:  # noqa: BLE001 - an alert sink must never break the repair
            log.exception("alert sink failed")


def event_scopes_for_rows(table: str, rows: Iterable[dict]) -> list[Scope]:
    """Scopes to recompute after the given business rows changed (used by reconcile)."""
    out: dict[tuple[str, str], Scope] = {}

    def add(kind: str, id_: str | None) -> None:
        if id_:
            out[(kind, id_)] = Scope(kind, id_)

    for r in rows:
        if table in ("assignment", "session"):
            add("child", r.get("child_id"))
            add("teacher", r.get("teacher_id"))
        elif table == "guardian_link":
            add("parent", r.get("parent_id"))
            add("user", r.get("user_id"))
        elif table == "safeguarding_case":
            add("case", r["$id"])
        elif table in ("user_profile", "role_assignment"):
            add("user", r.get("user_id") or r["$id"])
        elif table == "teacher_profile":
            add("teacher", r["$id"])
        elif table == "parent_profile":
            add("parent", r["$id"])
        elif table == "child":
            add("child", r["$id"])
        elif table == "access_window":
            add("child", r.get("child_id"))
        elif table == "catalogue_version":
            add("catalogue_version", r["$id"])
        elif table in SPECS:
            add("row", f"{table}:{r['$id']}")
    return list(out.values())


__all__ = [
    "ApplyReport",
    "NullQueue",
    "PartialApplication",
    "PermissionEvent",
    "PermissionService",
    "RetryQueue",
    "Scope",
    "account_changed",
    "assignment_changed",
    "case_changed",
    "equal",
    "event_scopes_for_rows",
    "forbidden_in",
    "guardian_changed",
    "role_changed",
    "row_created",
    "substitute_cover",
]
