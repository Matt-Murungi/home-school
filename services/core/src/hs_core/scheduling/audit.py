"""Audit interface for admin scheduling actions.

Placeholder for M06 #41, which supplies the real sink writing audit_log.

Until M06 lands the default sink logs ids and the reason (reason text is operator-written, never
user data). The real sink must append to `audit_log` through the append-only wrapper; this module
only fixes the shape of the call.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Protocol

log = logging.getLogger("hs_core.scheduling.audit")


@dataclass(frozen=True)
class AuditEntry:
    actor_id: str | None  # None = the system (nightly job)
    action: str  # e.g. "scheduling.generate_now"
    entity: str
    entity_id: str
    reason: str
    detail: dict[str, Any] = field(default_factory=dict)  # counts and ids only


class AuditSink(Protocol):
    def record(self, entry: AuditEntry) -> None: ...


class FakeAuditSink:
    def __init__(self) -> None:
        self.entries: list[AuditEntry] = []

    def record(self, entry: AuditEntry) -> None:
        self.entries.append(entry)


class LogAuditSink:
    def record(self, entry: AuditEntry) -> None:
        log.info(
            "audit",
            extra={
                "actor_id": entry.actor_id,
                "action": entry.action,
                "entity": entry.entity,
                "entity_id": entry.entity_id,
                "reason": entry.reason,
                **entry.detail,
            },
        )
