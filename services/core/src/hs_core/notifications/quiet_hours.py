"""Quiet hours (SR 8): non-urgent push waits from 21:00 to 07:00 Africa/Kampala (UTC+3, no DST).
A GLOBAL setting (M04 note), not per user."""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

KAMPALA = ZoneInfo("Africa/Kampala")


def _local(instant: datetime) -> datetime:
    if instant.tzinfo is None:
        raise ValueError("instant must be timezone-aware")
    return instant.astimezone(KAMPALA)


def _check(start: int, end: int) -> None:
    if not (0 <= start <= 23 and 0 <= end <= 23) or start == end:
        raise ValueError("quiet hours need two different hours between 0 and 23")


def is_quiet(instant: datetime, start: int = 21, end: int = 7) -> bool:
    """True from `start`:00:00 (inclusive) to `end`:00:00 (exclusive), Kampala wall clock."""
    _check(start, end)
    t = _local(instant).time()
    s, e = time(start), time(end)
    return (s <= t < e) if s < e else (t >= s or t < e)


def next_allowed(instant: datetime, start: int = 21, end: int = 7) -> datetime:
    """The instant itself when allowed, otherwise the next `end`:00:00 Kampala (UTC)."""
    if not is_quiet(instant, start, end):
        return instant.astimezone(UTC)
    local = _local(instant)
    candidate = local.replace(hour=end, minute=0, second=0, microsecond=0)
    if candidate <= local:
        candidate += timedelta(days=1)  # Kampala has no DST: a day is 24 hours
    return candidate.astimezone(UTC)
