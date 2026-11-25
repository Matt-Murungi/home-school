"""Audit adapters for hooks other tickets defined before M06 existed (#41 reviewer round 1).

Both are duck-typed so this module imports neither the ledger nor the notification package (they
are separate tickets). They turn the hook's event into one `audit_log` row under the hook's own
DETERMINISTIC key, bound to the content: replaying the same event is a no-op, the same key with
different content is a recorded collision (never a silent dedupe).

Wiring (the owner of each module does it when it merges, see docs/ops/audit.md "Hooks"):
    LedgerService(..., audit=LedgerAuditAdapter(app.state.audit))
    TemplateStore(..., audit=TemplateAuditAdapter(app.state.audit))
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from . import payload
from .service import AuditService


class LedgerAuditAdapter:
    """`hs_core.ledger.audit.AuditSink`: `record(event)` with event.key (`ledger:<tx id>`),
    action, entity_type, entity_id, actor_id, detail (ids, counts and amounts only)."""

    def __init__(self, service: AuditService) -> None:
        self._svc = service

    def record(self, event: Any) -> None:
        detail = dict(event.detail or {})
        content = payload.stable_hash(
            {"action": event.action, "entity": event.entity_type, "id": event.entity_id,
             "actor": event.actor_id, "detail": payload.redact_state(event.entity_type, detail)}
        )  # fmt: skip
        self._svc.record_external(
            str(event.key), content_hash=content, action=event.action,
            entity=event.entity_type, entity_id=str(event.entity_id), actor_id=event.actor_id,
            roles=("ledger",), job="ledger" if event.actor_id is None else None, after=detail,
        )  # fmt: skip


class TemplateAuditAdapter:
    """`TemplateStore.audit`: called with the publish payload dict (action, actor_id, key,
    channel, locale, version); ids only, never copy text."""

    REQUIRED = ("action", "key", "channel", "locale", "version")

    def __init__(self, service: AuditService) -> None:
        self._svc = service

    def __call__(self, event: Mapping[str, Any]) -> None:
        if any(k not in event for k in self.REQUIRED):
            raise ValueError("not a template audit event")
        ident = f"{event['key']}:{event['channel']}:{event['locale']}:{event['version']}"
        content = payload.stable_hash(dict(event))
        self._svc.record_external(
            f"template:{ident}", content_hash=content, action=str(event["action"]),
            entity="notification_template", entity_id=ident,
            actor_id=event.get("actor_id"), roles=("superadmin",), job="notifications",
            after={k: v for k, v in event.items() if k not in ("action", "actor_id")},
        )  # fmt: skip
