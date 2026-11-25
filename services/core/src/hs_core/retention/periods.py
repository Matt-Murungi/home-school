"""Calendar periods and the retention date rule (C03 #141, DP-6).

Retention is counted in Africa/Kampala calendar days, not in UTC instants: a row created at
23:30 Kampala on 31 December has the Kampala date 31 December (its UTC date is the same, but a row
created at 00:30 on 1 January has the UTC date 31 December). The rule, in one sentence:

    a row is due when TODAY (Kampala date of `now`) is AFTER (Kampala date of the clock instant
    + the period), i.e. the row is kept through the whole last day of its period.

Month and year arithmetic that lands on a day that does not exist (29 Feb + 1 year, 31 Jan + 1
month) rolls FORWARD to the first day of the next month, so a record is kept one day longer, never
shorter (conservative: retention errors on the long side are recoverable, deletion is not).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

KAMPALA = ZoneInfo("Africa/Kampala")
_PERIOD_RE = re.compile(r"^(\d{1,3})([dmy])$")


class PeriodError(ValueError):
    pass


@dataclass(frozen=True, order=False)
class Period:
    years: int = 0
    months: int = 0
    days: int = 0

    @classmethod
    def parse(cls, text: str) -> Period:
        """'90d', '18m', '7y' (one unit only: keeps the rendered schedule unambiguous)."""
        m = _PERIOD_RE.match(str(text).strip().lower())
        if not m or int(m.group(1)) == 0:
            raise PeriodError(f"bad retention period {text!r} (use e.g. 90d, 18m, 7y)")
        n = int(m.group(1))
        return cls(**{"d": {"days": n}, "m": {"months": n}, "y": {"years": n}}[m.group(2)])

    def __str__(self) -> str:
        if self.years:
            return f"{self.years}y"
        if self.months:
            return f"{self.months}m"
        return f"{self.days}d"

    @property
    def human(self) -> str:
        if self.years:
            return f"{self.years} year{'s' if self.years != 1 else ''}"
        if self.months:
            return f"{self.months} month{'s' if self.months != 1 else ''}"
        return f"{self.days} day{'s' if self.days != 1 else ''}"

    @property
    def min_days(self) -> int:
        """A lower bound of the real length in days (for the coarse repository prefilter)."""
        return self.years * 365 + self.months * 28 + self.days

    @property
    def approx_days(self) -> int:
        """Comparable length (floors are compared with this, never used for a deadline)."""
        return self.years * 365 + self.months * 30 + self.days

    def shorter_than(self, other: Period) -> bool:
        return self.approx_days < other.approx_days


def kampala_date(instant: datetime) -> date:
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise ValueError("retention needs timezone-aware instants")
    return instant.astimezone(KAMPALA).date()


def add_period(start: date, period: Period) -> date:
    total = start.year * 12 + (start.month - 1) + period.years * 12 + period.months
    year, month = divmod(total, 12)
    month += 1
    try:
        out = date(year, month, start.day)
    except ValueError:  # day does not exist in that month: first day of the next month
        out = date(year + (month == 12), 1 if month == 12 else month + 1, 1)
    return out + timedelta(days=period.days)


def parse_clock(value: object) -> date | None:
    """A clock column holds an ISO datetime (UTC) or a calendar date (varchar(10)). Anything
    else is 'no clock' (the row is never due), never a guess."""
    if not isinstance(value, str) or not value:
        return None
    try:
        if len(value) == 10:
            return date.fromisoformat(value)
        return kampala_date(datetime.fromisoformat(value.replace("Z", "+00:00")))
    except ValueError:
        return None


def expiry_date(clock_value: object, period: Period) -> date | None:
    start = parse_clock(clock_value)
    return None if start is None else add_period(start, period)


def is_due(clock_value: object, period: Period, now: datetime) -> bool:
    """True when the row has outlived its period: Kampala today is AFTER the expiry date."""
    exp = expiry_date(clock_value, period)
    return exp is not None and kampala_date(now) > exp


def prefilter_boundary(period: Period, now: datetime) -> str:
    """UTC ISO instant: rows whose clock is at or after it can NOT be due yet; rows before it
    MAY be due (the exact calendar rule is applied per row). Looser than the exact rule by a few
    days, never tighter, so no due row is missed."""
    return (now.astimezone(UTC) - timedelta(days=period.min_days - 1)).isoformat()
