"""Repository layer over Appwrite TablesDB. Tests use InMemoryRepository."""

from __future__ import annotations

import copy
import json
import threading
from typing import Any, Protocol

from .settings import ConfigError, Settings


class RepositoryError(Exception): ...


class NotFound(RepositoryError): ...


class Conflict(RepositoryError, ValueError):
    """A row with this ID already exists (Appwrite 409). Creating with a deterministic ID is the
    atomic claim the job runner relies on, so this must stay distinguishable from an outage."""


class RepositoryUnavailable(RepositoryError): ...


class Repository(Protocol):
    def ping(self) -> None: ...
    def get(self, table: str, row_id: str) -> dict[str, Any]: ...
    def list(self, table: str, queries: list[str] | None = None) -> list[dict[str, Any]]: ...
    def create(
        self, table: str, row_id: str, data: dict, permissions: list[str] | None = None
    ) -> dict[str, Any]: ...
    def update(
        self, table: str, row_id: str, data: dict, permissions: list[str] | None = None
    ) -> dict[str, Any]: ...
    def delete(self, table: str, row_id: str) -> None: ...


def _apply_queries(rows: list[dict], queries: list[str] | None) -> list[dict]:
    """Interpret the subset of Appwrite JSON queries the core service uses (fake only)."""
    parsed = [json.loads(q) if isinstance(q, str) else q for q in (queries or [])]
    cmp = {
        "equal": lambda v, vals: v in vals,
        "notEqual": lambda v, vals: v not in vals,
        "lessThan": lambda v, vals: v < vals[0],
        "lessThanEqual": lambda v, vals: v <= vals[0],
        "greaterThan": lambda v, vals: v > vals[0],
        "greaterThanEqual": lambda v, vals: v >= vals[0],
    }
    orders: list[tuple[str, bool]] = []
    limit: int | None = None
    offset = 0
    cursor_after: str | None = None
    for q in parsed:
        m = q["method"]
        if m in cmp:
            attr, vals = q["attribute"], q["values"]
            # Appwrite never matches a null attribute with a comparison.
            rows = [r for r in rows if r.get(attr) is not None and cmp[m](r[attr], vals)]
        elif m in ("orderAsc", "orderDesc"):
            orders.append((q["attribute"], m == "orderDesc"))
        elif m == "limit":
            limit = q["values"][0]
        elif m == "offset":
            offset = q["values"][0]
        elif m == "cursorAfter":
            cursor_after = q["values"][0]
        else:
            raise ValueError(f"unsupported query method in fake repository: {m}")
    for attr, desc in reversed(orders):
        rows = sorted(rows, key=lambda r, a=attr: (r.get(a) is None, r.get(a)), reverse=desc)
    if cursor_after is not None:  # Appwrite: rows after the row with this id, in query order
        ids = [r.get("$id") for r in rows]
        if cursor_after not in ids:
            raise ValueError("cursor row not found")
        rows = rows[ids.index(cursor_after) + 1 :]
    rows = rows[offset:]
    return rows[:limit] if limit is not None else rows


class InMemoryRepository:
    """Fake used by all tests and by dev when Appwrite is not configured. Thread-safe; create is
    atomic per row ID like Appwrite's (409 on duplicate)."""

    def __init__(self) -> None:
        self.available = True
        self._t: dict[str, dict[str, dict]] = {}
        self._lock = threading.RLock()

    def _up(self) -> None:
        if not self.available:
            raise RepositoryUnavailable("in-memory repository marked unavailable")

    def ping(self) -> None:
        self._up()

    def get(self, table, row_id):
        self._up()
        with self._lock:
            try:
                return copy.deepcopy(self._t[table][row_id])
            except KeyError:
                raise NotFound(f"{table}/{row_id}") from None

    def list(self, table, queries=None):
        self._up()
        with self._lock:
            rows = [copy.deepcopy(r) for r in self._t.get(table, {}).values()]
        return _apply_queries(rows, queries)

    def create(self, table, row_id, data, permissions=None):
        self._up()
        with self._lock:
            rows = self._t.setdefault(table, {})
            if row_id in rows:
                raise Conflict(f"duplicate {table}/{row_id}")
            rows[row_id] = {**copy.deepcopy(data), "$id": row_id, "$permissions": permissions or []}
            return copy.deepcopy(rows[row_id])

    def update(self, table, row_id, data, permissions=None):
        self._up()
        with self._lock:
            row = self._t.get(table, {}).get(row_id)
            if row is None:
                raise NotFound(f"{table}/{row_id}")
            row.update(copy.deepcopy(data))
            if permissions is not None:
                row["$permissions"] = permissions
            return copy.deepcopy(row)

    def delete(self, table, row_id):
        self._up()
        with self._lock:
            if self._t.get(table, {}).pop(row_id, None) is None:
                raise NotFound(f"{table}/{row_id}")


def _plain(obj: Any) -> dict[str, Any]:
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, "to_dict"):
        return obj.to_dict()
    return dict(obj)


class AppwriteRepository:
    """Thin adapter over the Appwrite server SDK (API key). Exercised only with mocks in tests."""

    def __init__(self, client: Any, database_id: str, tables: Any | None = None) -> None:
        self._client = client
        self._db = database_id
        if tables is None:
            from appwrite.services.tables_db import TablesDB

            tables = TablesDB(client)
        self._tables = tables

    def _wrap(self, fn, *a, **kw):
        from appwrite.exception import AppwriteException

        try:
            return fn(*a, **kw)
        except AppwriteException as e:
            if e.code == 404:
                raise NotFound(str(e.message)) from None
            if e.code == 409:
                raise Conflict(str(e.message)) from None
            raise RepositoryUnavailable(f"appwrite error {e.code}") from None
        except OSError as e:
            raise RepositoryUnavailable(type(e).__name__) from None

    def ping(self) -> None:
        self._wrap(self._client.call, "get", "/health")

    def get(self, table, row_id):
        return _plain(
            self._wrap(self._tables.get_row, database_id=self._db, table_id=table, row_id=row_id)
        )

    def list(self, table, queries=None):
        res = self._wrap(
            self._tables.list_rows, database_id=self._db, table_id=table, queries=queries
        )
        rows = res["rows"] if isinstance(res, dict) else res.rows
        return [_plain(r) for r in rows]

    def create(self, table, row_id, data, permissions=None):
        return _plain(
            self._wrap(
                self._tables.create_row,
                database_id=self._db,
                table_id=table,
                row_id=row_id,
                data=data,
                permissions=permissions,
            )
        )

    def update(self, table, row_id, data, permissions=None):
        return _plain(
            self._wrap(
                self._tables.update_row,
                database_id=self._db,
                table_id=table,
                row_id=row_id,
                data=data,
                permissions=permissions,
            )
        )

    def delete(self, table, row_id):
        self._wrap(self._tables.delete_row, database_id=self._db, table_id=table, row_id=row_id)


class _TimeoutRequests:
    """Stands in for the `requests` module inside `appwrite.client`: the SDK's Client.call has no
    timeout, so a blackholed Appwrite would hang the caller (and /ready) forever."""

    def __init__(self, real: Any, timeout: tuple[float, float]) -> None:
        self._real = real
        self.timeout = timeout

    def request(self, **kwargs):
        kwargs.setdefault("timeout", self.timeout)
        return self._real.request(**kwargs)

    def __getattr__(self, name: str):
        return getattr(self._real, name)


def install_request_timeout(timeout: tuple[float, float] = (3.05, 10.0)) -> None:
    """Give every SDK HTTP call a (connect, read) timeout. Idempotent; call before building."""
    import appwrite.client as ac

    current = ac.requests
    if isinstance(current, _TimeoutRequests):
        current.timeout = timeout
    else:
        ac.requests = _TimeoutRequests(current, timeout)


def build_repository(settings: Settings) -> Repository:
    """Real adapter when Appwrite is configured. The in-memory fake is for dev only: outside dev a
    missing configuration is an error, never a silent fallback."""
    if settings.appwrite_endpoint and settings.appwrite_api_key.get_secret_value():
        from appwrite.client import Client

        install_request_timeout(
            (settings.appwrite_connect_timeout_s, settings.appwrite_read_timeout_s)
        )
        client = (
            Client()
            .set_endpoint(settings.appwrite_endpoint)
            .set_project(settings.appwrite_project_id)
            .set_key(settings.appwrite_api_key.get_secret_value())
        )
        return AppwriteRepository(client, settings.appwrite_database_id)
    if settings.hs_env != "dev":
        raise ConfigError(
            "Appwrite is not configured; refusing the in-memory repository outside dev"
        )
    return InMemoryRepository()
