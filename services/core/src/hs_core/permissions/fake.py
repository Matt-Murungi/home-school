"""A permission-aware fake of Appwrite TablesDB and Storage, for tests that ACT AS each role.

`PermissionAwareRepository` is the F03 `InMemoryRepository` (the server view: API key, bypasses
permissions) plus `as_principal(...)`, a client view that evaluates `$permissions` the way
Appwrite does (as assumed here, see docs/ops/permissions.md "Unverified assumptions"):

- a row is readable when a table-level OR (if rowSecurity) a row-level `read` matches the caller;
- roles: `any` everyone, `users` any signed-in user, `guests` nobody signed in, `user:<id>`,
  `label:<x>` exact match on the user's labels (case sensitive), `team:`/`member:` unsupported;
- `write` means create + update + delete; create is checked on the TABLE only;
- an unreadable row is `NotFound` on get and silently absent from list.

Only the in-process model is checked here; that Appwrite really behaves this way is owner-blocked
(a throwaway project is needed).
"""

from __future__ import annotations

import copy
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..repository import InMemoryRepository, NotFound, _apply_queries
from .strings import parse


class InvalidPermission(ValueError):
    """What Appwrite answers with 400: a malformed permission string or role id."""


_ROLE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,35}$")
_LABEL = re.compile(r"^[A-Za-z0-9]{1,36}$")


def validate_permissions(perms) -> None:
    """Reject what Appwrite rejects (as assumed, docs/ops/permissions.md): unparsable strings,
    unknown role kinds, ids over 36 characters, non-alphanumeric labels. `[]` is valid."""
    for s in perms or ():
        try:
            p = parse(s)
        except ValueError:
            raise InvalidPermission(f"invalid permission string: {s!r}") from None
        kind, _, value = p.role.partition(":")
        if p.role in ("any", "users", "guests"):
            continue
        ok = {
            "user": _ROLE_ID,
            "team": _ROLE_ID,
            "member": _ROLE_ID,
            "label": _LABEL,
        }.get(kind)
        if ok is None or not ok.match(value.split("/")[0]):
            raise InvalidPermission(f"invalid role in permission: {s!r}")


class PermissionDenied(Exception):
    """A client write or read the permissions do not allow (Appwrite 401)."""


@dataclass(frozen=True)
class Principal:
    user_id: str | None
    labels: frozenset[str] = field(default_factory=frozenset)

    @staticmethod
    def of(user_id: str | None, labels: list[str] | None = None) -> Principal:
        # Labels are taken as the auth service reports them: exact strings, nothing normalised.
        return Principal(user_id, frozenset(labels or []))


def matches(role: str, p: Principal) -> bool:
    if role == "any":
        return True
    if role == "users":
        return p.user_id is not None
    if role == "guests":
        return p.user_id is None
    kind, _, value = role.partition(":")
    if kind == "user":
        return p.user_id is not None and value == p.user_id
    if kind == "label":
        return value in p.labels
    return False  # team:/member: are not used by this project


def allows(perms: list[str] | tuple[str, ...], action: str, p: Principal) -> bool:
    for s in perms or ():
        try:
            perm = parse(s)
        except ValueError:
            continue
        acts = ("create", "update", "delete") if perm.action == "write" else (perm.action,)
        if action in acts and matches(perm.role, p):
            return True
    return False


def _config_tables(root: Path | None = None) -> dict[str, dict]:
    root = root or Path(__file__).resolve().parents[5]
    out: dict[str, dict] = {}
    for f in sorted((root / "appwrite" / "schema").glob("*.json")):
        for t in json.loads(f.read_text())["tables"]:
            out[t["$id"]] = {
                "rowSecurity": t.get("rowSecurity", False),
                "perms": t.get("$permissions", []),
            }
    return out


def _config_buckets(root: Path | None = None) -> dict[str, dict]:
    root = root or Path(__file__).resolve().parents[5]
    cfg = json.loads((root / "appwrite" / "appwrite.config.json").read_text())
    return {
        b["$id"]: {"fileSecurity": b.get("fileSecurity", False), "perms": b.get("$permissions", [])}
        for b in cfg["buckets"]
    }


class PermissionAwareRepository(InMemoryRepository):
    """Server view = the base class. Client view = `as_principal`. Rows get `$createdAt` and
    `$updatedAt` (from `clock`) like Appwrite, so `$updatedAt` queries work."""

    def __init__(
        self,
        tables: dict[str, dict] | None = None,
        clock: Callable[[], datetime] | None = None,
        default_row_security: bool = True,
    ) -> None:
        super().__init__()
        self.table_cfg = tables if tables is not None else _config_tables()
        self.clock = clock or (lambda: datetime.now(UTC))
        self._default_rs = default_row_security
        self.writes: list[tuple[str, str, str]] = []  # (op, table, row_id) server writes, for tests

    def _stamp(self) -> str:
        return self.clock().astimezone(UTC).isoformat()

    def create(self, table, row_id, data, permissions=None):
        validate_permissions(permissions)
        super().create(table, row_id, data, permissions)
        with self._lock:
            stored = self._t[table][row_id]
            stored["$createdAt"] = stored["$updatedAt"] = self._stamp()
            self.writes.append(("create", table, row_id))
            return copy.deepcopy(stored)

    def update(self, table, row_id, data, permissions=None):
        validate_permissions(permissions)
        super().update(table, row_id, data, permissions)
        with self._lock:
            stored = self._t[table][row_id]
            stored["$updatedAt"] = self._stamp()
            self.writes.append(("update", table, row_id))
            return copy.deepcopy(stored)

    def delete(self, table, row_id):
        super().delete(table, row_id)
        self.writes.append(("delete", table, row_id))

    def list(self, table, queries=None):
        """Adds `cursorAfter` (Appwrite cursor paging) to the F03 fake."""
        self._up()
        parsed = [json.loads(q) if isinstance(q, str) else q for q in (queries or [])]
        cursor = next((q["values"][0] for q in parsed if q["method"] == "cursorAfter"), None)
        rest = [q for q in parsed if q["method"] != "cursorAfter"]
        if cursor is None:
            return super().list(table, [json.dumps(q) for q in rest])
        lim = next((q["values"][0] for q in rest if q["method"] == "limit"), None)
        no_limit = [q for q in rest if q["method"] != "limit"]
        with self._lock:
            rows = [copy.deepcopy(r) for r in self._t.get(table, {}).values()]
        rows = _apply_queries(rows, [json.dumps(q) for q in no_limit])
        ids = [r["$id"] for r in rows]
        start = ids.index(cursor) + 1 if cursor in ids else 0
        rows = rows[start:]
        return rows[:lim] if lim is not None else rows

    def _rs(self, table: str) -> tuple[bool, list[str]]:
        cfg = self.table_cfg.get(table)
        if cfg is None:
            return self._default_rs, []
        return cfg["rowSecurity"], cfg["perms"]

    def readable(self, table: str, row: dict, p: Principal) -> bool:
        row_security, table_perms = self._rs(table)
        if allows(table_perms, "read", p):
            return True
        return row_security and allows(row.get("$permissions", []), "read", p)

    def as_principal(self, principal: Principal) -> ClientView:
        return ClientView(self, principal)


class ClientView:
    """What a signed-in (or anonymous) client SDK can do with this project."""

    def __init__(self, repo: PermissionAwareRepository, p: Principal) -> None:
        self._repo, self.p = repo, p

    def get(self, table: str, row_id: str) -> dict[str, Any]:
        row = self._repo.get(table, row_id)
        if not self._repo.readable(table, row, self.p):
            raise NotFound(f"{table}/{row_id}")  # Appwrite does not reveal that it exists
        return row

    def list(self, table: str, queries: list[str] | None = None) -> list[dict[str, Any]]:
        rows = self._repo.list(table, queries)
        return [r for r in rows if self._repo.readable(table, r, self.p)]

    def can_read(self, table: str, row_id: str) -> bool:
        try:
            self.get(table, row_id)
            return True
        except NotFound:
            return False

    def readable_ids(self, table: str) -> set[str]:
        return {r["$id"] for r in self.list(table)}

    def create(self, table: str, row_id: str, data: dict, permissions: list[str] | None = None):
        _, table_perms = self._repo._rs(table)
        if not allows(table_perms, "create", self.p):
            raise PermissionDenied(f"create {table}")
        return self._repo.create(table, row_id, data, permissions)

    def update(self, table: str, row_id: str, data: dict, permissions: list[str] | None = None):
        row = self._repo.get(table, row_id)
        row_security, table_perms = self._repo._rs(table)
        if not (
            allows(table_perms, "update", self.p)
            or (row_security and allows(row.get("$permissions", []), "update", self.p))
        ):
            raise PermissionDenied(f"update {table}/{row_id}")
        return self._repo.update(table, row_id, data, permissions)

    def delete(self, table: str, row_id: str) -> None:
        row = self._repo.get(table, row_id)
        row_security, table_perms = self._repo._rs(table)
        if not (
            allows(table_perms, "delete", self.p)
            or (row_security and allows(row.get("$permissions", []), "delete", self.p))
        ):
            raise PermissionDenied(f"delete {table}/{row_id}")
        self._repo.delete(table, row_id)


# ------------------------------------------------------------------ storage
class FakeFileStore:
    """Fake of Appwrite Storage buckets: file id -> permissions, fileSecurity per bucket."""

    def __init__(self, buckets: dict[str, dict] | None = None) -> None:
        self.bucket_cfg = buckets if buckets is not None else _config_buckets()
        self._f: dict[str, dict[str, list[str]]] = {b: {} for b in self.bucket_cfg}
        self.writes: list[tuple[str, str]] = []

    def put(self, bucket: str, file_id: str, permissions: list[str] | tuple[str, ...]) -> None:
        validate_permissions(permissions)
        self._f[bucket][file_id] = list(permissions)

    def exists(self, bucket: str, file_id: str) -> bool:
        return file_id in self._f.get(bucket, {})

    def get_permissions(self, bucket: str, file_id: str) -> list[str]:
        try:
            return list(self._f[bucket][file_id])
        except KeyError:
            raise NotFound(f"{bucket}/{file_id}") from None

    def update_permissions(self, bucket: str, file_id: str, permissions: list[str]) -> None:
        if file_id not in self._f[bucket]:
            raise NotFound(f"{bucket}/{file_id}")
        validate_permissions(permissions)
        self._f[bucket][file_id] = list(permissions)
        self.writes.append((bucket, file_id))

    def list_file_ids(self, bucket: str) -> list[str]:
        return sorted(self._f[bucket])

    def can_read(self, bucket: str, file_id: str, p: Principal) -> bool:
        cfg = self.bucket_cfg[bucket]
        if allows(cfg["perms"], "read", p):
            return True
        return cfg["fileSecurity"] and allows(self._f[bucket].get(file_id, []), "read", p)


__all__ = [
    "InvalidPermission",
    "FakeFileStore",
    "PermissionAwareRepository",
    "PermissionDenied",
    "Principal",
    "allows",
    "matches",
]
