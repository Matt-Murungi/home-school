"""Integer UGX only (NFR, CLAUDE.md rule 6). No floats, no cents, no Decimal, no bool."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .errors import InvalidAmount, InvalidTransaction, MixedCurrencies

DEFAULT_CURRENCY = "UGX"
# Per-line ceiling: one trillion UGX. Far above any real posting, far below 2**63 (the largest
# Appwrite integer) even when 100 lines are summed; a bigger number is a bug or an attack.
MAX_AMOUNT = 10**12
_CURRENCY = re.compile(r"[A-Z]{3}")


def check_currency(value: Any) -> str:
    if not isinstance(value, str) or not _CURRENCY.fullmatch(value):
        raise InvalidTransaction("currency must be a three-letter upper-case code")
    return value


def check_amount(value: Any, *, what: str = "amount") -> int:
    """A ledger line amount: plain int, 1..MAX_AMOUNT."""
    if type(value) is not int:  # excludes bool, float, str, Decimal and int subclasses
        raise InvalidAmount(f"{what} must be a plain int (integer UGX), got {type(value).__name__}")
    if value <= 0:
        raise InvalidAmount(f"{what} must be positive")
    if value > MAX_AMOUNT:
        raise InvalidAmount(f"{what} exceeds the ceiling of {MAX_AMOUNT}")
    return value


@dataclass(frozen=True, slots=True)
class Money:
    """Signed integer amount in one currency (balances can be negative; lines cannot)."""

    amount: int
    currency: str = DEFAULT_CURRENCY

    def __post_init__(self) -> None:
        if type(self.amount) is not int:
            raise InvalidAmount(f"Money needs a plain int, got {type(self.amount).__name__}")
        if abs(self.amount) > MAX_AMOUNT * 1000:  # sums of many lines, still far below 2**63
            raise InvalidAmount("Money amount out of range")
        check_currency(self.currency)

    def _same(self, other: Money) -> None:
        if not isinstance(other, Money):
            raise TypeError("Money can only be combined with Money")
        if other.currency != self.currency:
            raise MixedCurrencies("cannot combine different currencies")

    def __add__(self, other: Money) -> Money:
        self._same(other)
        return Money(self.amount + other.amount, self.currency)

    def __sub__(self, other: Money) -> Money:
        self._same(other)
        return Money(self.amount - other.amount, self.currency)

    def __neg__(self) -> Money:
        return Money(-self.amount, self.currency)

    def __bool__(self) -> bool:
        return self.amount != 0

    def __str__(self) -> str:
        return f"{self.amount} {self.currency}"
