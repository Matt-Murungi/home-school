"""Retention hook for C03 (retention job, later): notification payloads hold template variables
(names, amounts, times), so once a row is older than the retention boundary its variables are
removed and the row is soft-deleted (`deleted_at`). The row itself (status, channel, template key
and version, timestamps, attempts, `meta` such as locale and provider id) is kept: it is the
delivery record. `meta.payload_expired` marks the payload as expired; the inbox must skip such
rows (`service.is_renderable`).

Paging: ALL rows older than the boundary are walked by cursor (`cursorAfter`, at most 100 per
list call, never offsets, Appwrite caps `total` and offsets at 5,000), so no row is missed however
many there are. A call changes at most `page` rows; call it again (or loop until it returns 0).
Rows still waiting to be sent (`queued`) are never touched: they are not finished yet.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime

from appwrite.query import Query

from ..repository import NotFound, Repository
from .errors import NaiveDatetime

TABLE = "notification"
LIST_PAGE = 100  # Appwrite list page size used here (never more)


@dataclass(frozen=True)
class ExpireResult:
    changed: int
    next_cursor: str | None  # resume point when `done` is False; pass back as `start_after`
    done: bool  # True: every row older than the boundary has been looked at


def _needs_expiry(row: dict) -> bool:
    if row.get("status") == "queued":
        return False
    if not row.get("deleted_at"):
        return True
    try:
        meta = (json.loads(row["payload"]) or {}).get("meta", {}) if row.get("payload") else {}
    except ValueError:
        return True
    return bool(row.get("payload")) and not meta.get("payload_expired")


def _expired_payload(row: dict) -> str:
    """Keep the metadata (locale, group, provider id ...), drop every variable."""
    try:
        meta = (json.loads(row["payload"]) or {}).get("meta", {}) if row.get("payload") else {}
    except ValueError:
        meta = {}
    meta = {k: v for k, v in meta.items() if k not in ("subject",)}
    meta["payload_expired"] = True
    return json.dumps({"vars": {}, "meta": meta})


def expire_chunk(
    repo: Repository,
    before: datetime,
    now: datetime | None = None,
    page: int = 40,
    start_after: str | None = None,
) -> ExpireResult:
    """Walk rows created strictly before `before` (a row created exactly at `before` is kept),
    oldest first, changing up to `page` of them. Returns where to resume."""
    if before.utcoffset() is None:
        raise NaiveDatetime("retention boundary must be timezone-aware")
    now = now or datetime.now(UTC)
    boundary = before.astimezone(UTC).isoformat()
    changed, cursor = 0, start_after
    while True:
        queries = [
            Query.less_than("created_at", boundary),
            Query.order_asc("created_at"),
            Query.limit(LIST_PAGE),
        ]
        if cursor:
            queries.append(Query.cursor_after(cursor))
        rows = repo.list(TABLE, queries)
        for r in rows:
            if changed >= page:
                return ExpireResult(changed, cursor, False)
            if _needs_expiry(r):
                try:
                    repo.update(
                        TABLE,
                        r["$id"],
                        {
                            "payload": _expired_payload(r),
                            "deleted_at": r.get("deleted_at") or now.astimezone(UTC).isoformat(),
                        },
                    )
                    changed += 1
                except NotFound:
                    pass
            cursor = r["$id"]
        if len(rows) < LIST_PAGE:
            return ExpireResult(changed, None, True)


def expire_payloads(
    repo: Repository, before: datetime, now: datetime | None = None, page: int = 40
) -> int:
    """Expire up to `page` rows older than `before`; returns how many were changed. Idempotent:
    finished rows are skipped, so repeated calls drain the backlog and then return 0."""
    return expire_chunk(repo, before, now, page).changed
