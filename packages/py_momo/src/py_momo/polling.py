"""Polling schedule helper (callbacks are never the only signal; SR 7.8)."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from .status import PaymentResult, Status

# seconds between polls: quick at first (parent is approving on their phone), then slower.
DEFAULT_DELAYS = (3, 3, 5, 5, 10, 10, 15, 15, 30, 30, 60, 60)


@dataclass(frozen=True)
class PollSchedule:
    delays: tuple[float, ...] = DEFAULT_DELAYS
    deadline_seconds: float = 300.0  # MTN pending requests expire; owner confirms exact TTL

    def delay_for(self, attempt: int) -> float:
        """attempt is 0-based; the last delay repeats."""
        return float(self.delays[min(attempt, len(self.delays) - 1)])


def poll_until_final(
    get_status: Callable[[str], PaymentResult],
    reference_id: str,
    schedule: PollSchedule = PollSchedule(),
    *,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> PaymentResult:
    """Poll until a final status or the deadline. At the deadline returns the last (PENDING)
    result: the caller must keep it pending and leave it to reconciliation, never mark failed."""
    start = clock()
    attempt = 0
    while True:
        result = get_status(reference_id)
        if result.status is not Status.PENDING:
            return result
        delay = schedule.delay_for(attempt)
        if clock() - start + delay > schedule.deadline_seconds:
            return result
        sleep(delay)
        attempt += 1
