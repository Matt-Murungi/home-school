"""`notification-dispatch`: every minute, sends queued notifications whose time has come
(deferred by quiet hours, or waiting for a retry). Idempotent and chunked (F04 Continue/Done);
processed rows leave the queue and rows under a live lease are skipped, so overlapping or
repeated runs cannot double-send. Registered in jobs/schedule.py.

Job handlers receive no dependencies from the runner (F04): the app binds the service at startup.
"""

from __future__ import annotations

import logging

from ..jobs.model import Continue, Done, JobContext
from .service import NotificationService

log = logging.getLogger("hs_core.notifications.dispatch")
PAGE = 40  # rows per chunk: each is a few provider calls, well inside the attempt timeout

_bound: dict[str, NotificationService] = {}


def bind(service: NotificationService) -> None:
    _bound["service"] = service


def make_handler(service: NotificationService, page: int = PAGE):
    def handler(ctx: JobContext) -> Done | Continue:
        n = service.dispatch_due(page=page)
        if n >= page:
            return Continue(cursor=None, items=n)
        if n:
            log.info("notification dispatch done", extra={"processed": n})
        return Done(items=n)

    return handler


def notification_dispatch(ctx: JobContext) -> Done | Continue:
    service = _bound.get("service")
    if service is None:
        raise RuntimeError("notification-dispatch ran before the service was bound")
    return make_handler(service)(ctx)
