"""Job handlers. Business jobs arrive with later tickets; `heartbeat` is the sample job."""

from __future__ import annotations

import logging

from .model import Done, JobContext

log = logging.getLogger("hs_core.jobs.heartbeat")


def heartbeat(ctx: JobContext) -> Done:
    """Writes one log line. Proves on staging that the cron -> tick -> run chain works."""
    log.info("heartbeat", extra={"period": ctx.scheduled_for, "run_id": ctx.run_id})
    return Done(items=1)
