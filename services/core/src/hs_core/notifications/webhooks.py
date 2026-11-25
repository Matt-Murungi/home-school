"""Email provider webhook processing: verified, replay-safe, idempotent, crash-tolerant.

Flow for one delivery:
1. `sender.parse_webhook` verifies the signature and timestamp, then maps events (raises
   `WebhookRejected`: 401 signature/timestamp, 400 malformed, 413 too large).
2. The delivery id (`svix-id`, reused by the provider on retries) is CLAIMED by creating a
   `webhook_event` row with a derived id (atomic: Conflict = seen before). A row already `done`
   means a duplicate or replay: acknowledged, nothing applied. A row still `processing` means an
   earlier attempt died part-way: the effects are applied again, which is safe because each one is
   idempotent (suppression add by derived row id; `record_delivery` is order-safe and repeatable).
3. Effects, in this order: suppression for hard bounce, complaint and provider suppression (the
   safety effect comes first), then `record_delivery` on the notification named by the
   `notification_id` tag. An unknown notification (deleted, other environment) is counted, not an
   error. 4. The claim is marked `done`.
A failure anywhere raises (the endpoint answers 5xx and the provider retries). Only ids, event
types and counts are logged; never addresses or bodies.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from appwrite.query import Query

from ..jobs.idempotency import row_id
from ..repository import Conflict, NotFound, Repository
from .channels import EmailEvent, EmailSender
from .service import NotificationService
from .suppression import SuppressionList

log = logging.getLogger("hs_core.notifications.webhooks")
TABLE = "webhook_event"
PROVIDER = "resend"
RETENTION = timedelta(days=7)  # far longer than the 5 minute signature tolerance
_SUPPRESS = {
    "bounced": "hard_bounce",
    "complained": "complaint",
    "suppressed": "provider_suppressed",
}


@dataclass(frozen=True)
class Outcome:
    duplicate: bool
    events: int
    applied: int
    unmatched: int


class WebhookProcessor:
    def __init__(
        self,
        sender: EmailSender,
        notifications: NotificationService,
        suppression: SuppressionList,
        repo: Repository,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.sender, self.notifications, self.suppression = sender, notifications, suppression
        self.repo = repo
        self.clock = clock or (lambda: datetime.now(UTC))

    def handle(self, headers: Mapping[str, str], body: bytes) -> Outcome:
        events = self.sender.parse_webhook(headers, body)  # raises WebhookRejected
        event_id = self.sender.event_id(headers, body)
        rid = row_id(TABLE, f"{PROVIDER}:{event_id}")
        now = self.clock()
        try:
            self.repo.create(
                TABLE,
                rid,
                {
                    "provider": PROVIDER,
                    "event_id": event_id,
                    "event_type": ",".join(sorted({e.kind for e in events}))[:64] or "ignored",
                    "status": "processing",
                    "received_at": now.isoformat(),
                    "processed_at": None,
                    "expires_at": (now + RETENTION).isoformat(),
                },
            )
        except Conflict:
            if self.repo.get(TABLE, rid).get("status") == "done":
                log.info("webhook duplicate ignored", extra={"event": rid})
                return Outcome(True, len(events), 0, 0)
            log.warning("webhook claim found half-processed, re-applying", extra={"event": rid})
        applied = unmatched = 0
        for ev in events:
            if self._apply(ev, event_id):
                applied += 1
            else:
                unmatched += 1
        self.repo.update(TABLE, rid, {"status": "done", "processed_at": self.clock().isoformat()})
        log.info(
            "webhook processed",
            extra={"event": rid, "events": len(events), "applied": applied, "unmatched": unmatched},
        )
        return Outcome(False, len(events), applied, unmatched)

    def _apply(self, ev: EmailEvent, event_id: str) -> bool:
        reason = _SUPPRESS.get(ev.kind)
        if reason and ev.address:
            self.suppression.add(ev.address, reason, event_id)
        if not ev.notification_id:
            return False
        try:
            self.notifications.record_delivery(
                ev.notification_id, ev.kind, ev.occurred_at, ev.reason
            )
        except NotFound:
            return False
        return True


def cleanup_webhook_events(repo: Repository, now: datetime, page: int = 40) -> int:
    """Hard-delete up to `page` expired rows (job `webhook-event-cleanup`); returns how many."""
    rows = repo.list(TABLE, [Query.less_than("expires_at", now.isoformat()), Query.limit(page)])
    deleted = 0
    for r in rows:
        try:
            repo.delete(TABLE, r["$id"])
            deleted += 1
        except NotFound:
            pass
    return deleted
