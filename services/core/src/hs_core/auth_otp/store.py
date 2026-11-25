"""Storage of challenges, attempt slots, the single-use claim and rate-limit slots.

Appwrite has no conditional update and no transactions (ADR 0002, 0012), so every counter is a set
of *slot rows* whose IDs are derived from (owner, n): creating the same ID twice is a 409, which the
repository raises as `Conflict`. Concurrent callers therefore claim distinct slots or lose, and a
code can be consumed by exactly one caller (the same idiom as the job runner's run claim).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from appwrite.query import Query

from ..jobs.idempotency import row_id
from ..repository import Conflict, NotFound, Repository

log = logging.getLogger("hs_core.auth_otp.store")
TABLE = "otp_challenge"


def iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat()


def parse(value: str) -> datetime:
    return datetime.fromisoformat(value).astimezone(UTC)


@dataclass(frozen=True)
class Slot:
    row_id: str
    n: int


@dataclass(frozen=True)
class Challenge:
    id: str
    code_hash: str
    salt: str
    app: str
    created_at: datetime
    expires_at: datetime


class OtpStore:
    def __init__(self, repo: Repository) -> None:
        self.repo = repo

    # -- challenges ------------------------------------------------------------------------
    def create_challenge(
        self,
        *,
        challenge_id: str,
        phone_hash: str,
        code_hash: str,
        salt: str,
        app: str,
        now: datetime,
        expires_at: datetime,
    ) -> Challenge:
        cid = challenge_id
        self.repo.create(
            TABLE,
            cid,
            {
                "kind": "challenge",
                "challenge_id": cid,
                "phone_hash": phone_hash,
                "code_hash": code_hash,
                "salt": salt,
                "app": app,
                "created_at": iso(now),
                "expires_at": iso(expires_at),
            },
        )
        return Challenge(cid, code_hash, salt, app, now, expires_at)

    def delete_challenge(self, challenge_id: str) -> None:
        try:
            self.repo.delete(TABLE, challenge_id)
        except NotFound:
            pass

    def latest_challenge(self, phone_hash: str) -> Challenge | None:
        rows = self.repo.list(
            TABLE,
            [
                Query.equal("phone_hash", [phone_hash]),
                Query.equal("kind", ["challenge"]),
                Query.order_desc("created_at"),
                Query.limit(1),
            ],
        )
        if not rows:
            return None
        r = rows[0]
        return Challenge(
            r["$id"],
            r["code_hash"],
            r["salt"],
            r.get("app") or "",
            parse(r["created_at"]),
            parse(r["expires_at"]),
        )

    # -- slots (attempt counters, rate limits) -------------------------------------------------
    def used(self, bucket: str, limit: int) -> int:
        """Rows in the bucket, capped at `limit` (we never need more)."""
        return len(self.repo.list(TABLE, [Query.equal("bucket", [bucket]), Query.limit(limit)]))

    def claim_slot(
        self, bucket: str, limit: int, now: datetime, expires_at: datetime, kind: str = "bucket"
    ) -> Slot | None:
        """Take one of `limit` slots; None when the bucket is full. Atomic per slot ID."""
        taken = {
            r["$id"]
            for r in self.repo.list(TABLE, [Query.equal("bucket", [bucket]), Query.limit(limit)])
        }
        if len(taken) >= limit:
            return None
        for n in range(1, limit + 1):
            rid = row_id("otp_slot", bucket, n)
            if rid in taken:
                continue
            try:
                self.repo.create(
                    TABLE,
                    rid,
                    {
                        "kind": kind,
                        "bucket": bucket,
                        "created_at": iso(now),
                        "expires_at": iso(expires_at),
                    },
                )
            except Conflict:
                continue  # a concurrent caller took this slot; try the next one
            return Slot(rid, n)
        return None

    def release(self, slots: list[Slot]) -> None:
        for s in slots:
            try:
                self.repo.delete(TABLE, s.row_id)
            except NotFound:
                pass
            except Exception:  # noqa: BLE001 - best effort: a leaked slot only costs quota
                log.warning("could not release an otp slot")

    # -- single-use claim ----------------------------------------------------------------------
    def claim_consume(self, challenge: Challenge, now: datetime) -> bool:
        """Create the one-and-only consume row of this challenge. False if already consumed."""
        try:
            self.repo.create(
                TABLE,
                row_id("otp_consume", challenge.id),
                {
                    "kind": "consume",
                    "challenge_id": challenge.id,
                    "created_at": iso(now),
                    "expires_at": iso(challenge.expires_at),
                },
            )
        except Conflict:
            return False
        return True

    def release_consume(self, challenge: Challenge) -> None:
        try:
            self.repo.delete(TABLE, row_id("otp_consume", challenge.id))
        except NotFound:
            pass
