"""F04 entry point for `LedgerService.recover` (finding: abandoned group = a payment the ledger
does not know about). `make_recover_handler(get_service, rebuild)` returns a job handler; it is NOT
yet in `jobs/schedule.py` because the app has no ledger wiring (the ticket that wires the service
adds `JobSpec("ledger-recover", handler, schedule=Schedule(timedelta(hours=1)), ...)`).
Idempotent (recovery re-posts by key), one pass per run, logs a WARNING and counts abandoned groups
(`abandoned` extra; alert rule on it belongs in ops/alerts.json, owner of F08/R04)."""

from __future__ import annotations

import logging
from collections.abc import Callable

from ..jobs.model import Done, JobContext
from .model import OrphanGroup, TransactionRequest
from .service import LedgerService

log = logging.getLogger("hs_core.ledger.recover")


def make_recover_handler(
    get_service: Callable[[], LedgerService],
    rebuild: Callable[[OrphanGroup], TransactionRequest | None] | None = None,
) -> Callable[[JobContext], Done]:
    def ledger_recover(ctx: JobContext) -> Done:
        report = get_service().recover(rebuild=rebuild)
        if report.abandoned:
            log.warning(
                "ledger.recover.abandoned",
                extra={"abandoned": len(report.abandoned), "run_id": ctx.run_id},
            )
        log.info(
            "ledger.recover",
            extra={
                "completed": len(report.completed),
                "abandoned": len(report.abandoned),
                "run_id": ctx.run_id,
            },
        )
        return Done(items=len(report.completed) + len(report.abandoned))

    return ledger_recover
