"""Fakes for the billing interfaces (tests and dev)."""

from __future__ import annotations

import threading
from datetime import datetime

from ..repository import Repository
from .interfaces import AuditEntry, DomainEvent
from .models import PlanInfo, Subscription
from .periods import Period


class FakeLedger:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.payments: dict[str, dict] = {}
        self.credits: dict[str, dict] = {}
        self.refunds_due: dict[str, dict] = {}
        self.fail = False

    def _put(self, bucket: dict, key: str, row: dict) -> None:
        if self.fail:
            raise RuntimeError("ledger down")
        with self._lock:
            bucket.setdefault(key, row)

    def record_payment(self, *, invoice_id, payment_ref, amount_ugx, idempotency_key):
        self._put(
            self.payments,
            idempotency_key,
            {"invoice_id": invoice_id, "payment_ref": payment_ref, "amount_ugx": amount_ugx},
        )

    def record_credit_applied(self, *, subscription_id, credit_id, amount_ugx, idempotency_key):
        self._put(
            self.credits,
            idempotency_key,
            {"subscription_id": subscription_id, "credit_id": credit_id, "amount_ugx": amount_ugx},
        )

    def flag_refund_due(self, *, invoice_id, payment_ref, amount_ugx, reason, idempotency_key):
        self._put(
            self.refunds_due,
            idempotency_key,
            {
                "invoice_id": invoice_id,
                "payment_ref": payment_ref,
                "amount_ugx": amount_ugx,
                "reason": reason,
            },
        )


class FakeAuditSink:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.entries: dict[str, AuditEntry] = {}
        self.fail = False

    def record(self, entry: AuditEntry) -> None:
        if self.fail:
            raise RuntimeError("audit down")
        with self._lock:
            self.entries.setdefault(entry.idempotency_key, entry)

    def actions(self) -> list[str]:
        return [e.action for e in self.entries.values()]


class FakeNotifier:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.events: dict[str, DomainEvent] = {}
        self.fail = False

    def notify(self, event: DomainEvent) -> None:
        if self.fail:
            raise RuntimeError("notifier down")
        with self._lock:
            self.events.setdefault(event.event_id, event)

    def names(self) -> list[str]:
        return [e.name for e in self.events.values()]


class FakeScheduler:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.cancelled: dict[str, dict] = {}
        self.regenerated: dict[str, dict] = {}
        self.sessions: dict[str, int] = {}  # subscription id -> future sessions (tests set it)
        self.fail = False

    def cancel_future(self, *, subscription_id, from_at: datetime, reason, event_id):
        if self.fail:
            raise RuntimeError("scheduler down")
        with self._lock:
            self.cancelled.setdefault(
                event_id, {"subscription_id": subscription_id, "from_at": from_at, "reason": reason}
            )
            self.sessions[subscription_id] = 0

    def regenerate(self, *, subscription_id, period_start_at, period_end_at, event_id):
        if self.fail:
            raise RuntimeError("scheduler down")
        with self._lock:
            self.regenerated.setdefault(
                event_id,
                {
                    "subscription_id": subscription_id,
                    "period_start_at": period_start_at,
                    "period_end_at": period_end_at,
                },
            )

    def future_sessions(self, *, subscription_id, from_at) -> int:
        if self.fail:
            raise RuntimeError("scheduler down")
        return self.sessions.get(subscription_id, 0)


class FakePlans:
    def __init__(self, *plans: PlanInfo) -> None:
        self.plans = {p.id: p for p in plans}

    def get(self, plan_id: str) -> PlanInfo | None:
        return self.plans.get(plan_id)


class FixedDiscount:
    def __init__(self, ugx: object = 0) -> None:
        self.ugx = ugx

    def discount_for(self, sub: Subscription, plan: PlanInfo, period: Period):
        return self.ugx


class RepositoryPlans:
    """Reads the M02 `plan` table."""

    def __init__(self, repo: Repository) -> None:
        self._repo = repo

    def get(self, plan_id: str) -> PlanInfo | None:
        from ..repository import NotFound

        try:
            r = self._repo.get("plan", plan_id)
        except NotFound:
            return None
        if r.get("deleted_at"):
            return None
        return PlanInfo(
            id=plan_id,
            price_ugx=r["price_ugx"],
            sessions_per_week=r["sessions_per_week"],
            active=bool(r.get("active", True)),
            currency=r.get("currency") or "UGX",
        )
