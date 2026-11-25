"""Read side of the audit log, for the admin viewer (H08 builds the UI later).

Visibility (ADR 0012, SEC-6): opsadmin and superadmin always; safeguardinglead only while
`AUDIT_LEAD_BLANKET_READ` is on (owner question 12; default on); nobody else. Cursor pagination
only: a page asks the store for `limit + 1` rows, never trusts a `total` and never gets near the
5,000 list cap.
"""

from __future__ import annotations

import base64
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from appwrite.query import Query

from ..repository import Repository
from .errors import AuditForbidden, AuditQueryError
from .service import TABLE, iso, meta_of

MAX_LIMIT = 100
_CURSOR = re.compile(r"[A-Za-z0-9_=-]{1,128}")


@dataclass
class AuditPage:
    items: list[dict[str, Any]] = field(default_factory=list)
    next_cursor: str | None = None


def can_read_audit(roles: Sequence[str], *, lead_blanket_read: bool) -> bool:
    allowed = {"opsadmin", "superadmin"} | ({"safeguardinglead"} if lead_blanket_read else set())
    return bool(set(roles) & allowed)


def _encode(row_id: str) -> str:
    return base64.urlsafe_b64encode(f"v1:{row_id}".encode()).decode()


def _decode(cursor: str) -> str:
    try:
        if not _CURSOR.fullmatch(cursor):
            raise ValueError
        text = base64.urlsafe_b64decode(cursor.encode()).decode()
        version, _, row_id = text.partition(":")
        if version != "v1" or not row_id:
            raise ValueError
        return row_id
    except (ValueError, UnicodeDecodeError):
        raise AuditQueryError("invalid cursor") from None


def _load(text: str | None) -> dict[str, Any] | None:
    if not text:
        return None
    try:
        return json.loads(text)
    except ValueError:
        return {"unreadable": True}


def parse_row(row: Mapping[str, Any]) -> dict[str, Any]:
    m = meta_of(row)
    return {
        "id": row["$id"],
        "occurred_at": row.get("occurred_at"),
        "actor_id": row.get("actor_id"),
        "actor_roles": row.get("actor_roles"),
        "action": row.get("action"),
        "entity": row.get("entity"),
        "entity_id": row.get("entity_id"),
        "reason": row.get("reason"),
        "ip": row.get("ip"),
        "request_id": row.get("request_id"),
        "status": m.get("status"),
        "source": m.get("source"),
        "job": m.get("job"),
        "seq": m.get("seq"),
        "ref": m.get("ref"),
        "error": m.get("error"),
        "before": _load(row.get("before")),
        "after": _load(row.get("after")),
    }


def list_audit(
    repo: Repository,
    *,
    roles: Sequence[str],
    lead_blanket_read: bool = True,
    entity: str | None = None,
    entity_id: str | None = None,
    actor_id: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    cursor: str | None = None,
    limit: int = 50,
) -> AuditPage:
    if not can_read_audit(roles, lead_blanket_read=lead_blanket_read):
        raise AuditForbidden
    if not 1 <= limit <= MAX_LIMIT:
        raise AuditQueryError(f"limit must be between 1 and {MAX_LIMIT}")
    if entity_id and not entity:
        raise AuditQueryError("entity_id needs entity")
    if actor_id and entity:
        raise AuditQueryError("filter by entity or by actor, not both (no index for the pair)")
    q: list[str] = []
    if entity:
        q.append(Query.equal("entity", [entity]))
    if entity_id:
        q.append(Query.equal("entity_id", [entity_id]))
    if actor_id:
        q.append(Query.equal("actor_id", [actor_id]))
    if since:
        q.append(Query.greater_than_equal("occurred_at", iso(since)))
    if until:
        q.append(Query.less_than_equal("occurred_at", iso(until)))
    q.append(Query.order_desc("occurred_at"))
    if cursor:
        q.append(Query.cursor_after(_decode(cursor)))
    q.append(Query.limit(limit + 1))
    rows = repo.list(TABLE, q)
    page = rows[:limit]
    more = len(rows) > limit
    return AuditPage([parse_row(r) for r in page], _encode(page[-1]["$id"]) if more else None)
