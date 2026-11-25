"""Repository layer over Appwrite TablesDB. Tests use InMemoryRepository."""

from __future__ import annotations

import copy
from typing import Any, Protocol

from .settings import Settings


class RepositoryError(Exception): ...


class NotFound(RepositoryError): ...


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


class InMemoryRepository:
    """Fake used by all tests and by dev when Appwrite is not configured."""

    def __init__(self) -> None:
        self.available = True
        self._t: dict[str, dict[str, dict]] = {}

    def _up(self) -> None:
        if not self.available:
            raise RepositoryUnavailable("in-memory repository marked unavailable")

    def ping(self) -> None:
        self._up()

    def get(self, table, row_id):
        self._up()
        try:
            return copy.deepcopy(self._t[table][row_id])
        except KeyError:
            raise NotFound(f"{table}/{row_id}") from None

    def list(self, table, queries=None):
        self._up()
        return [copy.deepcopy(r) for r in self._t.get(table, {}).values()]

    def create(self, table, row_id, data, permissions=None):
        self._up()
        rows = self._t.setdefault(table, {})
        if row_id in rows:
            raise ValueError(f"duplicate {table}/{row_id}")
        rows[row_id] = {**copy.deepcopy(data), "$id": row_id, "$permissions": permissions or []}
        return copy.deepcopy(rows[row_id])

    def update(self, table, row_id, data, permissions=None):
        self._up()
        row = self._t.get(table, {}).get(row_id)
        if row is None:
            raise NotFound(f"{table}/{row_id}")
        row.update(copy.deepcopy(data))
        if permissions is not None:
            row["$permissions"] = permissions
        return copy.deepcopy(row)

    def delete(self, table, row_id):
        self._up()
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


def build_repository(settings: Settings) -> Repository:
    """Real adapter when Appwrite is configured, else the in-memory fake (dev only)."""
    if settings.appwrite_endpoint and settings.appwrite_api_key.get_secret_value():
        from appwrite.client import Client

        client = (
            Client()
            .set_endpoint(settings.appwrite_endpoint)
            .set_project(settings.appwrite_project_id)
            .set_key(settings.appwrite_api_key.get_secret_value())
        )
        return AppwriteRepository(client, settings.appwrite_database_id)
    return InMemoryRepository()
