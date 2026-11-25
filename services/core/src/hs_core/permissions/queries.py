"""Appwrite JSON query strings (wire format) and cursor paging. Stdlib only (no SDK import) so
the permission code and the drift script run anywhere the core service does."""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

PAGE = 100


def _q(method: str, attribute: str | None = None, values: list | None = None) -> str:
    d: dict[str, Any] = {"method": method}
    if attribute is not None:
        d["attribute"] = attribute
    d["values"] = values or []
    return json.dumps(d)


def equal(attr: str, values: list) -> str:
    return _q("equal", attr, list(values))


def less_than_equal(attr: str, value: Any) -> str:
    return _q("lessThanEqual", attr, [value])


def greater_than_equal(attr: str, value: Any) -> str:
    return _q("greaterThanEqual", attr, [value])


def limit(n: int) -> str:
    return _q("limit", None, [n])


def order_asc(attr: str) -> str:
    return _q("orderAsc", attr)


def cursor_after(row_id: str) -> str:
    return _q("cursorAfter", None, [row_id])


def iter_rows(
    repo, table: str, queries: list[str] | None = None, page: int = PAGE
) -> Iterator[dict]:
    """Every matching row, by cursor (never offset: Appwrite caps totals, ADR 0004/0012)."""
    after: str | None = None
    while True:
        q = [*(queries or []), order_asc("$id"), limit(page)]
        if after:
            q.append(cursor_after(after))
        rows = repo.list(table, q)
        yield from rows
        if len(rows) < page:
            return
        after = rows[-1]["$id"]


def rows_where(repo, table: str, column: str, values: list) -> list[dict]:
    return list(iter_rows(repo, table, [equal(column, values)]))
