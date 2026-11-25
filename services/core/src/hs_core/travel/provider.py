"""Travel-time provider interface, in-memory fake, and the Google Routes adapter.

The Google adapter is written against Google's public documentation for the Routes API method
`distanceMatrix/v2:computeRouteMatrix` and has only ever run against a mocked transport in tests:
UNVERIFIED against the real service (no API key exists yet; see docs/ops/travel-time.md for the
owner's sanity check). Never log request bodies or the key; errors carry no response text.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol

import httpx

from .geo import deg_str_from_e6, haversine_km, validate_e6


class ProviderError(Exception):
    """The provider could not answer. `retryable` says whether trying again soon may help."""

    def __init__(self, message: str = "", *, retryable: bool = True) -> None:
        super().__init__(message)
        self.retryable = retryable


class ProviderTimeout(ProviderError): ...


class ProviderRateLimited(ProviderError):
    def __init__(self, message: str = "", *, retry_after: float | None = None) -> None:
        super().__init__(message, retryable=True)
        self.retry_after = retry_after


class ProviderQuotaExceeded(ProviderError):
    def __init__(self, message: str = "") -> None:
        super().__init__(message, retryable=False)


@dataclass(frozen=True)
class Point:
    lat_e6: int
    lng_e6: int

    def __post_init__(self) -> None:
        validate_e6(self.lat_e6, self.lng_e6)


@dataclass(frozen=True)
class Element:
    seconds: int | None
    status: str  # "ok" | "no_route" | "error"


class TravelTimeProvider(Protocol):
    max_elements: int

    def matrix(
        self, origins: list[Point], dests: list[Point], departure: datetime
    ) -> list[list[Element]]:
        """matrix[i][j] = time from origins[i] to dests[j] (directional: i->j may differ from
        j->i). Raises ProviderError subclasses; never returns a ragged matrix."""
        ...


# --- fake -------------------------------------------------------------------------------------


@dataclass(frozen=True)
class FakeCall:
    origins: tuple[Point, ...]
    dests: tuple[Point, ...]
    departure: datetime

    @property
    def n_origins(self) -> int:
        return len(self.origins)

    @property
    def n_dests(self) -> int:
        return len(self.dests)


@dataclass
class FakeTravelTimeProvider:
    """Scriptable stand-in. Default answer = straight line x 1.4 at 25 km/h (plausible, so the
    service's sanity checks pass); override per directed pair with `set`, make calls fail with
    `queue_error` (one per call, FIFO) or `always_fail`; `gate` blocks inside a call (threads)."""

    max_elements: int = 100
    calls: list[FakeCall] = field(default_factory=list)
    always_fail: Exception | None = None
    gate: threading.Event | None = None
    entered: threading.Event = field(default_factory=threading.Event)
    _errors: list[Exception] = field(default_factory=list)
    _set: dict[tuple[Point, Point], Element] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def set(self, o: Point, d: Point, seconds: int | None = None, status: str = "ok") -> None:
        self._set[(o, d)] = Element(seconds, status)

    def queue_error(self, exc: Exception) -> None:
        self._errors.append(exc)

    def matrix(self, origins, dests, departure):
        with self._lock:
            self.calls.append(FakeCall(tuple(origins), tuple(dests), departure))
            queued = self._errors.pop(0) if self._errors else None
        self.entered.set()
        if self.gate is not None:
            self.gate.wait(10)
        if queued is not None:
            raise queued
        if self.always_fail is not None:
            raise self.always_fail
        out = []
        for o in origins:
            row = []
            for d in dests:
                if (o, d) in self._set:
                    row.append(self._set[(o, d)])
                else:
                    km = haversine_km(o.lat_e6, o.lng_e6, d.lat_e6, d.lng_e6)
                    row.append(Element(max(60, round(km * 1.4 / 25 * 3600)), "ok"))
            out.append(row)
        return out


# --- Google Routes adapter (unverified) -------------------------------------------------------

GOOGLE_URL = "https://routes.googleapis.com/distanceMatrix/v2:computeRouteMatrix"
FIELD_MASK = "originIndex,destinationIndex,duration,condition,status"


class GoogleRoutesProvider:
    # Google documents 625 elements per request, and 100 when routingPreference is
    # TRAFFIC_AWARE_OPTIMAL; we use TRAFFIC_AWARE and stay at 100 (conservative, unverified).
    def __init__(
        self,
        api_key: str,
        *,
        http: httpx.Client | None = None,
        timeout_s: float = 4.0,
        max_elements: int = 100,
    ) -> None:
        if not api_key or not api_key.strip():
            raise ProviderError("GOOGLE_MAPS_SERVER_KEY is not set", retryable=False)
        self._key = api_key
        self._http = http or httpx.Client(timeout=timeout_s)
        self.max_elements = max_elements

    def __repr__(self) -> str:
        return "GoogleRoutesProvider(key=<hidden>)"

    @staticmethod
    def _wp(p: Point) -> dict:
        lat, lng = float(deg_str_from_e6(p.lat_e6)), float(deg_str_from_e6(p.lng_e6))
        return {"waypoint": {"location": {"latLng": {"latitude": lat, "longitude": lng}}}}

    def matrix(self, origins, dests, departure):
        if len(origins) * len(dests) <= self.max_elements:
            return self._call(origins, dests, departure)
        # split the longer side into slices that fit
        if len(origins) >= len(dests):
            step = max(1, self.max_elements // max(1, len(dests)))
            rows: list[list[Element]] = []
            for i in range(0, len(origins), step):
                rows.extend(self.matrix(origins[i : i + step], dests, departure))
            return rows
        step = max(1, self.max_elements // max(1, len(origins)))
        cols = [
            self.matrix(origins, dests[j : j + step], departure) for j in range(0, len(dests), step)
        ]
        return [sum((c[i] for c in cols), []) for i in range(len(origins))]

    def _call(self, origins, dests, departure):
        body = {
            "origins": [self._wp(p) for p in origins],
            "destinations": [self._wp(p) for p in dests],
            "travelMode": "DRIVE",
            "routingPreference": "TRAFFIC_AWARE",
            "departureTime": departure.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        headers = {"X-Goog-Api-Key": self._key, "X-Goog-FieldMask": FIELD_MASK}
        try:
            resp = self._http.post(GOOGLE_URL, json=body, headers=headers)
        except httpx.TimeoutException:
            raise ProviderTimeout("routes request timed out") from None
        except httpx.HTTPError:
            raise ProviderError("routes request failed (network)") from None
        if resp.status_code == 429:
            raise ProviderRateLimited("routes rate limited", retry_after=_retry_after(resp))
        if resp.status_code in (401, 403):
            if "RESOURCE_EXHAUSTED" in resp.text or "quota" in resp.text.lower():
                raise ProviderQuotaExceeded("routes quota exceeded")
            raise ProviderError(f"routes refused the request ({resp.status_code})", retryable=False)
        if resp.status_code >= 500:
            raise ProviderError(f"routes server error ({resp.status_code})")
        if resp.status_code != 200:
            raise ProviderError(
                f"routes rejected the request ({resp.status_code})", retryable=False
            )
        try:
            data = resp.json()
        except ValueError:
            raise ProviderError("routes returned an unreadable body") from None
        if not isinstance(data, list):
            raise ProviderError("routes returned an unexpected body")
        out = [[Element(None, "error") for _ in dests] for _ in origins]
        for item in data:
            try:
                i, j = int(item["originIndex"]), int(item["destinationIndex"])
                out[i][j] = _element(item)
            except (KeyError, TypeError, ValueError, IndexError):
                continue
        return out


def _retry_after(resp: httpx.Response) -> float | None:
    try:
        v = float(resp.headers.get("Retry-After", ""))
    except ValueError:
        return None
    return v if v >= 0 else None


def _element(item: dict) -> Element:
    if item.get("condition") == "ROUTE_NOT_FOUND":
        return Element(None, "no_route")
    dur = item.get("duration")
    if item.get("condition") == "ROUTE_EXISTS" and isinstance(dur, str) and dur.endswith("s"):
        try:
            return Element(int(float(dur[:-1])), "ok")
        except ValueError:
            pass
    return Element(None, "error")
