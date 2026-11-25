"""Ledger value objects. Row ids are DETERMINISTIC (hash of the code / idempotency key), so
`create` with a known id is an atomic claim (Appwrite 409) and every retry converges."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from .errors import InvalidTransaction
from .money import DEFAULT_CURRENCY

MAX_LINES = 100
ID_CHARS = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,35}")  # an Appwrite row id
KEY = re.compile(r"[a-z_]{2,20}:[A-Za-z0-9._:-]{1,170}")  # <kind>:<source id>, <= 200 chars
CODE = re.compile(r"[a-z_]{2,30}:[A-Za-z0-9._-]{1,66}")  # e.g. cash:mtn_momo, teacher_payable:<id>


class Side(StrEnum):
    DEBIT = "debit"
    CREDIT = "credit"

    @property
    def opposite(self) -> Side:
        return Side.CREDIT if self is Side.DEBIT else Side.DEBIT


class AccountType(StrEnum):  # the schema enum (M04); contra accounts are a normal_side flip
    ASSET = "asset"
    LIABILITY = "liability"
    REVENUE = "revenue"
    EXPENSE = "expense"


class Kind(StrEnum):  # the schema enum of ledger_transaction.kind
    PAYMENT = "payment"
    EARNING = "earning"
    PAYOUT = "payout"
    REFUND = "refund"
    FEE = "fee"
    ADJUSTMENT = "adjustment"
    REVERSAL = "reversal"


_NATURAL = {
    AccountType.ASSET: Side.DEBIT,
    AccountType.EXPENSE: Side.DEBIT,
    AccountType.LIABILITY: Side.CREDIT,
    AccountType.REVENUE: Side.CREDIT,
}


def _digest(text: str, n: int) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:n]


def account_id_for(code: str) -> str:
    return "a" + _digest(f"account:{code}", 32)


def transaction_id_for(key: str) -> str:
    return "t" + _digest(f"transaction:{key}", 32)


def entry_id_for(transaction_id: str, line: int) -> str:
    return "e" + _digest(f"entry:{transaction_id}:{line}", 32)


_TS_FORMAT = "%Y-%m-%dT%H:%M:%S"


def iso(moment: datetime) -> str:
    """Fixed-width UTC instant, sortable as a string: 2026-11-25T09:30:00.123Z."""
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise InvalidTransaction("timestamps must be timezone-aware (UTC)")
    m = moment.astimezone(UTC)
    return f"{m.strftime(_TS_FORMAT)}.{m.microsecond // 1000:03d}Z"


def parse_ts(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class Account:
    code: str
    name: str
    type: AccountType
    normal_side: Side
    currency: str = DEFAULT_CURRENCY
    teacher_id: str | None = None
    active: bool = True
    created_at: str = ""

    @property
    def id(self) -> str:
        return account_id_for(self.code)

    @property
    def is_contra(self) -> bool:
        """Normal side opposite to the type's natural side (e.g. refunds and credits)."""
        return self.normal_side is not _NATURAL[self.type]

    def same_definition(self, other: Account) -> bool:
        return (
            self.code == other.code
            and self.name == other.name
            and self.type == other.type
            and self.normal_side == other.normal_side
            and self.currency == other.currency
            and (self.teacher_id or None) == (other.teacher_id or None)
        )


@dataclass(frozen=True, slots=True)
class LineRequest:
    """One line as the caller writes it. Nothing is validated until `LedgerService.post`."""

    account_code: str
    side: Side | str
    amount: Any


def debit(account_code: str, amount: Any) -> LineRequest:
    return LineRequest(account_code, Side.DEBIT, amount)


def credit(account_code: str, amount: Any) -> LineRequest:
    return LineRequest(account_code, Side.CREDIT, amount)


@dataclass(frozen=True, slots=True)
class TransactionRequest:
    """What the posting helpers return and `post` accepts (no ids, no clocks)."""

    idempotency_key: str
    kind: Kind | str
    lines: tuple[LineRequest, ...]
    reference_type: str | None = None
    reference_id: str | None = None
    description: str | None = None
    reverses_transaction_id: str | None = None
    reversal_reason: str | None = None
    created_by_id: str | None = None
    currency: str = DEFAULT_CURRENCY


@dataclass(frozen=True, slots=True)
class PreparedLine:
    line: int
    account_id: str
    side: Side
    amount: int


@dataclass(frozen=True, slots=True)
class PreparedTransaction:
    """Fully validated, canonical (lines sorted, numbered from 1). What a store appends."""

    id: str
    idempotency_key: str
    kind: Kind
    lines: tuple[PreparedLine, ...]
    currency: str
    posted_at: str
    reference_type: str | None = None
    reference_id: str | None = None
    description: str | None = None
    reverses_transaction_id: str | None = None
    reversal_reason: str | None = None
    created_by_id: str | None = None

    @property
    def total_amount(self) -> int:
        return sum(ln.amount for ln in self.lines if ln.side is Side.DEBIT)

    def header_row(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "idempotency_key": self.idempotency_key,
            "reference_type": self.reference_type,
            "reference_id": self.reference_id,
            "description": self.description,
            "reverses_transaction_id": self.reverses_transaction_id,
            "reversal_reason": self.reversal_reason,
            "entry_count": len(self.lines),
            "total_amount": self.total_amount,
            "currency": self.currency,
            "created_by_id": self.created_by_id,
            "posted_at": self.posted_at,
        }

    def entry_row(self, ln: PreparedLine, posted_at: str | None = None) -> dict[str, Any]:
        return {
            "transaction_id": self.id,
            "line": ln.line,
            "account_id": ln.account_id,
            "side": ln.side.value,
            "amount": ln.amount,
            "currency": self.currency,
            "reference_type": self.reference_type,
            "reference_id": self.reference_id,
            "posted_at": posted_at or self.posted_at,
        }


@dataclass(frozen=True, slots=True)
class Entry:
    transaction_id: str
    line: int
    account_id: str
    side: Side
    amount: int
    currency: str
    posted_at: str
    reference_type: str | None = None
    reference_id: str | None = None

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> Entry:
        return cls(
            transaction_id=row["transaction_id"],
            line=row["line"],
            account_id=row["account_id"],
            side=Side(row["side"]),
            amount=row["amount"],
            currency=row.get("currency") or DEFAULT_CURRENCY,
            posted_at=row["posted_at"],
            reference_type=row.get("reference_type"),
            reference_id=row.get("reference_id"),
        )


@dataclass(frozen=True, slots=True)
class PostedTransaction:
    id: str
    idempotency_key: str
    kind: Kind
    entry_count: int
    total_amount: int
    currency: str
    posted_at: str
    lines: tuple[Entry, ...] = ()
    reference_type: str | None = None
    reference_id: str | None = None
    description: str | None = None
    reverses_transaction_id: str | None = None
    reversal_reason: str | None = None
    created_by_id: str | None = None
    replayed: bool = field(default=False, compare=False)

    @classmethod
    def from_rows(cls, header: dict[str, Any], entries: list[dict[str, Any]]) -> PostedTransaction:
        lines = tuple(sorted((Entry.from_row(r) for r in entries), key=lambda e: e.line))
        return cls(
            id=header["$id"],
            idempotency_key=header["idempotency_key"],
            kind=Kind(header["kind"]),
            entry_count=header["entry_count"],
            total_amount=header["total_amount"],
            currency=header.get("currency") or DEFAULT_CURRENCY,
            posted_at=header["posted_at"],
            lines=lines,
            reference_type=header.get("reference_type"),
            reference_id=header.get("reference_id"),
            description=header.get("description"),
            reverses_transaction_id=header.get("reverses_transaction_id"),
            reversal_reason=header.get("reversal_reason"),
            created_by_id=header.get("created_by_id"),
        )


def differences(tx: PreparedTransaction, posted: PostedTransaction) -> list[str]:
    """Names of the fields in which an already-posted transaction differs from what the caller
    now asks for under the same idempotency key (empty list = same content). posted_at is the
    service's clock, not content."""
    diff: list[str] = []
    for name in (
        "idempotency_key",
        "kind",
        "reference_type",
        "reference_id",
        "description",
        "reverses_transaction_id",
        "reversal_reason",
        "currency",
        "created_by_id",
    ):
        if (getattr(tx, name) or None) != (getattr(posted, name) or None):
            diff.append(name)
    if tx.total_amount != posted.total_amount:
        diff.append("total_amount")
    if len(tx.lines) != posted.entry_count or len(posted.lines) != len(tx.lines):
        diff.append("lines")
    else:
        for want, got in zip(tx.lines, posted.lines, strict=True):
            if (want.line, want.account_id, want.side, want.amount) != (
                got.line,
                got.account_id,
                got.side,
                got.amount,
            ):
                diff.append("lines")
                break
    return diff


@dataclass(frozen=True, slots=True)
class OrphanGroup:
    """Lines whose transaction header was never written (a crashed post)."""

    transaction_id: str
    lines: tuple[Entry, ...]
    oldest_posted_at: str


@dataclass(frozen=True, slots=True)
class AccountTotals:
    debits: int = 0
    credits: int = 0
