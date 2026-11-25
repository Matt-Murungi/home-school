"""Coordinates as integer micro-degrees (the schema's `*_e6` columns), distance and fallback time.

Coordinates stay integers everywhere (storage, cache keys, provider requests); floats appear only
inside the haversine calculation, whose result is rounded to whole minutes. Degrees are rendered
for the provider from the integer by string arithmetic, so no value can drift (0.1 + 0.2 style).

Fallback assumptions (Kampala), used only when the provider is unavailable or over budget:
- road distance = straight-line (haversine) distance x CIRCUITY 1.4. Kampala's hilly, river-and-
  swamp-cut street grid makes roads much longer than the crow flies; 1.3 to 1.5 is the usual
  range for African cities, 1.4 is the middle. Unverified: calibrate with real routes (owner step).
- average speed 20 km/h, scaled by a band factor (BAND_SPEED_FACTOR): peak traffic in the city is
  slow (boda-boda and taxi congestion, 10 to 15 km/h in the evening peak), nights are quicker.
  Unverified: replace by the owner's sanity check of real Kampala routes
  (docs/ops/travel-time.md).
Both are settings (`TRAVEL_FALLBACK_SPEED_KMH`, `TRAVEL_FALLBACK_CIRCUITY`).
"""

from __future__ import annotations

import math

LAT_MAX_E6 = 90_000_000
LNG_MAX_E6 = 180_000_000
# Uganda bounding box in micro-degrees (a little generous). Outside: a warning, never an error.
UG_LAT_E6 = (-1_600_000, 4_400_000)
UG_LNG_E6 = (29_400_000, 35_100_000)
EARTH_RADIUS_KM = 6371.0088

# Multiplier on the configured average speed per time band (1.0 = the setting itself).
BAND_SPEED_FACTOR = {"morning_peak": 0.7, "evening_peak": 0.7, "off_peak": 1.0, "night": 1.4}


class InvalidCoordinate(ValueError):
    """Not a valid integer micro-degree coordinate."""


def _is_int(v: object) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def validate_e6(lat_e6: object, lng_e6: object) -> None:
    """Raise InvalidCoordinate unless both are real ints inside the Earth's ranges. (0, 0) is
    rejected: it is what an unset coordinate looks like and is in the Atlantic, not in Uganda."""
    if not (_is_int(lat_e6) and _is_int(lng_e6)):
        raise InvalidCoordinate("coordinates must be integer micro-degrees")
    if abs(lat_e6) > LAT_MAX_E6 or abs(lng_e6) > LNG_MAX_E6:
        raise InvalidCoordinate("coordinate out of range")
    if lat_e6 == 0 and lng_e6 == 0:
        raise InvalidCoordinate("(0, 0) is not a location")


def outside_uganda(lat_e6: int, lng_e6: int) -> bool:
    return not (UG_LAT_E6[0] <= lat_e6 <= UG_LAT_E6[1] and UG_LNG_E6[0] <= lng_e6 <= UG_LNG_E6[1])


def deg_str_from_e6(v: int) -> str:
    """Exact decimal degrees for an integer micro-degree value: 347600 -> '0.3476'."""
    sign = "-" if v < 0 else ""
    whole, frac = divmod(abs(v), 1_000_000)
    return f"{sign}{whole}.{frac:06d}".rstrip("0").rstrip(".") if frac else f"{sign}{whole}"


def haversine_km(lat1_e6: int, lng1_e6: int, lat2_e6: int, lng2_e6: int) -> float:
    p1, p2 = math.radians(lat1_e6 / 1e6), math.radians(lat2_e6 / 1e6)
    dphi = p2 - p1
    dlmb = math.radians((lng2_e6 - lng1_e6) / 1e6)
    h = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(h)))


def fallback_minutes(distance_km: float, *, speed_kmh: float, circuity: float) -> int:
    """Whole minutes (rounded up, at least 1): distance x circuity / speed."""
    if speed_kmh <= 0 or circuity <= 0:
        raise ValueError("speed and circuity must be positive")
    return max(1, math.ceil(distance_km * circuity / speed_kmh * 60 - 1e-9))
