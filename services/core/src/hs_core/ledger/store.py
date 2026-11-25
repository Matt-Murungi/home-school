"""The `LedgerStore` boundary. Everything above it (validation, chart of accounts, posting
helpers, balances as Money, reconciliation) is storage-agnostic; moving the ledger from Appwrite to
Postgres (ADR 0012, owner Q10) is an adapter swap. See docs/ops/ledger.md.

Contract every implementation must keep (tests/test_ledger_store_contract.py runs it against all):

* `append` is atomic IN EFFECT: after it returns, or after ANY crash/retry sequence, either the
  whole transaction is visible or none of it is. Idempotent by key; different content under a used
  key raises `IdempotencyConflict` and changes nothing.
* Readers (`account_totals`, `entries`, `get_transaction`, `get_by_key`) see sealed transactions
  only. Totals and entries never expose a half-posted transaction.
* Rows are never updated or deleted.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from typing import Protocol

from .model import (
    Account,
    AccountTotals,
    Entry,
    OrphanGroup,
    PostedTransaction,
    PreparedTransaction,
)


class LedgerStore(Protocol):
    def create_account(self, account: Account) -> Account:
        """Create, or return the existing account with the same code and definition (race-safe).
        Raises `AccountConflict` when the code exists with a different definition."""
        ...

    def get_account(self, code: str) -> Account | None: ...

    def list_accounts(self) -> list[Account]: ...

    def append(self, tx: PreparedTransaction) -> PostedTransaction:
        """Post atomically in effect. `replayed=True` on the result when the key already existed
        with the same content (the original is returned, nothing is written twice)."""
        ...

    def get_transaction(self, transaction_id: str) -> PostedTransaction | None: ...

    def get_by_key(self, idempotency_key: str) -> PostedTransaction | None: ...

    def account_totals(
        self, as_of: datetime | None = None, account_id: str | None = None
    ) -> dict[str, AccountTotals]:
        """Debit and credit totals of sealed entries (posted_at <= as_of), per account id.
        All accounts when `account_id` is None; the totals of one snapshot always balance."""
        ...

    def entries(self, account_id: str, as_of: datetime | None = None) -> Iterator[Entry]:
        """Sealed entries of one account (statement), lazily paged."""
        ...

    def find_orphans(
        self, older_than: datetime, since: datetime | None = None
    ) -> list[OrphanGroup]:
        """Unsealed line groups whose oldest line is older than `older_than`."""
        ...
