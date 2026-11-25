"""Insert-only access to the append-only tables (M04 #39, SEC-6, SR 7.10, ADR 0012).

Appwrite cannot revoke update/delete from the server API key, so the protection is
two separate, partial things; the wrapper is used by the ledger service (hs_core.ledger)
and is opt-in elsewhere (audit_log and safeguarding_action are not wired yet).

1. `AppendOnlyRepository` is OPT-IN runtime protection: code that holds it can only
   create/get/list; update, delete and the bulk/upsert/increment variants raise.
   Nothing forces core code to hold it: M05+ repositories for these tables must be
   built only with the wrapper (a review rule, not enforced).
2. `find_append_only_violations` is a TRIPWIRE, not coverage. It parses src/hs_core
   and flags a call to a write-style method (WRITE_METHODS: update, delete,
   update_row(s), delete_row(s), upsert_row(s), increment/decrement_row_column,
   create_operations) when an argument is a string literal naming an append-only
   table, or a name bound anywhere in the same file (module-level or function-local
   NAME = "x" or NAME: str = "x") to one.
   It MISSES: table names that are computed (f-strings, concatenation, dynamic
   lookups), passed as a function parameter, held in a class attribute, tuple or
   other container constant, supplied through *args/**kwargs splats, reached via
   getattr(repo, "update"), a bound-method alias (u = repo.update; u("ledger_entry")),
   or dict dispatch. Constants are resolved per file, not per scope: a name reassigned
   later in the same file hides the earlier value (last assignment wins), and the
   same name bound in two scopes is conflated (two scopes, one value). It also cannot see write
   methods added to the SDK later; a test compares the list with the installed SDK.
The set of tables must equal the tables marked `append_only` in appwrite/schema/*.json
(checked by a test).
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

from .repository import Repository

APPEND_ONLY_TABLES = frozenset(
    {"ledger_entry", "ledger_transaction", "audit_log", "safeguarding_action"}
)
WRITE_METHODS = frozenset(
    {
        "update",
        "delete",
        "update_row",
        "update_rows",
        "delete_row",
        "delete_rows",
        "upsert_row",
        "upsert_rows",
        "increment_row_column",
        "decrement_row_column",
        "create_operations",
    }
)


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

    def _refuse(self, table: str, what: str) -> None:
        raise AppendOnlyViolation(f"{table} is append-only: {what} is not allowed")

    def update(self, table: str, *args: Any, **kwargs: Any) -> None:
        self._refuse(table, "update")

    def delete(self, table: str, *args: Any, **kwargs: Any) -> None:
        self._refuse(table, "delete")

    def update_rows(self, table: str, *args: Any, **kwargs: Any) -> None:
        self._refuse(table, "bulk update")

    def delete_rows(self, table: str, *args: Any, **kwargs: Any) -> None:
        self._refuse(table, "bulk delete")

    def upsert_row(self, table: str, *args: Any, **kwargs: Any) -> None:
        self._refuse(table, "upsert")

    def upsert_rows(self, table: str, *args: Any, **kwargs: Any) -> None:
        self._refuse(table, "bulk upsert")

    def increment_row_column(self, table: str, *args: Any, **kwargs: Any) -> None:
        self._refuse(table, "increment")

    def decrement_row_column(self, table: str, *args: Any, **kwargs: Any) -> None:
        self._refuse(table, "decrement")


def _string_constants(tree: ast.AST) -> dict[str, str]:
    """NAME = "literal" assignments, so `T = "ledger_entry"; repo.update(T, ...)` is seen."""
    out: dict[str, str] = {}
    for n in ast.walk(tree):
        value = getattr(n, "value", None)
        if not (isinstance(value, ast.Constant) and isinstance(value.value, str)):
            continue
        if isinstance(n, ast.Assign):
            targets = n.targets
        elif isinstance(n, ast.AnnAssign):
            targets = [n.target]
        else:
            continue
        for t in targets:
            if isinstance(t, ast.Name):
                out[t.id] = value.value
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
                and n.func.attr in WRITE_METHODS
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
