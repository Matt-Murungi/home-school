"""Retention date arithmetic (C03): boundaries, leap years, month ends, Kampala vs UTC date."""

import random
from datetime import UTC, date, datetime, timedelta

import pytest

from hs_core.retention.periods import (
    KAMPALA,
    Period,
    PeriodError,
    add_period,
    expiry_date,
    is_due,
    kampala_date,
    parse_clock,
    prefilter_boundary,
)

Y3 = Period.parse("3y")


def utc(*a):
    return datetime(*a, tzinfo=UTC)


def eat(*a):
    return datetime(*a, tzinfo=KAMPALA)


@pytest.mark.parametrize(
    "text,period",
    [
        ("90d", Period(days=90)),
        ("18m", Period(months=18)),
        ("7y", Period(years=7)),
        (" 3Y ", Period(years=3)),
    ],
)
def test_parse(text, period):
    assert Period.parse(text) == period


@pytest.mark.parametrize("bad", ["", "0d", "3", "3w", "-1d", "1y6m", "abc", "1000d", None, 7])
def test_parse_rejects(bad):
    with pytest.raises(PeriodError):
        Period.parse(bad)


def test_exactly_at_the_end_of_the_period_is_still_kept_and_the_next_day_is_due():
    clock = "2024-03-10T09:00:00+00:00"  # Kampala 12:00 on 10 March 2024
    end = date(2027, 3, 10)
    assert expiry_date(clock, Y3) == end
    assert not is_due(clock, Y3, eat(2027, 3, 10, 23, 59, 59))  # whole last day: kept
    assert is_due(clock, Y3, eat(2027, 3, 11, 0, 0, 0))  # first second of the next Kampala day


def test_one_second_before_midnight_vs_after_in_utc_terms():
    clock = "2024-03-10T09:00:00+00:00"
    assert not is_due(clock, Y3, utc(2027, 3, 10, 20, 59, 59))  # 23:59:59 Kampala
    assert is_due(clock, Y3, utc(2027, 3, 10, 21, 0, 0))  # 00:00:00 Kampala, 11 March


def test_dec_31_2330_kampala_is_dec_31_but_jan_1_0030_kampala_has_a_utc_date_of_dec_31():
    late = eat(2026, 12, 31, 23, 30)
    early = eat(2027, 1, 1, 0, 30)
    assert kampala_date(late) == date(2026, 12, 31)
    assert kampala_date(early) == date(2027, 1, 1)
    assert early.astimezone(UTC).date() == date(2026, 12, 31)  # the trap
    # a row created at 00:30 on 1 Jan 2024 (Kampala) belongs to 1 Jan, not to 31 Dec 2023
    created = early.replace(year=2024).isoformat()
    assert parse_clock(created) == date(2024, 1, 1)
    assert expiry_date(created, Y3) == date(2027, 1, 1)
    assert not is_due(created, Y3, eat(2027, 1, 1, 0, 30))  # today IS the expiry day
    assert is_due(created, Y3, eat(2027, 1, 2, 0, 30))


def test_leap_day_plus_one_year_rolls_forward_to_march_1():
    assert add_period(date(2024, 2, 29), Period(years=1)) == date(2025, 3, 1)
    assert add_period(date(2024, 2, 29), Period(years=4)) == date(2028, 2, 29)
    assert add_period(date(2024, 2, 29), Period(years=3)) == date(2027, 3, 1)


@pytest.mark.parametrize("start,months,expected", [
    (date(2026, 1, 31), 1, date(2026, 3, 1)),  # no 31 Feb: roll to 1 March (kept longer)
    (date(2026, 1, 30), 1, date(2026, 3, 1)),
    (date(2028, 1, 31), 1, date(2028, 3, 1)),
    (date(2026, 3, 31), 1, date(2026, 5, 1)),
    (date(2026, 11, 30), 3, date(2027, 3, 1)),
    (date(2026, 12, 31), 2, date(2027, 3, 1)),
    (date(2026, 10, 31), 2, date(2026, 12, 31)),
    (date(2026, 12, 15), 1, date(2027, 1, 15)),
    (date(2026, 1, 15), 12, date(2027, 1, 15)),
])  # fmt: skip
def test_month_arithmetic_and_month_ends(start, months, expected):
    assert add_period(start, Period(months=months)) == expected


def test_days_are_added_after_the_calendar_step():
    assert add_period(date(2024, 2, 28), Period(days=2)) == date(2024, 3, 1)  # leap year
    assert add_period(date(2025, 2, 28), Period(days=2)) == date(2025, 3, 2)


def test_leap_day_row_with_a_one_year_period_is_due_on_march_2_not_on_march_1():
    clock = "2024-02-29T10:00:00+00:00"
    p = Period(years=1)
    assert not is_due(clock, p, eat(2025, 3, 1, 12))
    assert is_due(clock, p, eat(2025, 3, 2, 0, 5))


def test_naive_datetimes_are_refused_everywhere():
    with pytest.raises(ValueError):
        kampala_date(datetime(2026, 1, 1))
    with pytest.raises(ValueError):
        is_due("2020-01-01T00:00:00+00:00", Y3, datetime(2030, 1, 1))


@pytest.mark.parametrize(
    "bad", [None, "", 5, "garbage", "2026-13-40", "2026-02-30T00:00:00+00:00", [], {}]
)
def test_unreadable_clock_values_are_never_due(bad):
    assert parse_clock(bad) is None
    assert not is_due(bad, Period(days=1), utc(2100, 1, 1))


def test_date_only_clock_values_use_that_calendar_day():
    assert parse_clock("2024-05-06") == date(2024, 5, 6)
    assert is_due("2024-05-06", Period(days=1), eat(2024, 5, 8, 0, 1))
    assert not is_due("2024-05-06", Period(days=1), eat(2024, 5, 7, 23, 0))


def test_z_suffix_is_read_as_utc():
    assert parse_clock("2024-05-06T22:00:00Z") == date(2024, 5, 7)  # 01:00 Kampala next day


def test_prefilter_is_never_tighter_than_the_exact_rule_property():
    """Every row the exact rule calls due is selected by the prefilter (no due row is missed)."""
    rng = random.Random(7)
    for _ in range(3000):
        period = rng.choice(
            [
                Period(days=rng.randint(1, 400)),
                Period(months=rng.randint(1, 60)),
                Period(years=rng.randint(1, 30)),
            ]
        )
        now = utc(2026, 1, 1) + timedelta(days=rng.randint(0, 12000), seconds=rng.randint(0, 86399))
        clock = now - timedelta(days=rng.randint(0, 12000), seconds=rng.randint(0, 86399))
        if is_due(clock.isoformat(), period, now):
            assert clock.isoformat() < prefilter_boundary(period, now), (period, now, clock)
