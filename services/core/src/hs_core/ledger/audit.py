"""Audit hook for M06 (#41, SEC-6). The ledger calls `record` after every post (also on a replay),
so an audit entry cannot be lost between "sealed" and "audited". `event.key` is deterministic
(`ledger:<transaction id>`): the real sink must treat a repeated key as success (create the
audit_log row with that id, 409 = done). The detail carries ids and counts only, never names."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class AuditEvent:
    key: str
    action: str
    entity_type: str
    entity_id: str
    actor_id: str | None
    detail: dict[str, Any] = field(default_factory=dict)


class AuditSink(Protocol):
    def record(self, event: AuditEvent) -> None: ...


class NullAuditSink:
    def record(self, event: AuditEvent) -> None:  # pragma: no cover - trivial
        return None
