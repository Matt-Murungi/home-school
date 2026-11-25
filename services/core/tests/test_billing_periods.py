"""B03: billing periods in Africa/Kampala calendar dates, stored as UTC instants."""

from __future__ import annotations

import random
from datetime import UTC, date, datetime, timedelta

import pytest

from hs_core.billing.periods import (
    add_months,
    kampala_date,
    period,
    period_index_for,
    sessions_included,
)


def test_kampala_date_differs_from_the_utc_date_after_21_00_utc():
    assert kampala_date(datetime(2027, 3, 31, 21, 0, tzinfo=UTC)) == date(2027, 4, 1)
    assert kampala_date(datetime(2027, 3, 31, 20, 59, 59, tzinfo=UTC)) == date(2027, 3, 31)
    # New Year in Kampala arrives at 21:00 UTC on 31 Dec
    assert kampala_date(datetime(2026, 12, 31, 21, 0, tzinfo=UTC)) == date(2027, 1, 1)


def test_naive_instants_are_rejected():
    with pytest.raises(ValueError):
        kampala_date(datetime(2027, 3, 31, 21, 0))


def test_period_bounds_are_kampala_midnight_in_utc():
    p = period(date(2027, 1, 15), 0)
    assert p.start == date(2027, 1, 15) and p.end_exclusive == date(2027, 2, 15)
    assert p.start_at == datetime(2027, 1, 14, 21, 0, tzinfo=UTC)
    assert p.end_at == datetime(2027, 2, 14, 21, 0, tzinfo=UTC)
    assert p.end_inclusive == date(2027, 2, 14)
    assert p.days == 31


@pytest.mark.parametrize(
    ("anchor", "expected"),
    [
        (date(2027, 1, 31), [date(2027, 1, 31), date(2027, 2, 28), date(2027, 3, 31)]),
        (date(2028, 1, 31), [date(2028, 1, 31), date(2028, 2, 29), date(2028, 3, 31)]),
        (date(2027, 1, 30), [date(2027, 1, 30), date(2027, 2, 28), date(2027, 3, 30)]),
        (date(2027, 8, 31), [date(2027, 8, 31), date(2027, 9, 30), date(2027, 10, 31)]),
        (date(2027, 12, 31), [date(2027, 12, 31), date(2028, 1, 31), date(2028, 2, 29)]),
    ],
)
def test_month_end_anchor_clamps_and_never_drifts(anchor, expected):
    assert [period(anchor, i).start for i in range(3)] == expected


def test_periods_tile_without_gap_or_overlap_across_years():
    for anchor in (date(2027, 1, 29), date(2027, 12, 31), date(2028, 2, 29), date(2027, 5, 1)):
        for i in range(30):
            a, b = period(anchor, i), period(anchor, i + 1)
            assert a.end_exclusive == b.start and a.end_at == b.start_at
            assert a.days >= 28 and a.days <= 31


def test_period_index_for_every_day_of_three_years_agrees_with_period():
    anchor = date(2027, 1, 31)
    d = anchor
    for _ in range(366 * 3):
        i = period_index_for(anchor, d)
        p = period(anchor, i)
        assert p.start <= d < p.end_exclusive
        d += timedelta(days=1)


def test_period_index_before_anchor_is_an_error():
    with pytest.raises(ValueError):
        period_index_for(date(2027, 1, 31), date(2027, 1, 30))


def test_random_anchors_properties():
    rng = random.Random(7)
    for _ in range(300):
        anchor = date(2026, 1, 1) + timedelta(days=rng.randrange(0, 1500))
        i = rng.randrange(0, 40)
        p = period(anchor, i)
        assert p.start.day == min(anchor.day, (p.end_exclusive - timedelta(days=1)).day) or (
            p.start.day <= anchor.day
        )
        assert p.start_at.tzinfo is not None and p.start_at.utcoffset() == timedelta(0)
        assert period_index_for(anchor, p.start) == i
        assert period_index_for(anchor, p.end_exclusive - timedelta(days=1)) == i
        assert period_index_for(anchor, p.end_exclusive) == i + 1


def test_add_months_clamps_to_the_anchor_day_not_the_previous_result():
    a = date(2027, 1, 31)
    assert add_months(a, 1) == date(2027, 2, 28)
    assert add_months(a, 2) == date(2027, 3, 31)  # not 28 (no drift)
    assert add_months(a, 13) == date(2028, 2, 29)


def test_sessions_included_is_sessions_per_week_times_weeks_in_period():
    # rule: floor(sessions_per_week * days / 7), integer arithmetic
    assert sessions_included(2, 28) == 8
    assert sessions_included(2, 30) == 8
    assert sessions_included(2, 31) == 8
    assert sessions_included(3, 31) == 13
    assert sessions_included(5, 28) == 20
    assert sessions_included(1, 28) == 4
    assert sessions_included(7, 31) == 31


def test_sessions_included_is_an_int_and_rejects_bad_input():
    assert type(sessions_included(2, 30)) is int
    for bad in (0, -1, 8, 2.0, True, "2"):
        with pytest.raises(ValueError):
            sessions_included(bad, 30)
    with pytest.raises(ValueError):
        sessions_included(2, 0)
