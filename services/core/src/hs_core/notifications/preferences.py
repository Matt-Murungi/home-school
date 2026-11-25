"""User preferences: users can mute MARKETING only. Operational and safeguarding notifications
can never be muted (SR 8). `notification_preference` (M04) stores `marketing_muted`.

Per-channel opt-outs (e.g. no marketing email) exist in the logic and the in-memory store, but
the M04 table has no column for them, so `RepositoryPreferenceStore` refuses to pretend to
persist them (owner question / N01 follow-up: add `marketing_muted_channels`). They too apply to
marketing only.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from appwrite.permission import Permission
from appwrite.role import Role

from ..jobs.idempotency import row_id
from ..repository import Conflict, NotFound, Repository

TABLE = "notification_preference"


class NotPersistable(NotImplementedError):
    pass


@dataclass(frozen=True)
class Preferences:
    marketing_muted: bool = False
    muted_channels: frozenset[str] = frozenset()  # applies to marketing only


def blocked_reason(category: str, channel: str, prefs: Preferences) -> str | None:
    """Why preferences stop this notification, or None. Only marketing can be stopped."""
    if category != "marketing":
        return None
    if prefs.marketing_muted:
        return "marketing_muted"
    if channel in prefs.muted_channels:
        return "channel_opt_out"
    return None


class PreferenceStore(Protocol):
    def get(self, user_id: str) -> Preferences: ...

    def set_marketing_muted(self, user_id: str, muted: bool) -> None: ...


class RepositoryPreferenceStore:
    def __init__(self, repo: Repository, clock: Callable[[], datetime] | None = None) -> None:
        self.repo = repo
        self.clock = clock or (lambda: datetime.now(UTC))

    def get(self, user_id: str) -> Preferences:
        try:
            row = self.repo.get(TABLE, row_id(TABLE, user_id))
        except NotFound:
            return Preferences()
        return Preferences(marketing_muted=bool(row.get("marketing_muted")))

    def set_marketing_muted(self, user_id: str, muted: bool) -> None:
        rid = row_id(TABLE, user_id)
        data = {
            "user_id": user_id,
            "marketing_muted": muted,
            "updated_at": self.clock().isoformat(),
        }
        try:
            self.repo.update(TABLE, rid, data)
        except NotFound:
            try:
                self.repo.create(TABLE, rid, data, [Permission.read(Role.user(user_id))])
            except Conflict:
                self.repo.update(TABLE, rid, data)

    def set_muted_channels(self, user_id: str, channels: set[str]) -> None:
        raise NotPersistable("per-channel opt-out needs a notification_preference column")


class FakePreferenceStore:
    def __init__(self) -> None:
        self._p: dict[str, Preferences] = {}
        self._lock = threading.Lock()

    def get(self, user_id: str) -> Preferences:
        return self._p.get(user_id, Preferences())

    def set_marketing_muted(self, user_id: str, muted: bool) -> None:
        with self._lock:
            self._p[user_id] = Preferences(muted, self.get(user_id).muted_channels)

    def set_muted_channels(self, user_id: str, channels: set[str]) -> None:
        with self._lock:
            self._p[user_id] = Preferences(self.get(user_id).marketing_muted, frozenset(channels))
