"""`POST /v1/admin/audit/event`: how the admin app (a Next.js server with NO Appwrite API key)
writes audit rows for admin-UI events that are not table writes (H09 search, #138/#41).

Hard limits, all enforced by the request model and tested:
- the caller is a verified Appwrite JWT with an admin role; actor, roles, IP and request id come
  from the server (the body cannot carry them: unknown fields are refused);
- only whitelisted events (`ALLOWED_EVENTS`); the body holds entity TYPES, a query KIND and
  per-type COUNTS, never query text, result rows or ids of people;
- fails closed: if the audit row cannot be written the answer is 503 and the admin app must not
  return the search results.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field

from ..auth import require_roles
from ..errors import AppError
from .api import audit_context
from .context import AuditContext
from .errors import AuditUnavailable

router = APIRouter(prefix="/v1/admin/audit", tags=["audit"])

MAX_BODY_BYTES = 4096
ADMIN_ROLES = ("opsadmin", "curator", "safeguardinglead", "superadmin")
# Mirror of apps/admin/src/lib/search/policy.ts ENTITY_TYPES / types.ts QueryKind.
ENTITY_TYPES = ("parent", "teacher", "child", "session", "payment", "topic", "question", "case")
QUERY_KINDS = ("text", "phone", "date", "reference")
EntityType = Literal[
    "parent", "teacher", "child", "session", "payment", "topic", "question", "case"
]
QueryKind = Literal["text", "phone", "date", "reference"]
# New admin-UI events are added here (and get their own model); anything else is refused.
ALLOWED_EVENTS = frozenset({"admin.search"})


class SearchEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    event: Literal["admin.search"]
    entity_types: list[EntityType] = Field(min_length=1, max_length=len(ENTITY_TYPES))
    query_kind: QueryKind
    results: dict[EntityType, int] = Field(default_factory=dict, max_length=len(ENTITY_TYPES))

    def model_post_init(self, _ctx) -> None:
        if len(set(self.entity_types)) != len(self.entity_types):
            raise ValueError("duplicate entity type")
        if any(not 0 <= n <= 10_000 for n in self.results.values()):
            raise ValueError("count out of range")


@router.post("/event")
def record_event(
    body: SearchEvent,
    request: Request,
    ctx: AuditContext = Depends(audit_context),  # noqa: B008
    _admin=Depends(require_roles(*ADMIN_ROLES)),  # noqa: B008
) -> dict:
    declared = request.headers.get("content-length", "0")
    if declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        raise AppError(413, "payload_too_large", "Request body too large")
    types = sorted(body.entity_types)
    try:
        request.app.state.audit.record(
            ctx,
            action=body.event,
            entity="admin_search",
            entity_id=ctx.actor_id or "-",
            after={
                "entity_types": types,
                "query_kind": body.query_kind,
                "results": {t: body.results[t] for t in sorted(body.results)},
            },
            op="read",
        )
    except AuditUnavailable:
        raise AppError(503, "audit_unavailable", "Audit log unavailable") from None
    return {"recorded": True}
