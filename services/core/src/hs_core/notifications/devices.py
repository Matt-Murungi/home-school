"""`device_token` handling, following M04's upsert rule (platform.json note):

look up by `token_hash` (sha256 of the token); no row -> insert; the same user again -> clear
`revoked_at`, set `last_seen_at`; a different user (shared device) -> move the row to the new
user, rewrite its read permission, clear `revoked_at`; never two live rows for one token. Logout
revokes (only if the row still belongs to that user). Tokens are secrets: never logged, never
listed to admins (per-row read permission for the owner only).
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from appwrite.permission import Permission
from appwrite.query import Query
from appwrite.role import Role

from ..jobs.idempotency import row_id
from ..repository import Conflict, NotFound, Repository

log = logging.getLogger("hs_core.notifications.devices")
TABLE = "device_token"
APPS = ("parent", "teacher", "admin")
PLATFORMS = ("android", "ios", "web")


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@dataclass(frozen=True)
class DeviceToken:
    row_id: str
    user_id: str
    token: str
    platform: str

    def __repr__(self) -> str:
        return f"DeviceToken(row={self.row_id})"


class DeviceRegistry:
    def __init__(self, repo: Repository, clock: Callable[[], datetime] | None = None) -> None:
        self.repo = repo
        self.clock = clock or (lambda: datetime.now(UTC))

    def register(self, user_id: str, app: str, platform: str, token: str) -> None:
        if not user_id or not 0 < len(user_id) <= 36:
            raise ValueError("user id required")
        if app not in APPS or platform not in PLATFORMS:
            raise ValueError("unknown app or platform")
        if not token or len(token) > 1024:
            raise ValueError("token must be 1-1024 characters")
        rid = row_id(TABLE, token_hash(token))
        now = self.clock().isoformat()
        perms = [Permission.read(Role.user(user_id))]
        for _ in range(5):
            try:
                self.repo.get(TABLE, rid)
            except NotFound:
                try:
                    self.repo.create(
                        TABLE,
                        rid,
                        {
                            "user_id": user_id,
                            "app": app,
                            "platform": platform,
                            "token_hash": token_hash(token),
                            "token": token,
                            "last_seen_at": now,
                            "revoked_at": None,
                        },
                        perms,
                    )
                    log.info("device registered", extra={"user_id": user_id, "app": app})
                    return
                except Conflict:
                    continue  # a concurrent register won the insert: fall through to update
            self.repo.update(
                TABLE,
                rid,
                {
                    "user_id": user_id,
                    "app": app,
                    "platform": platform,
                    "token": token,
                    "last_seen_at": now,
                    "revoked_at": None,
                },
                perms,
            )
            log.info("device registered", extra={"user_id": user_id, "app": app})
            return
        raise RuntimeError("device registration kept conflicting")  # pragma: no cover

    def unregister(self, user_id: str, token: str) -> bool:
        """Logout. Does nothing when the token now belongs to someone else."""
        rid = row_id(TABLE, token_hash(token))
        try:
            row = self.repo.get(TABLE, rid)
        except NotFound:
            return False
        if row["user_id"] != user_id or row.get("revoked_at"):
            return False
        self.repo.update(TABLE, rid, {"revoked_at": self.clock().isoformat()})
        return True

    def revoke_invalid(self, rid: str) -> None:
        """Provider said 'unregistered': stop using the token."""
        try:
            self.repo.update(TABLE, rid, {"revoked_at": self.clock().isoformat()})
        except NotFound:
            pass
        log.info("device token revoked as unregistered", extra={"device_row": rid})

    def active_tokens(self, user_id: str) -> list[DeviceToken]:
        rows = self.repo.list(TABLE, [Query.equal("user_id", [user_id]), Query.limit(100)])
        return [
            DeviceToken(r["$id"], r["user_id"], r["token"], r["platform"])
            for r in rows
            if not r.get("revoked_at")
        ]
