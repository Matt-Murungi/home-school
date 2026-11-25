"""Scheduled permission jobs (ADR 0012, ADR 0002): address-revoke, permission-reconcile,
permission-audit. Registered in `hs_core/jobs/schedule.py` (the one list).

F04 handlers receive only a JobContext, so the service is bound once at startup with `bind`
(same idiom A01's cleanup job uses; a proper dependency injection is the F04 follow-up). A job
that runs unbound fails loudly instead of silently doing nothing.

All three are idempotent (they recompute from state), chunked (`Continue` with a cursor) and
budget-aware (rows per chunk, `ctx.cancelled`, a wall-clock budget below the 10 s job timeout).
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable

from ..jobs.alerts import Alert
from ..jobs.model import Continue, Done, JobContext
from .audit import Auditor
from .queries import cursor_after, equal, greater_than_equal, less_than_equal, limit, order_asc
from .service import (
    ApplyReport,
    PartialApplication,
    PermissionEvent,
    PermissionService,
    Scope,
    Seen,
    event_scopes_for_rows,
)

log = logging.getLogger("hs_core.permissions.jobs")

WINDOWS_PER_CHUNK = 25
ROWS_PER_CHUNK = 60
TIME_BUDGET_S = 6.0  # job timeout is 10 s (F04): stop starting work after this

_lock = threading.Lock()
_factory: Callable[[], PermissionService] | None = None


def bind(factory: Callable[[], PermissionService] | None) -> None:
    global _factory
    with _lock:
        _factory = factory


def _service() -> PermissionService:
    with _lock:
        f = _factory
    if f is None:
        raise RuntimeError(
            "permission jobs are not bound to a service (call permissions.jobs.bind)"
        )
    return f()


def _stopper(ctx: JobContext, budget_s: float = TIME_BUDGET_S) -> Callable[[], bool]:
    end = time.monotonic() + budget_s
    return lambda: time.monotonic() > end or bool(ctx.cancelled and ctx.cancelled.is_set())


def _fail_if_incomplete(svc: PermissionService, ctx: JobContext, report: ApplyReport) -> None:
    svc.alert_malformed(ctx.dedupe_key, report, job=ctx.name)
    if report.ok:
        return
    if report.failed_revokes:
        # Over-grant stays until a retry succeeds: alert now (the event path was attempt one).
        svc.alert_over_grant(
            ctx.dedupe_key,
            f"{report.failed_revokes} revoke writes failed: {report.failed[:3]}",
            report.failed_revokes,
            job=ctx.name,
        )
    raise PartialApplication(report)


# ------------------------------------------------------------------ address-revoke
def address_revoke(ctx: JobContext) -> Done | Continue:
    """Every few minutes: recompute the child scope (child row, home address, logs, handover) of
    every access window that has lapsed, which removes a teacher's address access 24 h after the
    last session and a substitute's access 24 h after the covered session."""
    svc = _service()
    now = svc.clock()
    after = (ctx.cursor or {}).get("after")
    q = [
        equal("state", ["open"]),
        less_than_equal("expires_at", now.isoformat()),
        order_asc("$id"),
        limit(WINDOWS_PER_CHUNK),
    ] + ([cursor_after(after)] if after else [])
    windows = svc.repo.list("access_window", q)
    stop = _stopper(ctx)
    report = ApplyReport()
    sctx = svc.context()
    seen = Seen()
    processed = 0
    last = after
    for w in windows:
        if stop():
            break
        report.merge(svc.sync_scope(Scope("child", w["child_id"]), sctx, seen))
        processed += 1
        last = w["$id"]
    _fail_if_incomplete(svc, ctx, report)
    if processed < len(windows) or len(windows) == WINDOWS_PER_CHUNK:
        return Continue({"after": last}, items=processed)
    return Done(detail=f"windows={processed} revokes={report.revokes}", items=processed)


# ------------------------------------------------------------------ permission-reconcile
RECONCILE_STAGES = (
    "assignment",
    "session",
    "guardian_link",
    "safeguarding_case",
    "user_profile",
    "role_assignment",
    "teacher_profile",
    "parent_profile",
    "child",
    "catalogue_version",
    "access_window",
)


def permission_reconcile(ctx: JobContext) -> Done | Continue:
    """With an `event` payload: recompute that event's scopes (the retry of step 3 in the order
    of operations). Without (the nightly run): recompute the scopes of every business row
    touched in the lookback window, plus every access window already overdue."""
    svc = _service()
    if "event" in ctx.payload:
        event = PermissionEvent.from_payload(ctx.payload)
        try:
            report = svc.apply(event)
        except PartialApplication as exc:
            _fail_if_incomplete(svc, ctx, exc.report)
            raise
        return Done(detail=f"event={event.kind} changed={report.changed}", items=report.checked)

    now = svc.clock()
    since = (ctx.scheduled_for or now) - svc.policy.reconcile_lookback
    cur = ctx.cursor or {"stage": 0, "after": None}
    stage, after = cur["stage"], cur["after"]
    stop = _stopper(ctx)
    report = ApplyReport()
    sctx = svc.context()
    seen = Seen()
    budget = ROWS_PER_CHUNK
    while stage < len(RECONCILE_STAGES) and budget > 0 and not stop():
        table = RECONCILE_STAGES[stage]
        if table == "access_window":
            filt = [equal("state", ["open"]), less_than_equal("expires_at", now.isoformat())]
        else:
            filt = [greater_than_equal("$updatedAt", since.isoformat())]
        asked = budget
        q = [*filt, order_asc("$id"), limit(asked)] + ([cursor_after(after)] if after else [])
        rows = svc.repo.list(table, q)
        done_rows = 0
        for row in rows:
            if stop():
                break
            for scope in event_scopes_for_rows(table, [row]):
                report.merge(svc.sync_scope(scope, sctx, seen))
            after = row["$id"]
            done_rows += 1
        budget -= done_rows
        if done_rows == len(rows) and len(rows) < asked:
            stage, after = stage + 1, None  # stage exhausted
    _fail_if_incomplete(svc, ctx, report)
    if stage >= len(RECONCILE_STAGES):
        return Done(
            detail=f"checked={report.checked} changed={report.changed}", items=report.checked
        )
    return Continue({"stage": stage, "after": after}, items=report.checked)


# ------------------------------------------------------------------ permission-audit
def _alert_text(summary) -> str:
    d = summary.to_dict()
    head = {k: d[k] for k in ("rows", "over_grants", "under_grants", "forbidden", "malformed")}
    samples = (d["over_samples"] + d["forbidden_samples"] + d["malformed_samples"])[:5]
    return (json.dumps(head, sort_keys=True) + " " + "; ".join(samples))[:600]


def permission_audit(ctx: JobContext) -> Done | Continue:
    """Daily full drift check. Reads only. Alerts on over-grants and forbidden entries."""
    svc = _service()
    auditor = Auditor(svc.repo, svc.files, svc.clock, svc.labels, svc.policy)
    cursor = ctx.cursor or auditor.start()
    cursor, summary = auditor.step(cursor, rows_budget=ROWS_PER_CHUNK, stop=_stopper(ctx))
    if cursor is not None:
        return Continue(cursor, items=summary.rows + summary.files)
    log.info("permission audit finished", extra={"audit": summary.to_dict()})
    if summary.high_severity:
        try:
            svc.alerts.send(
                Alert(
                    "permission_drift",
                    "permission-audit",
                    ctx.dedupe_key,
                    _alert_text(summary),
                    summary.over_grants + summary.forbidden,
                )
            )
        except Exception:  # noqa: BLE001 - the report is still returned
            log.exception("alert sink failed")
    return Done(
        detail=json.dumps(summary.to_dict(), sort_keys=True), items=summary.rows + summary.files
    )


__all__ = ["address_revoke", "bind", "permission_audit", "permission_reconcile"]
