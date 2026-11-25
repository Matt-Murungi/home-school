"""Row-based per-teacher lease.

ADR 0002 / F04 idiom: deterministic row id + Conflict + lease expiry + fencing.

Appwrite has no advisory locks and no conditional update. What it does have is an atomic claim:
creating a row with an id that exists answers 409 (`Conflict`). The lease therefore never updates a
shared row to take it over:

- one row per (teacher, generation); id and `lease_key` derive from both, so creating generation N+1
  is the claim;
- acquire: read the highest generation; busy while it is not released and not expired; otherwise
  create N+1 (409 = lost);
- fencing: a holder is valid only while its generation is still the highest for the teacher AND its
  lease has not expired. A stalled holder whose lease expired finds a higher generation and stops
  (`LeaseLost`) before writing;
- release sets `released_at` on the holder's own row only; a late release by a fenced-out holder
  changes nothing for the new holder (its row is a different one);
- holders delete rows older than the previous generation, so a teacher keeps one or two rows.

Clock skew between workers shifts expiry by the skew (keep the TTL well above it). The writer ALSO
re-checks after insert (`writer.py`), so a lease that is wrongly believed valid cannot produce a
lasting double booking.
"""

from __future__ import annotations

import contextlib
import logging
import random
import time
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from appwrite.query import Query

from ..jobs.idempotency import row_id
from ..repository import Conflict, NotFound, Repository
from . import rules

log = logging.getLogger("hs_core.scheduling.lease")
TABLE = "teacher_schedule_lease"


class LeaseBusy(Exception):
    """Another worker holds the teacher's lease; nothing was written."""


class LeaseLost(Exception):
    """This worker's lease expired or was taken over: it must not write."""


@dataclass
class Lease:
    teacher_id: str
    generation: int
    holder: str
    expires_at: datetime
    _leases: TeacherLeases

    def check(self, remote: bool = False) -> None:
        """Raise LeaseLost if the lease ran out (local clock) or, with remote=True, was superseded
        (one
        read)."""
        if self._leases.clock() >= self.expires_at - self._leases.margin:
            raise LeaseLost(f"lease {self.teacher_id}#{self.generation} expired")
        if remote:
            latest = self._leases._latest(self.teacher_id)
            if (
                latest is None
                or latest["generation"] != self.generation
                or latest.get("released_at")
            ):
                raise LeaseLost(f"lease {self.teacher_id}#{self.generation} superseded")


class TeacherLeases:
    def __init__(
        self,
        repo: Repository,
        clock: Callable[[], datetime] | None = None,
        ttl: timedelta = timedelta(seconds=30),
        wait: timedelta = timedelta(0),
        sleep: Callable[[float], None] = time.sleep,
        margin: timedelta = timedelta(milliseconds=500),
        holder: str | None = None,
    ) -> None:
        self.repo = repo
        self.clock = clock or (lambda: datetime.now(UTC))
        self.ttl = ttl
        self.margin = margin
        self._sleep = sleep
        self._wait = wait
        self.holder = holder or uuid.uuid4().hex[:16]

    # ------------------------------------------------------------------ reads
    def _latest(self, teacher_id: str) -> dict | None:
        rows = self.repo.list(
            TABLE,
            [Query.equal("teacher_id", teacher_id), Query.order_desc("generation"), Query.limit(1)],
        )
        return rows[0] if rows else None

    # ------------------------------------------------------------------ acquire / release
    def acquire(self, teacher_id: str) -> Lease:
        """One attempt: LeaseBusy if held, a Lease otherwise."""
        now = self.clock()
        latest = self._latest(teacher_id)
        if latest and not latest.get("released_at") and rules.parse_utc(latest["expires_at"]) > now:
            raise LeaseBusy(f"teacher {teacher_id} is being scheduled by another worker")
        generation = (latest["generation"] if latest else 0) + 1
        expires = now + self.ttl
        try:
            self.repo.create(
                TABLE,
                row_id("teacher_lease", teacher_id, generation),
                {
                    "teacher_id": teacher_id,
                    "generation": generation,
                    "holder": self.holder,
                    "acquired_at": rules.iso_utc(now),
                    "expires_at": rules.iso_utc(expires),
                    "lease_key": f"{teacher_id}:{generation}",
                },
            )
        except Conflict:
            raise LeaseBusy(
                f"teacher {teacher_id}: lost the claim for generation {generation}"
            ) from None
        self._prune(teacher_id, generation)
        return Lease(teacher_id, generation, self.holder, expires, self)

    def _prune(self, teacher_id: str, generation: int) -> None:
        if generation < 3:
            return
        old = self.repo.list(
            TABLE,
            [Query.equal("teacher_id", teacher_id), Query.less_than("generation", generation - 1)],
        )
        for r in old:
            with contextlib.suppress(NotFound):
                self.repo.delete(TABLE, r["$id"])

    def release(self, lease: Lease) -> None:
        """Mark the holder's own row released. Never raises (a failed release only means the lease
        expires on its own)."""
        try:
            self.repo.update(
                TABLE,
                row_id("teacher_lease", lease.teacher_id, lease.generation),
                {"released_at": rules.iso_utc(self.clock())},
            )
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "lease release failed",
                extra={"teacher_id": lease.teacher_id, "error": type(exc).__name__},
            )

    @contextlib.contextmanager
    def hold(self, teacher_id: str) -> Iterator[Lease | None]:
        """Acquire (waiting up to `wait` in total, summing the requested sleeps), yield, always
        release."""
        waited = timedelta(0)
        while True:
            try:
                lease = self.acquire(teacher_id)
                break
            except LeaseBusy:
                if waited >= self._wait:
                    raise
                delay = min(0.02 + random.random() * 0.05, (self._wait - waited).total_seconds())
                self._sleep(delay)
                waited += timedelta(seconds=delay)
        try:
            yield lease
        finally:
            self.release(lease)
