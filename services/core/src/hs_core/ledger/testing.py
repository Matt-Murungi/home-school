"""Test doubles for the ledger (offline). Shipped in the package like `InMemoryRepository`.

* `PagingRepository`: an `InMemoryRepository` that behaves like Appwrite where the ledger relies
  on it: default page size 25, hard cap 5,000 per request, `cursorAfter`, unique indexes (409).
* `FaultyRepository`: wraps a repository and "crashes" at the Nth write, either BEFORE the write
  reaches the store or AFTER it was applied (response lost), to prove the write protocol.
* `FakeAuditSink`: records audit events, de-duplicated by key like M06's deterministic row ids.
"""

from __future__ import annotations

import copy
import json
import threading
from typing import Any

from ..repository import (
    Conflict,
    InMemoryRepository,
    NotFound,
    RepositoryUnavailable,
    _apply_queries,
)
from .audit import AuditEvent

APPWRITE_DEFAULT_LIMIT = 25
APPWRITE_MAX_LIMIT = 5000

# (table -> list of unique column tuples) as declared by appwrite/schema/money.json (M04)
LEDGER_UNIQUES: dict[str, list[tuple[str, ...]]] = {
    "ledger_account": [("code",)],
    "ledger_transaction": [("idempotency_key",), ("reverses_transaction_id",)],
    "ledger_entry": [("transaction_id", "line")],
}


class PagingRepository(InMemoryRepository):
    def __init__(self, uniques: dict[str, list[tuple[str, ...]]] | None = None) -> None:
        super().__init__()
        self.uniques = LEDGER_UNIQUES if uniques is None else uniques
        self._unique: dict[tuple[str, tuple[str, ...]], dict[tuple, str]] = {}
        self.list_calls: list[tuple[str, int, bool]] = []  # (table, rows returned, had cursor)
        self.max_page_seen = 0

    def create(self, table, row_id, data, permissions=None):
        with self._lock:
            claims = []
            for cols in self.uniques.get(table, []):
                vals = tuple(data.get(c) for c in cols)
                if any(v is None for v in vals):
                    continue
                owner = self._unique.setdefault((table, cols), {}).get(vals)
                if owner is not None and owner != row_id:
                    raise Conflict(f"unique index on {table}{cols}")
                claims.append(((table, cols), vals))
            row = super().create(table, row_id, data, permissions)  # 409 on a duplicate id
            for index, vals in claims:
                self._unique[index][vals] = row_id
            return row

    def list(self, table, queries=None):
        self._up()
        parsed = [json.loads(q) if isinstance(q, str) else q for q in (queries or [])]
        cursor = None
        limit = APPWRITE_DEFAULT_LIMIT
        rest = []
        for q in parsed:
            if q["method"] == "cursorAfter":
                cursor = q["values"][0]
            elif q["method"] == "limit":
                limit = q["values"][0]
            elif q["method"] == "offset":
                raise AssertionError("ledger code must page with cursors, not offsets")
            else:
                rest.append(q)
        if limit > APPWRITE_MAX_LIMIT:
            raise AssertionError(f"limit {limit} is above Appwrite's cap of {APPWRITE_MAX_LIMIT}")
        with self._lock:  # filter on the live rows, copy only the page that is returned
            live = self._t.get(table, {})
            if len(rest) == 1 and rest[0]["method"] == "equal" and rest[0]["attribute"] == "$id":
                rows = [live[i] for i in rest[0]["values"] if i in live]  # primary-key lookup
            else:
                rows = _apply_queries(list(live.values()), rest)
        if cursor is not None:
            ids = [r["$id"] for r in rows]
            if cursor not in ids:
                raise NotFound(f"cursor {cursor}")
            rows = rows[ids.index(cursor) + 1 :]
        out = copy.deepcopy(rows[:limit])
        self.max_page_seen = max(self.max_page_seen, len(out))
        self.list_calls.append((table, len(out), cursor is not None))
        return out


class CrashInjected(RepositoryUnavailable):
    """The injected failure (looks like an outage to the caller)."""


class FaultyRepository:
    """Delegates to `inner`; counts `create` calls (the only writes the ledger makes).

    `crash_at=N, mode="before"`: the Nth write raises before reaching the store.
    `crash_at=N, mode="after"`: the Nth write is applied, then raises (response lost).
    After the crash the repository keeps working (a restarted process); `writes` counts on."""

    def __init__(self, inner: InMemoryRepository) -> None:
        self.inner = inner
        self.writes = 0
        self.crash_at: int | None = None
        self.mode = "after"
        self.crashed = False
        self._lock = threading.Lock()

    def arm(self, crash_at: int | None, mode: str = "after") -> None:
        assert mode in ("before", "after")
        with self._lock:
            self.writes = 0
            self.crash_at = crash_at
            self.mode = mode
            self.crashed = False

    def create(self, table, row_id, data, permissions=None):
        with self._lock:
            self.writes += 1
            hit = self.crash_at is not None and self.writes == self.crash_at
            if hit:
                self.crashed = True
        if hit and self.mode == "before":
            raise CrashInjected(f"crash before write {self.crash_at}")
        result = self.inner.create(table, row_id, data, permissions)
        if hit:
            raise CrashInjected(f"crash after write {self.crash_at}")
        return result

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)


class FakeAuditSink:
    def __init__(self) -> None:
        self.events: dict[str, AuditEvent] = {}
        self.calls = 0
        self.fail_next = 0
        self._lock = threading.Lock()

    def record(self, event: AuditEvent) -> None:
        with self._lock:
            self.calls += 1
            if self.fail_next:
                self.fail_next -= 1
                raise RuntimeError("audit sink down")
            self.events.setdefault(event.key, event)
