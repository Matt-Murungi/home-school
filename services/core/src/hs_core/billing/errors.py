"""Named billing errors (each a distinct class so callers and tests can tell them apart)."""

from __future__ import annotations

from .states import ForbiddenTransition

__all__ = [
    "BillingError",
    "ChildAlreadySubscribed",
    "ConcurrentTransition",
    "ForbiddenTransition",
    "GraceNotExpired",
    "InvalidMoney",
    "InvoiceNotAllowed",
    "PaymentRefReuse",
    "PlanUnavailable",
    "UnknownInvoice",
    "UnknownSubscription",
]


class BillingError(Exception): ...


class UnknownSubscription(BillingError): ...


class UnknownInvoice(BillingError): ...


class ConcurrentTransition(BillingError):
    """Another writer changed the subscription first (optimistic check lost). Re-read and retry."""


class ChildAlreadySubscribed(BillingError):
    """The child already has a live (not cancelled) subscription on a different plan."""


class PlanUnavailable(BillingError): ...


class InvalidMoney(BillingError, TypeError):
    """Money must be a non-negative integer number of UGX (never float, bool or str)."""


class InvoiceNotAllowed(BillingError): ...


class PaymentRefReuse(BillingError):
    """The same provider payment reference was presented for a different invoice."""


class GraceNotExpired(BillingError): ...
