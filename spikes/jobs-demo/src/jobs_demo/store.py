"""Storage interface with Appwrite TablesDB semantics.

The runner relies on one Appwrite guarantee: creating a row with an ID that
already exists fails with 409 Conflict. That makes "create with a deterministic
ID" an atomic claim, which Appwrite otherwise lacks (there is no conditional
update). The in-memory store reproduces exactly that behaviour for tests; the
real core service implements the same protocol with the Appwrite server SDK.
"""
from __future__ import annotations

import copy
import hashlib
import threading
from typing import Any, Protocol


class Conflict(Exception):
    """A row with this ID already exists (Appwrite HTTP 409)."""


def row_id(*parts: object) -> str:
    """Deterministic Appwrite-safe row ID (<= 36 chars, [a-z0-9])."""
    return hashlib.sha256("\x1f".join(map(str, parts)).encode()).hexdigest()[:36]


class Store(Protocol):
    def create_row(self, table: str, rid: str, data: dict[str, Any]) -> dict[str, Any]: ...
    def get_row(self, table: str, rid: str) -> dict[str, Any] | None: ...
    def update_row(self, table: str, rid: str, data: dict[str, Any]) -> dict[str, Any]: ...
    def list_rows(self, table: str) -> list[dict[str, Any]]: ...


class MemoryStore:
    """Thread-safe stand-in for Appwrite TablesDB used by the tests."""

    def __init__(self) -> None:
        self._tables: dict[str, dict[str, dict[str, Any]]] = {}
        self._lock = threading.Lock()

    def create_row(self, table: str, rid: str, data: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            rows = self._tables.setdefault(table, {})
            if rid in rows:
                raise Conflict(f"{table}/{rid}")
            rows[rid] = {"$id": rid, **copy.deepcopy(data)}
            return copy.deepcopy(rows[rid])

    def get_row(self, table: str, rid: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._tables.get(table, {}).get(rid)
            return copy.deepcopy(row) if row else None

    def update_row(self, table: str, rid: str, data: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            row = self._tables[table][rid]
            row.update(copy.deepcopy(data))
            return copy.deepcopy(row)

    def list_rows(self, table: str) -> list[dict[str, Any]]:
        with self._lock:
            return [copy.deepcopy(r) for r in self._tables.get(table, {}).values()]
