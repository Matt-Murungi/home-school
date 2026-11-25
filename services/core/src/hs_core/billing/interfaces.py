"""Small interfaces billing depends on. The real implementations are other tickets ($01 ledger,
M06 audit, N01/N03 notifications, S01 scheduler); fakes live in `fakes.py`."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

from .models import PlanInfo, Subscription
from .periods import Period


@dataclass(frozen=True)
class DomainEvent:
    """What a transition implies. `event_id` is deterministic so consumers can de-duplicate."""

    event_id: str
    name: str
    subscription_id: str
    version: int
    at: datetime
    data: dict[str, Any] = field(default_factory=dict)


class BillingLedger(Protocol):
    """$01. Every method is idempotent by its `idempotency_key`."""

    def record_payment(
        self, *, invoice_id: str, payment_ref: str, amount_ugx: int, idempotency_key: str
    ) -> None: ...

    def record_credit_applied(
        self, *, subscription_id: str, credit_id: str, amount_ugx: int, idempotency_key: str
    ) -> None: ...

    def flag_refund_due(
        self,
        *,
        invoice_id: str,
        payment_ref: str,
        amount_ugx: int,
        reason: str,
        idempotency_key: str,
    ) -> None:
        """A confirmed payment that must not be applied (late after cancellation, duplicate,
        wrong amount): put it on the admin refund/credit queue (PAY-7)."""
        ...


class Notifier(Protocol):
    """N01/N03: receives domain events after the change is committed."""

    def notify(self, event: DomainEvent) -> None: ...


class SessionScheduler(Protocol):
    """S01 hooks.

    Contract for the S01 adapter:
    * Every call carries a deterministic `event_id` (`<subscription>:<to_version>:<index>`, or
      `reconcile:<subscription>:<date>`): the adapter MUST be idempotent on it, because billing
      re-sends events until a transition is marked delivered.
    * S01's own calls are keyed by ASSIGNMENT (`cancel_future(assignment_id, from_date, reason)`,
      `regenerate(assignment_id, from_date)`). Billing speaks in subscriptions; the adapter maps
      subscription -> assignment through `subscription.assignment_id` (kept current by
      `BillingService.on_teacher_assigned`) and converts `from_at` to the Kampala date. A
      subscription without an assignment (never matched) has nothing to cancel or regenerate:
      the adapter returns without error.
    * `future_sessions` counts the scheduled (not started, not cancelled) sessions of the
      subscription's assignment from `from_at`; the reconciler cancels them for cancelled and
      paused subscriptions."""

    def cancel_future(
        self, *, subscription_id: str, from_at: datetime, reason: str, event_id: str
    ) -> None: ...

    def regenerate(
        self,
        *,
        subscription_id: str,
        period_start_at: datetime,
        period_end_at: datetime,
        event_id: str,
    ) -> None: ...

    def future_sessions(self, *, subscription_id: str, from_at: datetime) -> int: ...


class PlanCatalog(Protocol):
    def get(self, plan_id: str) -> PlanInfo | None: ...


class DiscountPolicy(Protocol):
    """Hook for promotions/discounts: integer UGX off this period's price (0 = none)."""

    def discount_for(self, sub: Subscription, plan: PlanInfo, period: Period) -> int: ...
