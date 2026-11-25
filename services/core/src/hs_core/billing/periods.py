"""Billing periods (SUB-3): monthly, in Africa/Kampala calendar dates, stored as UTC instants.

Period k starts on the anchor day-of-month of month (anchor month + k), clamped to the last day of
shorter months, always computed from the ANCHOR (31 Jan, 28 Feb, 31 Mar; never 31, 28, 28). The
period runs from Kampala midnight of its start date to Kampala midnight of the next start date
(exclusive end). The anchor is the Kampala date on which the subscription was created (the first
invoice is created at period start, SR 7.8). A payment that arrives later does not move it.

Sessions included (rule, SUB-3): `sessions_per_week * days_in_period // 7`, integer arithmetic,
rounded down: 2 a week is 8 in a 28, 30 or 31 day month; 3 a week is 13 in a 31 day month.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

KAMPALA = ZoneInfo("Africa/Kampala")


def kampala_date(instant: datetime) -> date:
    if instant.tzinfo is None:
        raise ValueError("timezone-aware datetime required")
    return instant.astimezone(KAMPALA).date()


def kampala_midnight_utc(d: date) -> datetime:
    return datetime(d.year, d.month, d.day, tzinfo=KAMPALA).astimezone(UTC)


def add_months(anchor: date, k: int) -> date:
    """`anchor` plus k months, day clamped to the month's length (always from the anchor)."""
    if k < 0:
        raise ValueError("k must not be negative")
    m0 = anchor.month - 1 + k
    year, month = anchor.year + m0 // 12, m0 % 12 + 1
    return date(year, month, min(anchor.day, calendar.monthrange(year, month)[1]))


@dataclass(frozen=True)
class Period:
    index: int
    start: date
    end_exclusive: date

    @property
    def end_inclusive(self) -> date:
        return self.end_exclusive - timedelta(days=1)

    @property
    def days(self) -> int:
        return (self.end_exclusive - self.start).days

    @property
    def start_at(self) -> datetime:
        return kampala_midnight_utc(self.start)

    @property
    def end_at(self) -> datetime:
        return kampala_midnight_utc(self.end_exclusive)


def period(anchor: date, index: int) -> Period:
    return Period(index, add_months(anchor, index), add_months(anchor, index + 1))


def period_index_for(anchor: date, d: date) -> int:
    """Index of the period that contains the Kampala date `d`."""
    if d < anchor:
        raise ValueError("date is before the anchor")
    i = (d.year - anchor.year) * 12 + (d.month - anchor.month)
    while add_months(anchor, i) > d:
        i -= 1
    while add_months(anchor, i + 1) <= d:
        i += 1
    return i


def _is_int(v) -> bool:
    return type(v) is int


def sessions_included(sessions_per_week: int, days: int) -> int:
    if not _is_int(sessions_per_week) or not 1 <= sessions_per_week <= 7:
        raise ValueError("sessions_per_week must be an integer from 1 to 7")
    if not _is_int(days) or days < 1:
        raise ValueError("days must be a positive integer")
    return sessions_per_week * days // 7
