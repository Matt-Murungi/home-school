"""Retention hook for C03 (retention job, later): notification payloads hold template variables
(names, amounts, times), so they are cleared and the row soft-deleted once past retention."""

from __future__ import annotations

from datetime import UTC, datetime

from appwrite.query import Query

from ..repository import NotFound, Repository

TABLE = "notification"


def expire_payloads(
    repo: Repository, before: datetime, now: datetime | None = None, page: int = 40
) -> int:
    """Clear payload and set deleted_at on up to `page` rows created before `before`.
    Idempotent: rows already soft-deleted are skipped. Returns how many were changed."""
    now = now or datetime.now(UTC)
    rows = repo.list(
        TABLE,
        [
            Query.less_than("created_at", before.astimezone(UTC).isoformat()),
            Query.order_asc("created_at"),
            Query.limit(page * 3),
        ],
    )
    done = 0
    for r in rows:
        if r.get("deleted_at") or done >= page:
            continue
        try:
            repo.update(TABLE, r["$id"], {"payload": None, "deleted_at": now.isoformat()})
            done += 1
        except NotFound:
            pass
    return done
