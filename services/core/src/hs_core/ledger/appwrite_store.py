"""`LedgerStore` over the Appwrite repository. Appwrite has no multi-row transaction, so atomicity
IN EFFECT comes from this write protocol (the schema's own rule, M04: lines first, header last):

1. Ids are deterministic: transaction id = hash(idempotency key); line id = hash(transaction id,
   line number); so every retry targets the same rows and `create` 409 means "already written".
2. Write the lines in order 1..n (append-only wrapper: create only).
3. SEAL: create the header row (entry_count, total_amount). The header is the commit record.
4. Readers only count entries whose header exists. A crash anywhere before step 3 leaves orphan
   lines that nobody sees; the caller's retry (same key, same content) finds them, verifies they
   match, writes the rest, and seals. Same key with different content raises, never overwrites.

Every ledger row goes through `AppendOnlyRepository`; this module cannot update or delete them.
Only `ledger_account` (not append-only, never updated here) uses the plain repository.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable, Iterable, Iterator
from datetime import UTC, datetime
from typing import Any

from appwrite.query import Query

from ..append_only import AppendOnlyRepository
from ..repository import Conflict, NotFound, Repository
from .errors import AccountConflict, AlreadyReversed, IdempotencyConflict
from .model import (
    Account,
    AccountTotals,
    AccountType,
    Entry,
    OrphanGroup,
    PostedTransaction,
    PreparedTransaction,
    Side,
    account_id_for,
    differences,
    entry_id_for,
    iso,
    parse_ts,
    transaction_id_for,
)

ACCOUNT = "ledger_account"
HEADER = "ledger_transaction"
ENTRY = "ledger_entry"
PAGE = 1000  # well under Appwrite's 5,000 cap; all reads page by cursor, never by offset/total
ID_BATCH = 100  # values per `equal` query


class AppwriteLedgerStore:
    def __init__(
        self,
        repo: Repository,
        *,
        clock: Callable[[], datetime] | None = None,
        page_size: int = PAGE,
    ) -> None:
        self._plain = repo
        self._ao = AppendOnlyRepository(repo)
        self._clock = clock or (lambda: datetime.now(UTC))
        self._page = page_size

    # ---- accounts -------------------------------------------------------------------------
    @staticmethod
    def _account_row(a: Account) -> dict[str, Any]:
        return {
            "code": a.code,
            "name": a.name,
            "type": a.type.value,
            "normal_side": a.normal_side.value,
            "teacher_id": a.teacher_id,
            "currency": a.currency,
            "active": a.active,
            "created_at": a.created_at,
        }

    @staticmethod
    def _account(row: dict[str, Any]) -> Account:
        return Account(
            code=row["code"],
            name=row["name"],
            type=AccountType(row["type"]),
            normal_side=Side(row["normal_side"]),
            currency=row.get("currency") or "UGX",
            teacher_id=row.get("teacher_id"),
            active=row.get("active", True) is not False,
            created_at=row.get("created_at") or "",
        )

    def create_account(self, account: Account) -> Account:
        try:
            self._plain.create(ACCOUNT, account.id, self._account_row(account))
            return account
        except Conflict:
            existing = self.get_account(account.code)
            if existing is None or not existing.same_definition(account):
                raise AccountConflict(
                    f"account {account.code} exists with another definition"
                ) from None
            return existing

    def get_account(self, code: str) -> Account | None:
        try:
            return self._account(self._plain.get(ACCOUNT, account_id_for(code)))
        except NotFound:
            return None

    def list_accounts(self) -> list[Account]:
        return [self._account(r) for r in self._pages(self._plain, ACCOUNT, [])]

    # ---- paging ---------------------------------------------------------------------------
    def _pages(self, repo: Any, table: str, queries: list[str]) -> Iterator[dict[str, Any]]:
        cursor: str | None = None
        while True:
            q = [*queries, Query.limit(self._page)]
            if cursor is not None:
                q.append(Query.cursor_after(cursor))
            rows = repo.list(table, q)
            yield from rows
            if len(rows) < self._page:
                return
            cursor = rows[-1]["$id"]

    def _sealed(self, ids: Iterable[str], known: set[str], unsealed: set[str]) -> None:
        """Fill `known` with those of `ids` that have a header (batched, cached)."""
        todo = [i for i in dict.fromkeys(ids) if i not in known and i not in unsealed]
        for i in range(0, len(todo), ID_BATCH):
            chunk = todo[i : i + ID_BATCH]
            rows = self._ao.list(HEADER, [Query.equal("$id", chunk), Query.limit(ID_BATCH)])
            found = {r["$id"] for r in rows}
            known |= found
            unsealed |= set(chunk) - found

    # ---- the write protocol ---------------------------------------------------------------
    def _header_row(self, transaction_id: str) -> dict[str, Any] | None:
        try:
            return self._ao.get(HEADER, transaction_id)
        except NotFound:
            return None

    def _line_rows(self, transaction_id: str) -> list[dict[str, Any]]:
        return list(self._pages(self._ao, ENTRY, [Query.equal("transaction_id", [transaction_id])]))

    def _replay(self, tx: PreparedTransaction, header: dict[str, Any]) -> PostedTransaction:
        posted = PostedTransaction.from_rows(header, self._line_rows(tx.id))
        diff = differences(tx, posted)
        if diff:
            raise IdempotencyConflict(tx.idempotency_key, diff)
        return dataclasses.replace(posted, replayed=True)

    def _write_line(self, tx: PreparedTransaction, ln: Any) -> dict[str, Any]:
        """Create the line, or on a 409 read the persisted one; return the PERSISTED row after
        verifying it carries what this request would have written (posted_at included)."""
        row = tx.entry_row(ln)
        row_id = entry_id_for(tx.id, ln.line)
        try:
            self._ao.create(ENTRY, row_id, row)
            return row
        except Conflict:  # a retry or a concurrent poster of the same key wrote it first
            have = self._ao.get(ENTRY, row_id)
            self._verify_line(tx, ln, have)
            return have

    @staticmethod
    def _verify_line(tx: PreparedTransaction, ln: Any, have: dict[str, Any]) -> None:
        """Everything a line stores must match this request (the header-only fields, kind,
        description, created_by, cannot be checked against orphans: the first sealer wins)."""
        want = tx.entry_row(ln)
        same = (
            all(
                (have.get(k) or None) == (want.get(k) or None)
                for k in (
                    "transaction_id",
                    "account_id",
                    "side",
                    "amount",
                    "reference_type",
                    "reference_id",
                )
            )
            and (have.get("currency") or "UGX") == want["currency"]
        )
        if not same or parse_ts(have["posted_at"]) != parse_ts(want["posted_at"]):
            raise IdempotencyConflict(tx.idempotency_key, ["lines"])

    def _seal(self, tx: PreparedTransaction) -> bool:
        """Write the commit record. True when this call sealed it, False when another did."""
        try:
            self._ao.create(HEADER, tx.id, tx.header_row())
            return True
        except Conflict:
            header = self._header_row(tx.id)
            if header is None:  # unique index hit with a different id: the original is reversed
                if tx.reverses_transaction_id:
                    raise AlreadyReversed(tx.reverses_transaction_id) from None
                raise
            self._replay(tx, header)  # same content or IdempotencyConflict
            return False

    def append(self, tx: PreparedTransaction) -> PostedTransaction:
        header = self._header_row(tx.id)
        if header is not None:
            return self._replay(tx, header)
        existing = {r["line"]: r for r in self._line_rows(tx.id)}  # orphans of a crashed attempt
        if set(existing) - {ln.line for ln in tx.lines}:
            raise IdempotencyConflict(tx.idempotency_key, ["lines"])
        # an earlier attempt fixed posted_at on its first line; reuse it so lines and header agree
        # Line 1 is the single source of posted_at: write it (or read the winner of a 409) and
        # adopt ITS timestamp for every other line and the header, whichever clock we have.
        first = existing.get(1)
        if first is None:
            first_ln = tx.lines[0]
            try:
                self._ao.create(ENTRY, entry_id_for(tx.id, 1), tx.entry_row(first_ln))
                first = tx.entry_row(first_ln)
            except Conflict:
                first = self._ao.get(ENTRY, entry_id_for(tx.id, 1))
        tx = dataclasses.replace(tx, posted_at=first["posted_at"])
        for ln in tx.lines:
            have = existing.get(ln.line) if ln.line != 1 else first
            if have is not None:
                self._verify_line(tx, ln, have)
            else:
                self._write_line(tx, ln)
        sealed_here = self._seal(tx)
        posted = self.get_transaction(tx.id)
        assert posted is not None
        return dataclasses.replace(posted, replayed=not sealed_here)

    # ---- reads ----------------------------------------------------------------------------
    def get_transaction(self, transaction_id: str) -> PostedTransaction | None:
        header = self._header_row(transaction_id)
        if header is None:
            return None
        return PostedTransaction.from_rows(header, self._line_rows(transaction_id))

    def get_by_key(self, idempotency_key: str) -> PostedTransaction | None:
        found = self.get_transaction(transaction_id_for(idempotency_key))
        return found if found and found.idempotency_key == idempotency_key else None

    def _account_entries(self, account_id: str, as_of: datetime | None) -> Iterator[dict[str, Any]]:
        q = [Query.equal("account_id", [account_id])]
        if as_of is not None:
            q.append(Query.less_than_equal("posted_at", iso(as_of)))
        return self._pages(self._ao, ENTRY, q)

    def entries(self, account_id: str, as_of: datetime | None = None) -> Iterator[Entry]:
        known: set[str] = set()
        unsealed: set[str] = set()
        batch: list[dict[str, Any]] = []

        def flush() -> Iterator[Entry]:
            self._sealed((r["transaction_id"] for r in batch), known, unsealed)
            for r in batch:
                if r["transaction_id"] in known:
                    yield Entry.from_row(r)
            batch.clear()

        for row in self._account_entries(account_id, as_of):
            batch.append(row)
            if len(batch) >= ID_BATCH:
                yield from flush()
        yield from flush()

    def account_totals(
        self, as_of: datetime | None = None, account_id: str | None = None
    ) -> dict[str, AccountTotals]:
        out: dict[str, list[int]] = {}

        def add(account: str, side: str, amount: int) -> None:
            t = out.setdefault(account, [0, 0])
            t[0 if side == "debit" else 1] += amount

        if account_id is not None:
            for e in self.entries(account_id, as_of):
                add(e.account_id, e.side.value, e.amount)
        else:
            # Phase 1: the sealed set. Every member already has ALL its lines, so phase 2 sees a
            # complete transaction or none: the totals of one call always balance, even while
            # other posts run.
            q = [Query.less_than_equal("posted_at", iso(as_of))] if as_of else []
            sealed = {r["$id"] for r in self._pages(self._ao, HEADER, q)}
            for r in self._pages(self._ao, ENTRY, []):
                if r["transaction_id"] in sealed:
                    add(r["account_id"], r["side"], r["amount"])
        return {k: AccountTotals(*v) for k, v in out.items()}

    def find_orphans(
        self, older_than: datetime, since: datetime | None = None
    ) -> list[OrphanGroup]:
        """Scans the entries of every account within [since, older_than] (uses the
        (account_id, posted_at) index). Meant for a periodic recovery job, not a request path."""
        groups: dict[str, list[Entry]] = {}
        for account in self.list_accounts():
            q = [
                Query.equal("account_id", [account.id]),
                Query.less_than_equal("posted_at", iso(older_than)),
            ]
            if since is not None:
                q.append(Query.greater_than_equal("posted_at", iso(since)))
            for row in self._pages(self._ao, ENTRY, q):
                groups.setdefault(row["transaction_id"], []).append(Entry.from_row(row))
        known: set[str] = set()
        unsealed: set[str] = set()
        self._sealed(groups, known, unsealed)
        out = []
        for tid in unsealed & set(groups):
            lines = tuple(sorted(groups[tid], key=lambda e: e.line))
            oldest = min(lines, key=lambda e: parse_ts(e.posted_at)).posted_at
            out.append(OrphanGroup(tid, lines, oldest))
        return sorted(out, key=lambda g: (parse_ts(g.oldest_posted_at), g.transaction_id))
