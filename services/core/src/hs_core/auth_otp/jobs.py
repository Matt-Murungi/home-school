"""`otp-cleanup`: hourly, idempotent hard-delete of expired challenge, attempt, consume and
rate-limit rows (every row carries `expires_at`). Registered in jobs/schedule.py.

Job handlers receive no dependencies from the runner (F04), so the app binds the repository once
at startup (`bind`); tests build a handler with `make_cleanup_handler`.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from appwrite.query import Query

from ..jobs.model import Continue, Done, JobContext
from ..repository import NotFound, Repository
from .store import TABLE, iso

log = logging.getLogger("hs_core.auth_otp.cleanup")
PAGE = 40  # deletes per chunk: well inside the 10 s attempt timeout
GRACE = timedelta(hours=1)  # keep expired rows briefly so "code expired" is still answerable

_bound: dict[str, Repository] = {}


def bind(repo: Repository) -> None:
    _bound["repo"] = repo


def cleanup_otp(repo: Repository, now: datetime, grace: timedelta = GRACE, page: int = PAGE) -> int:
    """Delete up to `page` rows that expired more than `grace` ago; returns how many."""
    rows = repo.list(TABLE, [Query.less_than("expires_at", iso(now - grace)), Query.limit(page)])
    deleted = 0
    for r in rows:
        try:
            repo.delete(TABLE, r["$id"])
            deleted += 1
        except NotFound:
            pass  # a concurrent cleanup got it first
    return deleted


def make_cleanup_handler(
    repo: Repository,
    clock: Callable[[], datetime] | None = None,
    page: int = PAGE,
    grace: timedelta = GRACE,
):
    now_fn = clock or (lambda: datetime.now(UTC))

    def handler(ctx: JobContext) -> Done | Continue:
        n = cleanup_otp(repo, now_fn(), grace, page)
        if n >= page:
            return Continue(cursor=None, items=n)
        log.info("otp cleanup done", extra={"deleted": n})
        return Done(items=n)

    return handler


def otp_cleanup(ctx: JobContext) -> Done | Continue:
    repo = _bound.get("repo")
    if repo is None:
        raise RuntimeError("otp-cleanup ran before the repository was bound")
    return make_cleanup_handler(repo)(ctx)
