"""`application-claims-cleanup`: hourly, idempotent hard-delete of expired rate-limit and revision
claim rows (submit claims expire in ten years and stay: they are the exactly-once record)."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime

from appwrite.query import Query

from ..jobs.model import Continue, Done, JobContext
from ..repository import NotFound, Repository
from .service import CLAIM, iso

log = logging.getLogger("hs_core.teacher_application.cleanup")
PAGE = 40
_bound: dict[str, Repository] = {}


def bind(repo: Repository) -> None:
    _bound["repo"] = repo


def cleanup_claims(repo: Repository, now: datetime, page: int = PAGE) -> int:
    rows = repo.list(CLAIM, [Query.less_than("expires_at", iso(now)), Query.limit(page)])
    deleted = 0
    for r in rows:
        try:
            repo.delete(CLAIM, r["$id"])
            deleted += 1
        except NotFound:
            pass
    return deleted


def make_cleanup_handler(
    repo: Repository, clock: Callable[[], datetime] | None = None, page: int = PAGE
):
    now_fn = clock or (lambda: datetime.now(UTC))

    def handler(ctx: JobContext) -> Done | Continue:
        n = cleanup_claims(repo, now_fn(), page)
        if n >= page:
            return Continue(cursor=None, items=n)
        return Done(items=n)

    return handler


def application_claims_cleanup(ctx: JobContext) -> Done | Continue:
    repo = _bound.get("repo")
    if repo is None:
        raise RuntimeError("application-claims-cleanup ran before the repository was bound")
    return make_cleanup_handler(repo)(ctx)
