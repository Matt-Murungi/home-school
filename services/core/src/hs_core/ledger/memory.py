"""In-memory `LedgerStore`: trivially atomic (one lock). The reference for the contract tests and
the fast path for tests of everything above the store."""

from __future__ import annotations

import threading
from collections.abc import Iterator
from datetime import datetime

from .errors import AccountConflict, IdempotencyConflict
from .model import (
    Account,
    AccountTotals,
    Entry,
    OrphanGroup,
    PostedTransaction,
    PreparedTransaction,
    differences,
    iso,
)


class InMemoryLedgerStore:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._accounts: dict[str, Account] = {}
        self._tx: dict[str, PostedTransaction] = {}
        self._by_key: dict[str, str] = {}

    def create_account(self, account: Account) -> Account:
        with self._lock:
            existing = self._accounts.get(account.code)
            if existing is None:
                self._accounts[account.code] = account
                return account
            if not existing.same_definition(account):
                raise AccountConflict(f"account {account.code} exists with another definition")
            return existing

    def get_account(self, code: str) -> Account | None:
        with self._lock:
            return self._accounts.get(code)

    def list_accounts(self) -> list[Account]:
        with self._lock:
            return list(self._accounts.values())

    def append(self, tx: PreparedTransaction) -> PostedTransaction:
        with self._lock:
            existing = self._tx.get(tx.id)
            if existing is not None:
                diff = differences(tx, existing)
                if diff:
                    raise IdempotencyConflict(tx.idempotency_key, diff)
                return PostedTransaction(**{**_fields(existing), "replayed": True})
            if tx.reverses_transaction_id and any(
                p.reverses_transaction_id == tx.reverses_transaction_id for p in self._tx.values()
            ):
                from .errors import AlreadyReversed

                raise AlreadyReversed(tx.reverses_transaction_id)
            entries = tuple(
                Entry(
                    transaction_id=tx.id,
                    line=ln.line,
                    account_id=ln.account_id,
                    side=ln.side,
                    amount=ln.amount,
                    currency=tx.currency,
                    posted_at=tx.posted_at,
                    reference_type=tx.reference_type,
                    reference_id=tx.reference_id,
                )
                for ln in tx.lines
            )
            posted = PostedTransaction(
                id=tx.id,
                idempotency_key=tx.idempotency_key,
                kind=tx.kind,
                entry_count=len(tx.lines),
                total_amount=tx.total_amount,
                currency=tx.currency,
                posted_at=tx.posted_at,
                lines=entries,
                reference_type=tx.reference_type,
                reference_id=tx.reference_id,
                description=tx.description,
                reverses_transaction_id=tx.reverses_transaction_id,
                reversal_reason=tx.reversal_reason,
                created_by_id=tx.created_by_id,
            )
            self._tx[tx.id] = posted
            self._by_key[tx.idempotency_key] = tx.id
            return posted

    def get_transaction(self, transaction_id: str) -> PostedTransaction | None:
        with self._lock:
            return self._tx.get(transaction_id)

    def get_by_key(self, idempotency_key: str) -> PostedTransaction | None:
        with self._lock:
            tid = self._by_key.get(idempotency_key)
            return self._tx.get(tid) if tid else None

    def _sealed_entries(self, as_of: datetime | None) -> list[Entry]:
        cut = iso(as_of) if as_of else None
        with self._lock:
            return [
                e for t in self._tx.values() for e in t.lines if cut is None or e.posted_at <= cut
            ]

    def account_totals(
        self, as_of: datetime | None = None, account_id: str | None = None
    ) -> dict[str, AccountTotals]:
        out: dict[str, list[int]] = {}
        for e in self._sealed_entries(as_of):
            if account_id is not None and e.account_id != account_id:
                continue
            t = out.setdefault(e.account_id, [0, 0])
            t[0 if e.side.value == "debit" else 1] += e.amount
        return {k: AccountTotals(*v) for k, v in out.items()}

    def entries(self, account_id: str, as_of: datetime | None = None) -> Iterator[Entry]:
        yield from (e for e in self._sealed_entries(as_of) if e.account_id == account_id)

    def find_orphans(
        self, older_than: datetime, since: datetime | None = None
    ) -> list[OrphanGroup]:
        return []  # atomic: no partial state exists


def _fields(p: PostedTransaction) -> dict:
    import dataclasses

    return {f.name: getattr(p, f.name) for f in dataclasses.fields(p)}
