"""Row models. Dataclasses map to/from repository rows (ISO UTC strings for instants)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

from .errors import InvalidMoney
from .states import Status


def iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        raise ValueError("timezone-aware datetime required")
    return dt.astimezone(UTC).isoformat()


def parse(s: str | None) -> datetime | None:
    if s is None:
        return None
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(UTC)


def ugx(value: Any, *, name: str = "amount") -> int:
    """Money is a non-negative int number of UGX: floats, bools and strings are refused."""
    if type(value) is not int or value < 0:
        raise InvalidMoney(f"{name} must be a non-negative integer of UGX")
    return value


@dataclass(frozen=True)
class PlanInfo:
    id: str
    price_ugx: int
    sessions_per_week: int
    active: bool = True
    currency: str = "UGX"

    def __post_init__(self) -> None:
        ugx(self.price_ugx, name="price_ugx")


@dataclass
class Subscription:
    id: str
    child_id: str
    parent_id: str
    plan_id: str
    status: Status
    version: int
    anchor_date: date  # Kampala date the first period started; day-of-month anchors renewals
    period_index: int
    period_start_at: datetime
    period_end_at: datetime
    created_at: datetime
    cancel_at_period_end: bool = False
    cancel_reason: str | None = None
    pending_plan_id: str | None = None
    first_payment_at: datetime | None = None
    paused_at: datetime | None = None
    past_due_at: datetime | None = None
    cancelled_at: datetime | None = None
    last_payment_ref: str | None = None
    assignment_id: str | None = None
    is_trial: bool = False

    def to_row(self) -> dict[str, Any]:
        return {
            "child_id": self.child_id,
            "parent_id": self.parent_id,
            "plan_id": self.plan_id,
            "status": self.status.value,
            "version": self.version,
            "anchor_date": self.anchor_date.isoformat(),
            "period_index": self.period_index,
            "period_start_at": iso(self.period_start_at),
            "period_end_at": iso(self.period_end_at),
            "created_at": iso(self.created_at),
            "cancel_at_period_end": self.cancel_at_period_end,
            "cancel_reason": self.cancel_reason,
            "pending_plan_id": self.pending_plan_id,
            "first_payment_at": iso(self.first_payment_at),
            "paused_at": iso(self.paused_at),
            "past_due_at": iso(self.past_due_at),
            "cancelled_at": iso(self.cancelled_at),
            "last_payment_ref": self.last_payment_ref,
            "assignment_id": self.assignment_id,
            "is_trial": self.is_trial,
        }

    @classmethod
    def from_row(cls, r: dict[str, Any]) -> Subscription:
        return cls(
            id=r["$id"],
            child_id=r["child_id"],
            parent_id=r["parent_id"],
            plan_id=r["plan_id"],
            status=Status(r["status"]),
            version=int(r.get("version", 0)),
            anchor_date=date.fromisoformat(r["anchor_date"]),
            period_index=int(r.get("period_index", 0)),
            period_start_at=parse(r["period_start_at"]),
            period_end_at=parse(r["period_end_at"]),
            created_at=parse(r["created_at"]),
            cancel_at_period_end=bool(r.get("cancel_at_period_end", False)),
            cancel_reason=r.get("cancel_reason"),
            pending_plan_id=r.get("pending_plan_id"),
            first_payment_at=parse(r.get("first_payment_at")),
            paused_at=parse(r.get("paused_at")),
            past_due_at=parse(r.get("past_due_at")),
            cancelled_at=parse(r.get("cancelled_at")),
            last_payment_ref=r.get("last_payment_ref"),
            assignment_id=r.get("assignment_id"),
            is_trial=bool(r.get("is_trial", False)),
        )


@dataclass
class Invoice:
    id: str
    subscription_id: str
    parent_id: str
    plan_id: str
    period_index: int
    period_start_date: str  # Kampala calendar date, YYYY-MM-DD
    period_end_date: str  # inclusive
    plan_price_ugx: int
    discount_ugx: int
    credit_ugx: int
    amount: int  # net: plan price - discount - credits, integer UGX
    due_at: datetime
    created_at: datetime
    status: str = "open"  # open | paid | past_due | void | refunded (M04 enum)
    currency: str = "UGX"
    credit_ids: list[str] = field(default_factory=list)
    paid_at: datetime | None = None
    payment_ref: str | None = None

    def to_row(self) -> dict[str, Any]:
        return {
            "subscription_id": self.subscription_id,
            "parent_id": self.parent_id,
            "plan_id": self.plan_id,
            "period_index": self.period_index,
            "period_start_date": self.period_start_date,
            "period_end_date": self.period_end_date,
            "plan_price_ugx": self.plan_price_ugx,
            "discount_ugx": self.discount_ugx,
            "credit_ugx": self.credit_ugx,
            "amount": self.amount,
            "currency": self.currency,
            "status": self.status,
            "due_at": iso(self.due_at),
            "paid_at": iso(self.paid_at),
            "created_at": iso(self.created_at),
            "credit_ids": json.dumps(self.credit_ids),
            "payment_ref": self.payment_ref,
        }

    @classmethod
    def from_row(cls, r: dict[str, Any]) -> Invoice:
        return cls(
            id=r["$id"],
            subscription_id=r["subscription_id"],
            parent_id=r["parent_id"],
            plan_id=r["plan_id"],
            period_index=int(r["period_index"]),
            period_start_date=r["period_start_date"],
            period_end_date=r["period_end_date"],
            plan_price_ugx=r["plan_price_ugx"],
            discount_ugx=r["discount_ugx"],
            credit_ugx=r["credit_ugx"],
            amount=r["amount"],
            currency=r.get("currency") or "UGX",
            status=r["status"],
            due_at=parse(r["due_at"]),
            paid_at=parse(r.get("paid_at")),
            created_at=parse(r["created_at"]),
            credit_ids=json.loads(r.get("credit_ids") or "[]"),
            payment_ref=r.get("payment_ref"),
        )


@dataclass
class Credit:
    """Sessions owed to a subscription after platform-caused misses (SUB-5). One row per
    (kind, ref): re-reporting the same miss never creates a second credit."""

    id: str
    subscription_id: str
    kind: str  # platform_miss | late_payment_start
    ref: str  # session id or invoice id
    sessions: int
    reason: str
    created_at: datetime
    applied_invoice_id: str | None = None

    def to_row(self) -> dict[str, Any]:
        return {
            "subscription_id": self.subscription_id,
            "kind": self.kind,
            "ref": self.ref,
            "sessions": self.sessions,
            "reason": self.reason,
            "created_at": iso(self.created_at),
            "applied_invoice_id": self.applied_invoice_id,
        }

    @classmethod
    def from_row(cls, r: dict[str, Any]) -> Credit:
        return cls(
            id=r["$id"],
            subscription_id=r["subscription_id"],
            kind=r["kind"],
            ref=r["ref"],
            sessions=int(r["sessions"]),
            reason=r["reason"],
            created_at=parse(r["created_at"]),
            applied_invoice_id=r.get("applied_invoice_id"),
        )
