"""`role-reconcile` (A03): find and repair drift between Appwrite labels and `role_assignment`.

Policy (one dataclass, each line an owner decision with a default; docs/ops/roles.md):

| drift                                              | default repair                              |
|----------------------------------------------------|---------------------------------------------|
| active row, label missing, non-staff role          | set the label (finish the grant)            |
| active row, label missing, staff role              | close the row (a grant that never returned  |
|                                                    | success must not leave a staff role behind) |
| row closed (revoked), label still set              | remove the label (finish the revoke)        |
| label, no row ever, non-staff role                 | write the history row                       |
| label, no row ever, staff role                     | REPORT ONLY (an owner-set bootstrap label   |
|                                                    | must not vanish); `remove_unrecorded_staff` |
| active row of an account that no longer exists     | close the row                               |

Rows younger than `grace_s` are skipped: a grant in flight writes its row before its label.
The job is idempotent (it recomputes from state), chunked by (phase, cursor), and never writes
anything a failing step cannot repeat. Repairs go through `RoleService`, so they are audited as
the `role-reconcile` system actor and emit `role_changed`.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import timedelta

from appwrite.query import Query

from ..audit import AuditContext, audit_scope, current_audit_context
from ..auth_otp.store import parse
from ..jobs.alerts import Alert, AlertSink
from ..jobs.model import Continue, Done, JobContext
from ..repository import RepositoryError
from .directory import DirectoryUnavailable
from .model import ROLES, STAFF_ROLES, clean_labels
from .service import TABLE, Actor, RoleError, RoleService

log = logging.getLogger("hs_core.roles.reconcile")
SYSTEM = Actor.system("role-reconcile")
CHUNK = 40


@dataclass(frozen=True)
class ReconcilePolicy:
    grace_s: int = 300
    remove_unrecorded_staff: bool = False
    close_incomplete_staff_grants: bool = True
    backfill_mobile_rows: bool = True


@dataclass
class DriftReport:
    found: list[tuple[str, str, str]] = field(default_factory=list)  # (kind, user, role)
    repaired: int = 0
    failed: int = 0
    reported_only: int = 0
    malformed: int = 0  # rows that could not be read at all (isolated, never repaired)

    def merge(self, other: DriftReport) -> None:
        self.found += other.found
        self.repaired += other.repaired
        self.failed += other.failed
        self.reported_only += other.reported_only
        self.malformed += other.malformed


# Phases: 0..6 rows-without-label per role, 7..13 labels-without-row per role.
PHASES = [("rows", r) for r in ROLES] + [("labels", r) for r in ROLES]


def reconcile_chunk(
    svc: RoleService,
    phase: int,
    after: str | None,
    policy: ReconcilePolicy = ReconcilePolicy(),
    limit: int = CHUNK,
) -> tuple[tuple[int, str | None] | None, DriftReport]:
    """Process one page of one phase. Returns the next (phase, cursor) or None when finished.
    Writes are audited as actor `system` + job `role-reconcile`: the F04 runner sets that scope
    for the job; called directly (tests, scripts) a per-chunk system context is used."""
    if current_audit_context.get() is None:
        ctx = AuditContext.system("role-reconcile", run_key=f"chunk:{phase}:{after or '-'}")
        with audit_scope(ctx):
            return reconcile_chunk(svc, phase, after, policy, limit)
    kind, role = PHASES[phase]
    report = DriftReport()
    if kind == "rows":
        q = [Query.equal("role", [role]), Query.order_asc("$id"), Query.limit(limit)]
        if after:
            q.append(Query.cursor_after(after))
        rows = svc.repo.list(TABLE, q)
        for r in rows:
            if r.get("active_key"):
                _isolated(
                    report,
                    "role_assignment",
                    r.get("$id"),
                    _row_without_label,
                    svc,
                    r,
                    role,
                    policy,
                    report,
                )
        last = rows[-1]["$id"] if rows else None
        more = len(rows) == limit
    else:
        users = svc.users.labelled(role, after, limit)
        for u in users:
            _isolated(report, "user", u.id, _label_without_row, svc, u.id, role, policy, report)
        last = users[-1].id if users else None
        more = len(users) == limit
    if more:
        return (phase, last), report
    return ((phase + 1, None) if phase + 1 < len(PHASES) else None), report


def _isolated(report, table: str, row_id, fn, *args) -> None:
    """One bad row must never stop the job (the cursor advances past it, the page continues).
    Outages are NOT isolated: they propagate so F04 retries the chunk. Only the table and the
    row id are logged (no personal data, never the row's values)."""
    try:
        fn(*args)
    except (RepositoryError, DirectoryUnavailable):
        raise
    except Exception as exc:  # noqa: BLE001 - malformed data: count, log ids, continue
        report.malformed += 1
        report.failed += 1
        log.warning(
            "role reconcile skipped a malformed row",
            extra={"table": table, "row_id": str(row_id)[:64], "error_type": type(exc).__name__},
        )


def _row_without_label(svc, row, role, policy, report) -> None:
    uid = row["user_id"]
    if not isinstance(uid, str) or not 0 < len(uid) <= 36:
        raise ValueError("malformed user id")
    granted = parse(row["granted_at"])  # KeyError / TypeError / ValueError = malformed
    if svc.clock() - granted < timedelta(seconds=policy.grace_s):
        return  # in flight
    target = svc.users.get(uid)
    if target is not None and role in clean_labels(target.labels):
        return
    report.found.append(("row_without_label", uid, role))
    try:
        if target is None or (role in STAFF_ROLES and policy.close_incomplete_staff_grants):
            svc.revoke_role(SYSTEM, uid, role, reason="reconcile: grant never completed")
        elif role in STAFF_ROLES:
            report.reported_only += 1
            return
        else:
            svc.grant_role(SYSTEM, uid, role, reason="reconcile: completing grant")
        report.repaired += 1
    except RoleError as e:
        log.warning("role reconcile repair refused", extra={"code": e.code, "role": role})
        report.failed += 1
    except (RepositoryError, DirectoryUnavailable):
        report.failed += 1


def _label_without_row(svc, uid, role, policy, report) -> None:
    rows = svc._rows(uid, role)
    if any(r.get("active_key") for r in rows):
        return
    try:
        if rows:  # a closed row: the revoke never finished
            report.found.append(("label_after_revoke", uid, role))
            svc.revoke_role(SYSTEM, uid, role, reason="reconcile: revoke never completed")
            report.repaired += 1
            return
        report.found.append(("label_without_row", uid, role))
        if role in STAFF_ROLES:
            if policy.remove_unrecorded_staff:
                svc.revoke_role(SYSTEM, uid, role, reason="reconcile: unrecorded staff label")
                report.repaired += 1
            else:
                report.reported_only += 1
        elif policy.backfill_mobile_rows:
            svc.backfill_row(uid, role, "reconcile")
            report.repaired += 1
        else:
            report.reported_only += 1
    except RoleError as e:
        log.warning("role reconcile repair refused", extra={"code": e.code, "role": role})
        report.failed += 1
    except (RepositoryError, DirectoryUnavailable):
        report.failed += 1


def reconcile_all(svc: RoleService, policy: ReconcilePolicy = ReconcilePolicy()) -> DriftReport:
    total, cur = DriftReport(), (0, None)
    while cur is not None:
        cur, rep = reconcile_chunk(svc, cur[0], cur[1], policy)
        total.merge(rep)
    return total


# ---------------------------------------------------------------------- F04 job
_lock = threading.Lock()
_factory: Callable[[], tuple[RoleService, AlertSink | None, ReconcilePolicy]] | None = None


def bind(factory) -> None:
    global _factory
    with _lock:
        _factory = factory


def role_reconcile(ctx: JobContext) -> Done | Continue:
    with _lock:
        f = _factory
    if f is None:
        raise RuntimeError("role-reconcile ran before the role service was bound")
    svc, alerts, policy = f()
    cur = ctx.cursor or {}
    phase, after = int(cur.get("phase", 0)), cur.get("after")
    nxt, rep = reconcile_chunk(svc, phase, after, policy)
    if rep.found:
        log.warning(
            "role drift",
            extra={"found": len(rep.found), "repaired": rep.repaired, "failed": rep.failed},
        )
    if rep.malformed and alerts is not None:
        alerts.send(
            Alert(
                "role_malformed_row",
                ctx.name,
                ctx.dedupe_key,
                f"{rep.malformed} role rows or users could not be read (ids in the log)",
                rep.malformed,
            )
        )
    if (rep.failed - rep.malformed or rep.reported_only) and alerts is not None:
        alerts.send(
            Alert(
                "role_drift",
                ctx.name,
                ctx.dedupe_key,
                f"{rep.failed - rep.malformed} repairs failed, {rep.reported_only} reported only "
                f"(kinds: {sorted({k for k, _, _ in rep.found})})",
                rep.failed - rep.malformed + rep.reported_only,
            )
        )
    if nxt is None:
        return Done(detail=f"found={len(rep.found)} repaired={rep.repaired}", items=len(rep.found))
    return Continue({"phase": nxt[0], "after": nxt[1]}, items=len(rep.found))
