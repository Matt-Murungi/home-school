"""Provider status/reason mapping to our states."""

from __future__ import annotations

import enum
from dataclasses import dataclass, field


class Status(enum.StrEnum):
    PENDING = "pending"
    SUCCESSFUL = "successful"
    FAILED = "failed"


class FailureReason(enum.StrEnum):
    PAYER_NOT_FOUND = "payer_not_found"
    PAYEE_NOT_FOUND = "payee_not_found"
    NOT_ENOUGH_FUNDS = "not_enough_funds"
    NOT_ALLOWED = "not_allowed"
    NOT_ALLOWED_TARGET_ENVIRONMENT = "not_allowed_target_environment"
    INVALID_CALLBACK_URL_HOST = "invalid_callback_url_host"
    INVALID_CURRENCY = "invalid_currency"
    SERVICE_UNAVAILABLE = "service_unavailable"
    INTERNAL_PROCESSING_ERROR = "internal_processing_error"
    PAYER_LIMIT_REACHED = "payer_limit_reached"
    PAYEE_NOT_ALLOWED_TO_RECEIVE = "payee_not_allowed_to_receive"
    PAYMENT_NOT_APPROVED = "payment_not_approved"
    APPROVAL_REJECTED = "approval_rejected"
    EXPIRED = "expired"
    TRANSACTION_CANCELED = "transaction_canceled"
    RESOURCE_NOT_FOUND = "resource_not_found"
    COULD_NOT_PERFORM_TRANSACTION = "could_not_perform_transaction"
    UNKNOWN = "unknown"


_REASONS = {r.name for r in FailureReason if r is not FailureReason.UNKNOWN}

# Reasons where a new attempt with a NEW reference id may reasonably succeed later.
RETRYABLE_REASONS = frozenset(
    {
        FailureReason.SERVICE_UNAVAILABLE,
        FailureReason.INTERNAL_PROCESSING_ERROR,
        FailureReason.COULD_NOT_PERFORM_TRANSACTION,
        FailureReason.EXPIRED,
    }
)


def map_status(raw: object) -> Status:
    """SUCCESSFUL -> successful, PENDING -> pending, FAILED -> failed.

    Anything unrecognised is PENDING (never assume success or failure we cannot prove); the
    caller keeps polling and escalates after the schedule's deadline.
    """
    value = str(raw or "").strip().upper()
    if value == "SUCCESSFUL":
        return Status.SUCCESSFUL
    if value == "FAILED":
        return Status.FAILED
    return Status.PENDING


def map_reason(raw: object) -> tuple[FailureReason | None, str | None]:
    """Returns (enum, raw_code). Unknown code -> UNKNOWN with the raw code preserved."""
    if isinstance(raw, dict):
        raw = raw.get("code") or raw.get("message")
    if raw in (None, ""):
        return None, None
    code = str(raw)
    key = code.strip().upper()
    if key in _REASONS:
        return FailureReason[key], code
    return FailureReason.UNKNOWN, code


@dataclass(frozen=True)
class PaymentResult:
    reference_id: str
    status: Status
    reason: FailureReason | None = None
    raw_reason: str | None = None
    financial_transaction_id: str | None = None
    amount_ugx: int | None = None
    currency: str | None = None
    raw: dict = field(default_factory=dict, repr=False)

    @property
    def is_final(self) -> bool:
        return self.status is not Status.PENDING
