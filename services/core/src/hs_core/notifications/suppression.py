"""Email suppression list (N02, ADR 0005): addresses we never email again.

Decision (owner question, default recorded in memory.md): suppression applies to ALL email,
safeguarding included. Safeguarding mail already goes out on push and in-app at the same time
(N01); a suppressed address makes the email row fail permanently, which for a critical event
raises the `notification_delivery_failed` alert so an admin follows up by phone.

Rows hold only a hash of the lower-cased address (see appwrite/schema/email.json note). The row id
is derived from the hash, so adding twice is idempotent (Conflict = already listed). A support
`release` sets `revoked_at`; a new hard bounce or complaint clears it again.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable
from datetime import UTC, datetime

from ..jobs.idempotency import row_id
from ..repository import Conflict, NotFound, Repository
from .address import normalize_address

log = logging.getLogger("hs_core.notifications.suppression")
TABLE = "email_suppression"
REASONS = ("hard_bounce", "complaint", "provider_suppressed", "manual")


def address_hash(address: str) -> str | None:
    norm = normalize_address(address)
    if norm is None:
        return None
    return hashlib.sha256(("hs-email-suppression:" + norm).encode()).hexdigest()


class SuppressionList:
    def __init__(self, repo: Repository, clock: Callable[[], datetime] | None = None) -> None:
        self.repo = repo
        self.clock = clock or (lambda: datetime.now(UTC))

    def is_suppressed(self, address: str) -> bool:
        h = address_hash(address)
        if h is None:
            return False  # not a valid address: the validator refuses it anyway
        try:
            row = self.repo.get(TABLE, row_id(TABLE, h))
        except NotFound:
            return False
        return not row.get("revoked_at")

    def add(self, address: str, reason: str, source_event_id: str | None = None) -> bool:
        """True when newly listed (or re-listed after a release), False when already listed."""
        if reason not in REASONS:
            raise ValueError("unknown suppression reason")
        h = address_hash(address)
        if h is None:
            return False
        rid = row_id(TABLE, h)
        now = self.clock().isoformat()
        try:
            self.repo.create(
                TABLE,
                rid,
                {
                    "address_hash": h,
                    "reason": reason,
                    "source_event_id": (source_event_id or "")[:128] or None,
                    "created_at": now,
                    "revoked_at": None,
                },
            )
            log.info("address suppressed", extra={"reason": reason})
            return True
        except Conflict:
            row = self.repo.get(TABLE, rid)
            if row.get("revoked_at"):
                self.repo.update(TABLE, rid, {"revoked_at": None, "reason": reason})
                log.info("address re-suppressed", extra={"reason": reason})
                return True
            return False

    def release(self, address: str) -> bool:
        """Support action (a typo was fixed, the owner approved). Marks the row revoked."""
        h = address_hash(address)
        if h is None:
            return False
        try:
            row = self.repo.get(TABLE, row_id(TABLE, h))
        except NotFound:
            return False
        if row.get("revoked_at"):
            return False
        self.repo.update(TABLE, row["$id"], {"revoked_at": self.clock().isoformat()})
        return True
