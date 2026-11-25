"""`billing-period-roll`: daily at 03:00 Kampala (jobs/schedule.py).

For every active subscription whose period ends within `BILLING_RENEWAL_LEAD_DAYS`: create the
next period's invoice (idempotent: one per subscription and period start), advance a paid period
at its boundary, and honour "cancel at period end". Chunked with a keyset cursor on the
subscription id (`Continue`), error-isolated per subscription, stops early when the job attempt
times out (`ctx.cancelled`). Re-running the job, or running two copies, changes nothing.

Past-due marking, reminders and pausing sessions for non-payment are B04's, not this job's.
In dev the service is built with fakes (wiring.py); in staging/production an unwired job RAISES
`BillingNotWired` (fails closed) until real ledger, audit, notifier and scheduler adapters exist.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from ..jobs.model import Continue, Done, JobContext
from .service import BillingService

log = logging.getLogger("hs_core.billing.job")


@dataclass
class _Runtime:
    service: BillingService | None = None
    strict: bool = False  # staging/production: an unbound job fails instead of reporting
    missing: tuple[str, ...] = ()


_RT = _Runtime()


class BillingNotWired(RuntimeError):
    """Billing jobs refuse to run without real adapters outside dev."""


def bind(
    service: BillingService | None, *, strict: bool = False, missing: tuple[str, ...] = ()
) -> None:
    _RT.service, _RT.strict, _RT.missing = service, strict, tuple(missing)


def billing_period_roll(ctx: JobContext) -> Done | Continue:
    svc = _service_or_fail()
    if svc is None:
        return Done(detail="billing service not configured")
    cursor = ctx.cursor if isinstance(ctx.cursor, str) and ctx.cursor else None
    cancelled = ctx.cancelled
    res = svc.roll_due(cursor=cursor, stop=(cancelled.is_set if cancelled is not None else None))
    detail = (
        f"processed={res.processed} invoices={res.invoices_created} "
        f"advanced={res.advanced} cancelled={res.cancelled} failed={len(res.failed)}"
    )
    if res.failed:
        log.error(
            "billing-period-roll: %d subscription(s) failed: %s",
            len(res.failed),
            ",".join(res.failed[:20]),
        )
    if res.next_cursor is not None or res.stopped:
        return Continue(res.next_cursor or "", items=res.processed)
    return Done(detail=detail, items=res.processed)


def _service_or_fail() -> BillingService | None:
    svc = _RT.service
    if svc is None:
        if _RT.strict:
            raise BillingNotWired(
                "billing adapters not wired (missing: " + (", ".join(_RT.missing) or "all") + ")"
            )
        return None
    return svc


def billing_redispatch(ctx: JobContext) -> Done | Continue:
    """Every 15 minutes: finish transitions whose effects/events are not delivered (a crash
    after the commit, a failing notifier or scheduler). Idempotent; paged by claim id."""
    svc = _service_or_fail()
    if svc is None:
        return Done(detail="billing service not configured")
    cursor = ctx.cursor if isinstance(ctx.cursor, str) and ctx.cursor else None
    cancelled = ctx.cancelled
    res = svc.redispatch_undelivered(
        cursor=cursor, stop=(cancelled.is_set if cancelled is not None else None)
    )
    if res.failed:
        log.error("billing-redispatch: %d transition(s) still undelivered", len(res.failed))
    if res.next_cursor is not None or res.stopped:
        return Continue(res.next_cursor or "", items=res.processed)
    return Done(
        detail=f"processed={res.processed} delivered={res.delivered} failed={len(res.failed)}",
        items=res.processed,
    )


def billing_reconcile(ctx: JobContext) -> Done | Continue:
    """Daily: cancelled and paused subscriptions must have no future sessions; cancel any left
    (and log at error level: it means an event was lost)."""
    svc = _service_or_fail()
    if svc is None:
        return Done(detail="billing service not configured")
    cursor = ctx.cursor if isinstance(ctx.cursor, str) and ctx.cursor else None
    cancelled = ctx.cancelled
    res = svc.reconcile_future_sessions(
        cursor=cursor, stop=(cancelled.is_set if cancelled is not None else None)
    )
    if res.next_cursor is not None or res.stopped:
        return Continue(res.next_cursor or "", items=res.processed)
    return Done(
        detail=f"processed={res.processed} cancelled_for={len(res.cancelled_sessions_for)} "
        f"failed={len(res.failed)}",
        items=res.processed,
    )
