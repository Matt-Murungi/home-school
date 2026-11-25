"""N01 (#126): quiet hours 21:00-07:00 Africa/Kampala (UTC+3, no DST)."""

import random
from datetime import UTC, datetime, timedelta, timezone

import pytest

from hs_core.notifications import quiet_hours as q

EAT = timezone(timedelta(hours=3))


def at(h, m, s, day=18):
    return datetime(2027, 1, day, h, m, s, tzinfo=EAT)


@pytest.mark.parametrize(
    "t,quiet",
    [
        (at(20, 59, 59), False),
        (at(21, 0, 0), True),
        (at(6, 59, 59), True),
        (at(7, 0, 0), False),
        (at(0, 0, 0), True),
        (at(12, 0, 0), False),
        (at(23, 59, 59), True),
    ],
)
def test_boundaries(t, quiet):
    assert q.is_quiet(t) is quiet
    assert q.is_quiet(t.astimezone(UTC)) is quiet  # instant, not wall clock of the input zone


def test_next_allowed_is_next_0700_kampala():
    assert q.next_allowed(at(21, 0, 0)) == at(7, 0, 0, day=19).astimezone(UTC)
    assert q.next_allowed(at(23, 30, 0)) == at(7, 0, 0, day=19).astimezone(UTC)
    assert q.next_allowed(at(3, 0, 0)) == at(7, 0, 0).astimezone(UTC)
    assert q.next_allowed(at(6, 59, 59)) == at(7, 0, 0).astimezone(UTC)
    assert q.next_allowed(at(7, 0, 0)) == at(7, 0, 0).astimezone(UTC)  # already allowed
    assert q.next_allowed(at(20, 59, 59)) == at(20, 59, 59).astimezone(UTC)


def test_naive_datetimes_are_rejected():
    with pytest.raises(ValueError):
        q.is_quiet(datetime(2027, 1, 1, 12, 0))


def test_non_wrapping_window_and_validation():
    assert q.is_quiet(at(13, 0, 0), start=12, end=14)
    assert not q.is_quiet(at(14, 0, 0), start=12, end=14)
    assert q.next_allowed(at(13, 0, 0), start=12, end=14) == at(14, 0, 0).astimezone(UTC)
    with pytest.raises(ValueError):
        q.is_quiet(at(1, 0, 0), start=7, end=7)


def test_property_next_allowed_is_never_quiet_and_never_in_the_past():
    rng = random.Random(7)
    base = datetime(2027, 1, 1, tzinfo=UTC)
    for _ in range(2000):
        t = base + timedelta(seconds=rng.randrange(0, 86400 * 400))
        n = q.next_allowed(t)
        assert not q.is_quiet(n)
        assert n >= t
        assert n - t <= timedelta(hours=10)
        if not q.is_quiet(t):
            assert n == t
