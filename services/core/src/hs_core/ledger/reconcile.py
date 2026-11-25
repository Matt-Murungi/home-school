"""Reconciliation primitives (NFR-8, SR 7.8 daily reconciliation) for $05/R04 to schedule later.
Pure comparisons between what a provider says (payment rows or a statement) and what the ledger
derives. They read the ledger and write nothing. Real-money reconciliation also needs the
providers' own statements (owner/R04): these functions take them as plain data."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from .errors import InvalidTransaction
from .flows import Provider, cash_account
from .model import Side
from .money import check_amount
from .service import LedgerService


@dataclass(frozen=True, slots=True)
class ProviderPayment:
    """One successful collection as the provider/payment table reports it."""

    payment_id: str
    amount: int


@dataclass(frozen=True, slots=True)
class PaymentReconciliation:
    provider: str
    provider_total: int
    ledger_total: int
    missing_in_ledger: tuple[str, ...]  # provider has it, ledger does not
    unexpected_in_ledger: tuple[str, ...]  # ledger has it, provider does not
    amount_mismatch: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not (
            self.missing_in_ledger
            or self.unexpected_in_ledger
            or self.amount_mismatch
            or self.provider_total != self.ledger_total
        )


@dataclass(frozen=True, slots=True)
class CashReconciliation:
    provider: str
    ledger_balance: int
    reported_balance: int

    @property
    def difference(self) -> int:
        return self.reported_balance - self.ledger_balance

    @property
    def ok(self) -> bool:
        return self.difference == 0


def sum_payments(payments: Iterable[ProviderPayment]) -> int:
    return sum(check_amount(p.amount, what="provider payment amount") for p in payments)


def reconcile_payments(
    service: LedgerService,
    provider: Provider | str,
    payments: Iterable[ProviderPayment],
    as_of: datetime | None = None,
) -> PaymentReconciliation:
    """Per-payment comparison of the provider's successful collections with the net debits the
    ledger holds on that provider's cash account for `reference_type=payment` (reversed
    payments net to zero and count as absent)."""
    provider = Provider(provider)
    provided: dict[str, int] = {}
    for p in payments:
        if p.payment_id in provided:
            raise InvalidTransaction("duplicate payment id in the provider list")
        provided[p.payment_id] = check_amount(p.amount, what="provider payment amount")
    net: dict[str, int] = {}
    for e in service.statement(cash_account(provider), as_of):
        if e.reference_type == "payment" and e.reference_id:
            delta = e.amount if e.side is Side.DEBIT else -e.amount
            net[e.reference_id] = net.get(e.reference_id, 0) + delta
    in_ledger = {k: v for k, v in net.items() if v != 0}
    return PaymentReconciliation(
        provider=provider.value,
        provider_total=sum(provided.values()),
        ledger_total=sum(in_ledger.values()),
        missing_in_ledger=tuple(sorted(set(provided) - set(in_ledger))),
        unexpected_in_ledger=tuple(sorted(set(in_ledger) - set(provided))),
        amount_mismatch=tuple(
            sorted(k for k in set(provided) & set(in_ledger) if provided[k] != in_ledger[k])
        ),
    )


def reconcile_cash(
    service: LedgerService,
    provider: Provider | str,
    reported_balance: int,
    as_of: datetime | None = None,
) -> CashReconciliation:
    """The provider's reported wallet balance against the derived balance of its cash account."""
    provider = Provider(provider)
    if type(reported_balance) is not int:
        raise InvalidTransaction("reported balance must be a plain int (integer UGX)")
    ledger = service.balance(cash_account(provider), as_of)
    return CashReconciliation(provider.value, ledger.amount, reported_balance)
