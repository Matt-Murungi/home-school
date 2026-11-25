"""Appwrite permission strings: build, parse, canonicalise. No SDK import (stdlib only).

Format (Appwrite): `read("user:abc")`, `read("label:opsadmin")`, `read("users")`, `read("any")`.
Clients only ever hold `read` (ADR 0012 principle 2); this module cannot build anything else
for a client principal, and `parse` flags what it does not understand as forbidden.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

ACTIONS = ("read", "create", "update", "delete", "write")
_RE = re.compile(r'^(read|create|update|delete|write)\("([^"\\]*)"\)$')
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,35}$")


@dataclass(frozen=True)
class Permission:
    action: str
    role: str  # "user:<id>", "label:<x>", "users", "any", "guests", "team:...", "member:..."

    def __str__(self) -> str:
        return f'{self.action}("{self.role}")'

    @property
    def kind(self) -> str:
        return self.role.split(":", 1)[0] if ":" in self.role else self.role


def parse(s: str) -> Permission:
    m = _RE.match(s) if isinstance(s, str) else None
    if not m:
        raise ValueError(f"not a permission string: {s!r}")
    return Permission(m.group(1), m.group(2))


def user_id_ok(uid: object) -> bool:
    return isinstance(uid, str) and bool(_ID_RE.match(uid))


def read_user(uid: str) -> str:
    if not user_id_ok(uid):
        raise ValueError(f"bad user id for a permission: {uid!r}")
    return f'read("user:{uid}")'


def read_label(label: str) -> str:
    from .policy import is_label

    if not is_label(label):
        raise ValueError(f"unknown label: {label!r}")
    return f'read("label:{label}")'


READ_USERS = 'read("users")'


def canonical(perms: object) -> tuple[str, ...]:
    """Sorted, de-duplicated tuple (order never matters to Appwrite)."""
    return tuple(sorted(set(perms or ())))  # type: ignore[arg-type]


def forbidden_in(perms: object, *, allow_users_read: bool = False) -> list[str]:
    """Entries a row/file of the permission model must never carry: `any`; `users` unless the
    row is explicitly published catalogue content; any create/update/delete/write (clients hold
    read only); unparsable strings; roles other than user:/label:."""
    bad: list[str] = []
    for p in perms or ():
        try:
            parsed = parse(p)
        except ValueError:
            bad.append(str(p))
            continue
        if parsed.action != "read":
            bad.append(p)
        elif parsed.role == "any" or parsed.role == "guests":
            bad.append(p)
        elif parsed.role == "users":
            if not allow_users_read:
                bad.append(p)
        elif parsed.kind not in ("user", "label"):
            bad.append(p)
    return bad
