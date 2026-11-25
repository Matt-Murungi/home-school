"""Posting helpers for the SR flows: PURE functions that return a `TransactionRequest`. They post
nothing and are not wired to events (later tickets do that).

Idempotency key convention: `<prefix>:<source id>`, one key per source business object, so the
same payment/earning/payout can never be posted twice and every retry is the same request:

    invoice:<invoice id>      payment:<payment id>     earning:<earning id>
    payout:<payout id>        refund:<refund id>       credit:<credit id>
    fee:<fee id>              adjustment:<admin-chosen id>
    reversal:<original transaction id>   (built by `reversal_of`; one reversal per original)

Source ids are Appwrite row ids (1-36 chars of [A-Za-z0-9._-]); they also become `reference_id`.

Account semantics (accrual; the accountant confirms before launch, see docs/ops/ledger.md):
  invoice issued     Dr receivable        Cr platform revenue     (gross billing)
  payment received   Dr cash:<provider>   Cr receivable
  earning accrued    Dr platform revenue  Cr teacher payable      (negative earning = clawback)
  payout disbursed   Dr teacher payable   Cr cash:<provider>
  refund paid        Dr refunds+credits   Cr cash:<provider>
  credit issued      Dr refunds+credits   Cr receivable
  provider fee       Dr provider fees     Cr cash:<provider>
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from enum import StrEnum
from typing import Any

from . import chart
from .errors import InvalidTransaction
from .model import (
    ID_CHARS,
    Kind,
    LineRequest,
    PostedTransaction,
    Side,
    TransactionRequest,
    credit,
    debit,
)


class Provider(StrEnum):
    MTN_MOMO = "mtn_momo"
    AIRTEL_MONEY = "airtel_money"
    CARD = "card_gateway"


_CASH = {
    Provider.MTN_MOMO: chart.CASH_MTN,
    Provider.AIRTEL_MONEY: chart.CASH_AIRTEL,
    Provider.CARD: chart.CASH_CARD,
}


def cash_account(provider: Provider | str) -> str:
    try:
        return _CASH[Provider(provider)]
    except ValueError:
        raise InvalidTransaction("unknown payment provider") from None


def idempotency_key(prefix: str, source_id: str) -> str:
    if not isinstance(source_id, str) or not ID_CHARS.fullmatch(source_id):
        raise InvalidTransaction("source id must be 1-36 characters of [A-Za-z0-9._-]")
    return f"{prefix}:{source_id}"


def _req(
    prefix: str,
    kind: Kind,
    source_id: str,
    lines: tuple[LineRequest, ...],
    *,
    reference_type: str | None = None,
    **kw: Any,
) -> TransactionRequest:
    return TransactionRequest(
        idempotency_key=idempotency_key(prefix, source_id),
        kind=kind,
        lines=lines,
        reference_type=reference_type or prefix,
        reference_id=source_id,
        **kw,
    )


def invoice_issued(invoice_id: str, amount: int, *, created_by_id: str | None = None):
    # The M04 kind enum has no `invoice`: it is booked as `adjustment` with reference_type
    # `invoice` (owner/schema follow-up: add the kind).
    return _req(
        "invoice",
        Kind.ADJUSTMENT,
        invoice_id,
        (debit(chart.RECEIVABLES, amount), credit(chart.REVENUE, amount)),
        created_by_id=created_by_id,
    )


def payment_received(
    payment_id: str, amount: int, provider: Provider | str, *, invoice_id: str | None = None
):
    return _req(
        "payment",
        Kind.PAYMENT,
        payment_id,
        (debit(cash_account(provider), amount), credit(chart.RECEIVABLES, amount)),
        description=f"invoice {invoice_id}" if invoice_id else None,
    )


def earning_accrued(earning_id: str, teacher_id: str, amount: int):
    """Teacher earning. A negative `amount` (adjustment/clawback) flips the sides."""
    payable = chart.teacher_payable_code(teacher_id)
    side_flip = type(amount) is int and amount < 0
    n = -amount if side_flip else amount
    a, b = (chart.REVENUE, payable) if not side_flip else (payable, chart.REVENUE)
    return _req("earning", Kind.EARNING, earning_id, (debit(a, n), credit(b, n)))


def payout_disbursed(payout_id: str, teacher_id: str, amount: int, provider: Provider | str):
    return _req(
        "payout",
        Kind.PAYOUT,
        payout_id,
        (
            debit(chart.teacher_payable_code(teacher_id), amount),
            credit(cash_account(provider), amount),
        ),
    )


def refund_paid(refund_id: str, amount: int, provider: Provider | str):
    return _req(
        "refund",
        Kind.REFUND,
        refund_id,
        (debit(chart.REFUNDS_CREDITS, amount), credit(cash_account(provider), amount)),
    )


def credit_issued(credit_id: str, amount: int):
    return _req(
        "credit",
        Kind.REFUND,
        credit_id,
        (debit(chart.REFUNDS_CREDITS, amount), credit(chart.RECEIVABLES, amount)),
        reference_type="credit",
    )


def provider_fee(fee_id: str, amount: int, provider: Provider | str):
    return _req(
        "fee",
        Kind.FEE,
        fee_id,
        (debit(chart.PROVIDER_FEES, amount), credit(cash_account(provider), amount)),
    )


def adjustment(
    adjustment_id: str, lines: Iterable[LineRequest], *, reason: str, created_by_id: str
):
    """Manual admin adjustment: caller-chosen balanced lines, a reason and the acting admin."""
    if not isinstance(reason, str) or not reason.strip():
        raise InvalidTransaction("an adjustment needs a reason")
    return _req(
        "adjustment",
        Kind.ADJUSTMENT,
        adjustment_id,
        tuple(lines),
        description=reason.strip()[:500],
        created_by_id=created_by_id,
    )


def reversal_of(
    original: PostedTransaction,
    codes: Mapping[str, str],
    *,
    reason: str,
    created_by_id: str | None = None,
) -> TransactionRequest:
    """The contra transaction of `original`: same accounts and amounts, opposite sides.
    `codes` maps account id -> code. The original is only read, never modified."""
    if not isinstance(reason, str) or not reason.strip():
        raise InvalidTransaction("a reversal needs a reason")
    try:
        lines = tuple(
            LineRequest(codes[e.account_id], Side(e.side).opposite, e.amount)
            for e in original.lines
        )
    except KeyError:
        raise InvalidTransaction(
            "original references an account that is not in the chart"
        ) from None
    return TransactionRequest(
        idempotency_key=f"reversal:{original.id}",
        kind=Kind.REVERSAL,
        lines=lines,
        reference_type=original.reference_type,
        reference_id=original.reference_id,
        reverses_transaction_id=original.id,
        reversal_reason=reason.strip()[:500],
        created_by_id=created_by_id,
        currency=original.currency,
    )
