"""Job definitions: handlers return Done or Continue; JobSpec + Schedule describe a job."""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

KAMPALA = ZoneInfo("Africa/Kampala")


@dataclass(frozen=True)
class Done:
    detail: str = ""
    items: int = 0  # items processed by this chunk, recorded on the run


@dataclass(frozen=True)
class Continue:
    """Chunk finished, more work remains. `cursor` is passed to the next chunk."""

    cursor: Any
    items: int = 0


@dataclass(frozen=True)
class JobContext:
    name: str
    run_id: str
    dedupe_key: str
    scheduled_for: datetime | None
    payload: dict[str, Any]
    cursor: Any
    chunk: int
    # Set when the attempt times out; long handlers should poll it and stop.
    cancelled: threading.Event | None = None

    @property
    def effect_key(self) -> str:
        """Idempotency key for side effects of this chunk (payout request, email, ledger post)."""
        return f"{self.dedupe_key}#chunk{self.chunk}"


Handler = Callable[[JobContext], "Done | Continue"]


@dataclass(frozen=True)
class Schedule:
    """Every `every`, aligned to `anchor` (Kampala midnight 1 Jan 2026 by default, so daily jobs
    run at Kampala midnight; give a different anchor for e.g. 02:00)."""

    every: timedelta
    anchor: datetime = datetime(2026, 1, 1, tzinfo=KAMPALA)

    def due_periods(self, since: datetime, now: datetime) -> list[datetime]:
        """Period starts p (UTC) with since <= p <= now."""
        n = max(0, -((self.anchor - since) // self.every))  # ceil((since - anchor) / every)
        out, p = [], self.anchor + self.every * n
        while p <= now:
            out.append(p.astimezone(UTC))
            p += self.every
        return out


@dataclass(frozen=True)
class JobSpec:
    name: str
    handler: Handler
    schedule: Schedule | None = None
    max_attempts: int = 5
    backoff_base: timedelta = timedelta(minutes=1)
    # How far back a period may still be created; bounds catch-up after a long outage.
    catch_up: timedelta = timedelta(days=2)
    # One attempt longer than this is failed and retried (the thread cannot be killed: handlers
    # must be idempotent by effect_key and should poll ctx.cancelled).
    timeout: timedelta = timedelta(seconds=10)
