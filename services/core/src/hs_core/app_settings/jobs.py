"""`settings-activate` (every minute): activates scheduled changes whose time has come and repairs
value rows that fell behind their history after a crash. Idempotent (it recomputes from the
history). Registered in jobs/schedule.py; the app binds the service once at startup."""

from __future__ import annotations

import logging

from ..jobs.model import Continue, Done, JobContext
from .service import AppSettings

log = logging.getLogger("hs_core.app_settings.jobs")

_bound: dict[str, AppSettings] = {}


def bind(service: AppSettings) -> None:
    _bound["service"] = service


def make_handler(service: AppSettings):
    def handler(ctx: JobContext) -> Done | Continue:
        # writes inherit the job's audit context (actor `system`, job name) from the runner
        out = service.reconcile()
        n = out["activated"] + out["repaired"]
        if n:
            log.info("settings reconciled", extra=out)
        return Done(items=n)

    return handler


def settings_activate(ctx: JobContext) -> Done | Continue:
    service = _bound.get("service")
    if service is None:
        raise RuntimeError("settings service is not bound")
    return make_handler(service)(ctx)
