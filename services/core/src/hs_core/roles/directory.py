"""The Appwrite user side the role service needs (labels, status), behind one protocol.

`FakeUserDirectory` / `AppwriteUserDirectory` (in `auth_otp/accounts.py`, A01) implement it: one
object serves the phone sign-in and the role service, so both see the same labels.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol


class DirectoryConflict(Exception):  # noqa: N818
    """A user with this phone (or ID) already exists."""


class DirectoryUnavailable(Exception):  # noqa: N818
    ...


@dataclass
class AuthUser:
    id: str
    phone: str
    labels: list[str] = field(default_factory=list)
    active: bool = True
    accessed_at: datetime | None = None


class UserDirectory(Protocol):
    def find_by_phone(self, phone: str) -> AuthUser | None: ...
    def create(self, user_id: str, phone: str) -> AuthUser: ...
    def add_label(self, user_id: str, label: str) -> AuthUser: ...
    # A03: the role service sets and removes labels through the same directory.
    def get(self, user_id: str) -> AuthUser | None: ...
    def remove_label(self, user_id: str, label: str) -> AuthUser: ...
    def end_sessions(self, user_id: str) -> None: ...
    def labelled(self, label: str, after: str | None, limit: int) -> list[AuthUser]: ...
    def create_token(self, user_id: str, ttl_s: int) -> Any: ...
