"""Named ledger errors. Messages carry ids and field names only: never names, phones or amounts
together (PII-free logs, F08)."""

from __future__ import annotations


class LedgerError(Exception):
    """Base class."""


class InvalidAmount(LedgerError, ValueError):
    """Not a positive plain int within the configured ceiling (floats, bools, strings refused)."""


class InvalidTransaction(LedgerError, ValueError):
    """The transaction is malformed (shape, fields, currency, lines)."""


class UnbalancedTransaction(InvalidTransaction):
    """Sum of debits is not equal to the sum of credits."""


class TooFewLines(InvalidTransaction):
    """Empty or single-line transaction."""


class DuplicateLine(InvalidTransaction):
    """The same account appears twice in one transaction."""


class MixedCurrencies(InvalidTransaction):
    """Lines (or accounts) of different currencies in one transaction."""


class UnknownAccount(LedgerError, LookupError):
    """A line names an account that does not exist."""


class InactiveAccount(InvalidTransaction):
    """A line names a retired account."""


class AccountConflict(LedgerError):
    """An account with this code exists with a different definition."""


class IdempotencyConflict(LedgerError):
    """The idempotency key was already used for DIFFERENT content. Never overwrites."""

    def __init__(self, key: str, fields: list[str]) -> None:
        super().__init__(f"idempotency key {key!r} already used with different {', '.join(fields)}")
        self.key = key
        self.fields = fields


class UnknownTransaction(LedgerError, LookupError):
    """No sealed transaction with this id."""


class AlreadyReversed(LedgerError):
    """The original transaction already has a reversal (a posting is reversed once)."""


class AuditFailed(LedgerError):
    """The transaction is sealed but the audit sink failed; retry the same post (idempotent)."""
