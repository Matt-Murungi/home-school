"""`audit-recover`: settles audit intents left behind by a crash (F04 job, every 10 minutes).

For every intent row older than the grace period (and younger than the lookback) that has no
outcome row, the entity is read and compared with the intent's before/after state hashes:

- matches the after state  -> the change landed; the intent row already says so; nothing to write;
- matches the before state -> the change never landed; a `not_applied` outcome row is written;
- matches neither, or the states cannot be compared (non-table actions, no-op updates, changes
  to redacted fields only, a later change by someone else) -> reported as `unverifiable`, nothing
  is written and nothing is guessed (logged by count; an operator reviews).

Idempotent: the outcome row has a fixed id. Chunked (Continue cursor) so a long backlog fits the
job timeout. Runs only with the raw repository: it never writes an audited entity.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from appwrite.query import Query

from ..jobs.model import Continue, Done, JobContext
from ..repository import NotFound, Repository
from . import payload
from .service import TABLE, AuditService, iso, meta_of

log = logging.getLogger("hs_core.audit.recover")
RECOVER_JOB = "audit-recover"
CHUNK_ROWS = 40
_VERIFIABLE_OPS = ("create", "update", "delete")


@dataclass
class RecoverResult:
    examined: int = 0
    applied: int = 0
    not_applied: int = 0
    unverifiable: int = 0
    failed_but_applied: int = 0
    skipped_young: int = 0
    already_settled: int = 0
    errors: int = 0
    next_cursor: str | None = None
    unverifiable_ids: list[str] = field(default_factory=list)


def recover_audit(
    repo: Repository,
    service: AuditService,
    *,
    now: datetime,
    grace: timedelta,
    lookback: timedelta,
    page: int = 100,
    cursor: str | None = None,
    max_rows: int | None = None,
    job: str = RECOVER_JOB,
    run_key: str = RECOVER_JOB,
) -> RecoverResult:
    res = RecoverResult()
    young_after = iso(now - grace)
    scanned = 0
    while True:
        q = [
            Query.greater_than_equal("occurred_at", iso(now - lookback)),
            Query.order_asc("occurred_at"),
        ]
        if cursor:
            q.append(Query.cursor_after(cursor))
        q.append(Query.limit(page))
        rows = repo.list(TABLE, q)
        for row in rows:
            cursor = row["$id"]
            scanned += 1
            status = meta_of(row).get("status")
            if status == "intent":
                _settle(repo, service, row, res, young_after, job, run_key)
            elif status == "failed":
                _recheck_failed(repo, service, row, res, young_after, job, run_key)
            if max_rows is not None and scanned >= max_rows:
                res.next_cursor = cursor
                return res
        if len(rows) < page:
            return res


def _settle(repo, service, row, res: RecoverResult, young_after: str, job: str, run_key: str):
    if row["occurred_at"] > young_after:
        res.skipped_young += 1
        return
    try:
        if service.has_outcome(row["$id"]):
            res.already_settled += 1
            return
        res.examined += 1
        verdict = _verdict(repo, row)
        if verdict == "not_applied":
            service.mark_not_applied(row, job=job, run_key=run_key)
            res.not_applied += 1
        elif verdict == "applied":
            res.applied += 1
        else:
            res.unverifiable += 1
            res.unverifiable_ids.append(row["$id"])
            # metric + alert hook (not only a log line): an operator must settle it
            service.bump(
                "unverifiable_intent", intent=row["$id"], entity=row["entity"], action=row["action"]
            )
    except Exception as e:  # noqa: BLE001 - one bad row must not stop the sweep
        res.errors += 1
        log.warning("audit recovery skipped a row", extra={"error_type": type(e).__name__})


def _recheck_failed(repo, service, row, res: RecoverResult, young_after: str, job: str, run_key):
    """A `failed` row says the write did not happen. If the entity's state equals the intent's
    after-state the row is wrong (a driver retried internally: Conflict/NotFound after the write
    landed): add a corrective `applied_after_failed` row (never edit). Idempotent (fixed id).
    The same state can also come from a later successful retry of the change; the corrective row
    then still tells the truth (the intended state is in place)."""
    if row["occurred_at"] > young_after:
        return
    try:
        ref = meta_of(row).get("ref")
        intent = service.get_row(ref) if ref else None
        if intent is None or service.has_corrective(ref):
            return
        if _verdict(repo, intent) == "applied":
            service.mark_applied_after_failed(intent, job=job, run_key=run_key)
            res.failed_but_applied += 1
            service.bump(
                "failed_but_applied", intent=ref, entity=intent["entity"], action=intent["action"]
            )
    except Exception as e:  # noqa: BLE001
        res.errors += 1
        log.warning("audit recovery skipped a failed row", extra={"error_type": type(e).__name__})


def _verdict(repo: Repository, row: dict[str, Any]) -> str:
    m = meta_of(row)
    if m.get("op") not in _VERIFIABLE_OPS:
        return "unverifiable"
    before, after = m.get("before_state"), m.get("after_state")
    if not before or not after or before == after:
        return "unverifiable"
    entity = row["entity"]
    try:
        current = payload.stable_hash(
            payload.redact_state(entity, repo.get(entity, row["entity_id"]))
        )
    except NotFound:
        current = "none"
    if current == after:
        return "applied"
    if current == before:
        return "not_applied"
    return "unverifiable"


def report(res: RecoverResult) -> dict[str, Any]:
    """What the `audit-recover` sweep found, for the job log and for operators."""
    return {
        "examined": res.examined,
        "applied": res.applied,
        "not_applied": res.not_applied,
        "unverifiable": res.unverifiable,
        "unverifiable_ids": res.unverifiable_ids[:50],
        "failed_but_applied": res.failed_but_applied,
        "errors": res.errors,
    }


# ----------------------------------------------------------------------- job handler
def make_recover_handler(
    repo: Repository,
    service: AuditService,
    *,
    clock: Callable[[], datetime] | None = None,
    lookback: timedelta = timedelta(hours=6),
    grace: timedelta = timedelta(minutes=5),
    chunk: int = CHUNK_ROWS,
):
    now_fn = clock or (lambda: datetime.now(UTC))

    def handler(ctx: JobContext) -> Done | Continue:
        hours = ctx.payload.get("lookback_hours") if ctx.payload else None
        look = timedelta(hours=float(hours)) if hours else lookback
        res = recover_audit(
            repo, service, now=now_fn(), grace=grace, lookback=look,
            cursor=ctx.cursor if isinstance(ctx.cursor, str) else None, max_rows=chunk,
            run_key=ctx.effect_key,
        )  # fmt: skip
        if res.unverifiable or res.errors:
            log.warning("audit recovery needs review", extra=report(res))
        log.info(
            "audit recovery chunk",
            extra={
                "examined": res.examined,
                "applied": res.applied,
                "not_applied": res.not_applied,
            },
        )
        if res.next_cursor:
            return Continue(cursor=res.next_cursor, items=res.examined)
        return Done(items=res.examined)

    return handler


_bound: dict[str, Any] = {}


def bind(repo: Repository, service: AuditService, *, lookback_hours: int, grace_s: int) -> None:
    _bound["handler"] = make_recover_handler(
        repo, service, lookback=timedelta(hours=lookback_hours), grace=timedelta(seconds=grace_s)
    )


def audit_recover(ctx: JobContext) -> Done | Continue:
    handler = _bound.get("handler")
    if handler is None:
        raise RuntimeError("audit-recover ran before the audit service was bound")
    return handler(ctx)
