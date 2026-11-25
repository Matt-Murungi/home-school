"""X01 (#85): integer micro-degree coordinates, haversine fallback, time bands."""

from datetime import UTC, datetime

import pytest

from hs_core.travel.bands import BANDS, band_for, departure_for
from hs_core.travel.geo import (
    InvalidCoordinate,
    deg_str_from_e6,
    fallback_minutes,
    haversine_km,
    outside_uganda,
    validate_e6,
)
from hs_core.travel.provider import Point


def test_valid_kampala_coordinates_pass():
    validate_e6(347_600, 32_582_500)
    assert not outside_uganda(347_600, 32_582_500)


@pytest.mark.parametrize(
    "lat,lng",
    [
        (90_000_001, 0),
        (-90_000_001, 10),
        (0, 180_000_001),
        (0, -180_000_001),
        (0, 0),  # null island: an unset coordinate, not a place
        (0.347, 32.58),  # degrees as floats: the wrong unit
        ("347600", 32_582_500),
        (True, 32_582_500),
        (None, 32_582_500),
        (347_600.0, 32_582_500),  # float even if whole: refuse, no silent coercion
    ],
)
def test_invalid_coordinates_are_rejected(lat, lng):
    with pytest.raises(InvalidCoordinate):
        validate_e6(lat, lng)
    with pytest.raises(InvalidCoordinate):
        Point(lat, lng)


@pytest.mark.parametrize("lat,lng", [(51_500_000, -127_000), (-1_286_000, 36_820_000)])
def test_outside_uganda_is_a_warning_flag_not_an_error(lat, lng):
    validate_e6(lat, lng)  # valid on Earth
    assert outside_uganda(lat, lng)


def test_e6_to_decimal_string_has_no_float_drift():
    assert deg_str_from_e6(347_600) == "0.3476"
    assert deg_str_from_e6(-1_000_001) == "-1.000001"
    assert deg_str_from_e6(32_582_500) == "32.5825"
    assert deg_str_from_e6(5) == "0.000005"
    assert deg_str_from_e6(-5) == "-0.000005"
    for v in (1, 999_999, 1_000_000, 123_456_789, -90_000_000):
        assert round(float(deg_str_from_e6(v)) * 1_000_000) == v


def test_haversine_known_distance_kololo_to_entebbe():
    d = haversine_km(333_000, 32_590_000, 51_200, 32_463_700)
    assert 33 < d < 36  # about 34.3 km great-circle


def test_haversine_is_symmetric_and_zero_for_same_point():
    a, b = (333_000, 32_590_000), (353_000, 32_615_000)
    assert haversine_km(*a, *b) == pytest.approx(haversine_km(*b, *a))
    assert haversine_km(*a, *a) == 0.0


def test_fallback_minutes_documented_formula():
    # 10 km straight line x 1.4 circuity / 20 km/h = 42 min
    assert fallback_minutes(10.0, speed_kmh=20.0, circuity=1.4) == 42
    # never zero for distinct places
    assert fallback_minutes(0.01, speed_kmh=20.0, circuity=1.4) == 1
    with pytest.raises(ValueError):
        fallback_minutes(1.0, speed_kmh=0, circuity=1.4)


# --- time bands (Africa/Kampala wall clock) ---------------------------------------------------


def k(h, m=0, day=18):  # Kampala local time on Mon 18 Jan 2027 (UTC+3)
    from zoneinfo import ZoneInfo

    return datetime(2027, 1, day, h, m, tzinfo=ZoneInfo("Africa/Kampala"))


@pytest.mark.parametrize(
    "when,band",
    [
        (k(6, 29), "night"),
        (k(6, 30), "morning_peak"),
        (k(9, 29), "morning_peak"),
        (k(9, 30), "off_peak"),
        (k(15, 29), "off_peak"),
        (k(15, 30), "evening_peak"),
        (k(19, 59), "evening_peak"),
        (k(20, 0), "night"),
        (k(23, 59), "night"),
        (k(0, 0, day=19), "night"),
        (k(3, 0, day=19), "night"),
    ],
)
def test_band_boundaries_including_the_night_band_that_crosses_midnight(when, band):
    assert band_for(when) == band


def test_band_uses_kampala_time_not_utc():
    assert band_for(datetime(2027, 1, 18, 14, 30, tzinfo=UTC)) == "evening_peak"  # 17:30 EAT
    assert band_for(datetime(2027, 1, 18, 17, 30, tzinfo=UTC)) == "night"  # 20:30 EAT


def test_weekend_has_no_peaks():
    assert band_for(k(17, 30, day=23)) == "off_peak"  # Saturday
    assert band_for(k(8, 0, day=24)) == "off_peak"  # Sunday
    assert band_for(k(22, 0, day=23)) == "night"


def test_naive_datetimes_are_refused():
    with pytest.raises(ValueError):
        band_for(datetime(2027, 1, 18, 12, 0))


@pytest.mark.parametrize("band", BANDS)
def test_departure_is_in_the_future_on_a_weekday_inside_the_band(band):
    now = datetime(2027, 1, 22, 20, 0, tzinfo=UTC)  # Friday 23:00 Kampala
    dep = departure_for(band, now)
    assert dep > now and dep.tzinfo is not None
    assert band_for(dep) == band
    assert dep.astimezone(k(0).tzinfo).weekday() < 5
