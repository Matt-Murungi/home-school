"""Time bands for cache keys. Wall clock in Africa/Kampala (UTC+3, no DST).

| band         | weekdays      | weekends  | representative departure |
| morning_peak | 06:30-09:30   | off_peak  | 08:00 |
| off_peak     | 09:30-15:30   | all day   | 12:00 |
| evening_peak | 15:30-20:00   | off_peak  | 17:30 |
| night        | 20:00-06:30 (crosses midnight) | 20:00-06:30 | 22:00 |

The evening peak is the one that matters most for matching (lessons after school; NFR-5 treats
weekdays 15:00-20:00 as the protected window). Weekends have no commuter peak.
"""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

KAMPALA = ZoneInfo("Africa/Kampala")
BANDS = ("morning_peak", "off_peak", "evening_peak", "night")

_EDGES = {  # weekday band start/end as minutes since local midnight
    "morning_peak": (6 * 60 + 30, 9 * 60 + 30),
    "off_peak": (9 * 60 + 30, 15 * 60 + 30),
    "evening_peak": (15 * 60 + 30, 20 * 60),
}
_REPRESENTATIVE = {
    "morning_peak": time(8, 0),
    "off_peak": time(12, 0),
    "evening_peak": time(17, 30),
    "night": time(22, 0),
}


def band_for(when: datetime) -> str:
    if when.tzinfo is None:
        raise ValueError("a timezone-aware datetime is required")
    local = when.astimezone(KAMPALA)
    minute = local.hour * 60 + local.minute
    if minute >= 20 * 60 or minute < 6 * 60 + 30:
        return "night"
    if local.weekday() >= 5:
        return "off_peak"
    for band, (start, end) in _EDGES.items():
        if start <= minute < end:
            return band
    return "off_peak"  # unreachable; edges tile the day


def departure_for(band: str, now: datetime) -> datetime:
    """A future weekday instant inside `band` for the provider's traffic model (providers need
    a departure time in the future; the typical traffic for that time of day is what we cache)."""
    if band not in BANDS:
        raise ValueError(f"unknown band {band!r}")
    day = now.astimezone(KAMPALA).date()
    for _ in range(10):
        day += timedelta(days=1)
        if day.weekday() < 5:
            dep = datetime.combine(day, _REPRESENTATIVE[band], tzinfo=KAMPALA)
            if dep > now:
                return dep.astimezone(UTC)
    raise AssertionError("unreachable")
