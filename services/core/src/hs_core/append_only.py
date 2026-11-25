"""Insert-only access to the append-only tables (M04 #39, SEC-6, SR 7.10, ADR 0012).

Appwrite cannot revoke update/delete from the server API key, so the guarantee is
made here: code that writes ledger_entry, ledger_transaction, audit_log or
safeguarding_action holds an `AppendOnlyRepository`, which has create/get/list only
(update/delete raise). `find_append_only_violations` is the CI-style scan used by
tests: it fails when any module calls update/delete (or the raw SDK
update_row/delete_row) with one of these tables as an argument. The set must equal
the tables marked `append_only` in appwrite/schema/*.json (checked by a test).
Limit: a call whose table name is computed at run time cannot be seen by the scan;
the wrapper is the runtime guard.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

from .repository import Repository

APPEND_ONLY_TABLES = frozenset(
    {"ledger_entry", "ledger_transaction", "audit_log", "safeguarding_action"}
)
_WRITE_METHODS = frozenset({"update", "delete", "update_row", "delete_row"})


class AppendOnlyViolation(Exception):
    """update/delete attempted on an append-only table."""


class AppendOnlyRepository:
    def __init__(self, inner: Repository, tables: frozenset[str] = APPEND_ONLY_TABLES) -> None:
        self._inner = inner
        self._tables = tables

    def _check(self, table: str) -> None:
        if table not in self._tables:
            raise ValueError(f"{table} is not an append-only table; use the normal repository")

    def get(self, table: str, row_id: str) -> dict[str, Any]:
        self._check(table)
        return self._inner.get(table, row_id)

    def list(self, table: str, queries: list[str] | None = None) -> list[dict[str, Any]]:
        self._check(table)
        return self._inner.list(table, queries)

    def create(
        self, table: str, row_id: str, data: dict, permissions: list[str] | None = None
    ) -> dict[str, Any]:
        self._check(table)
        return self._inner.create(table, row_id, data, permissions)

    def update(self, table: str, *args: Any, **kwargs: Any) -> None:
        raise AppendOnlyViolation(f"{table} is append-only: update is not allowed")

    def delete(self, table: str, *args: Any, **kwargs: Any) -> None:
        raise AppendOnlyViolation(f"{table} is append-only: delete is not allowed")


def _string_constants(tree: ast.AST) -> dict[str, str]:
    """NAME = "literal" assignments, so `T = "ledger_entry"; repo.update(T, ...)` is seen."""
    out: dict[str, str] = {}
    for n in ast.walk(tree):
        if (
            isinstance(n, ast.Assign)
            and isinstance(n.value, ast.Constant)
            and isinstance(n.value.value, str)
        ):
            for t in n.targets:
                if isinstance(t, ast.Name):
                    out[t.id] = n.value.value
    return out


def find_append_only_violations(
    root: Path, tables: frozenset[str] = APPEND_ONLY_TABLES
) -> list[str]:
    found: list[str] = []
    for path in sorted(Path(root).rglob("*.py")):
        if path.name == "append_only.py":
            continue
        tree = ast.parse(path.read_text(), filename=str(path))
        consts = _string_constants(tree)
        for n in ast.walk(tree):
            if not (
                isinstance(n, ast.Call)
                and isinstance(n.func, ast.Attribute)
                and n.func.attr in _WRITE_METHODS
            ):
                continue
            for arg in [*n.args, *(k.value for k in n.keywords)]:
                name = None
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    name = arg.value
                elif isinstance(arg, ast.Name):
                    name = consts.get(arg.id)
                if name in tables:
                    found.append(f"{path}:{n.lineno}: {n.func.attr}() on append-only table {name}")
    return found
