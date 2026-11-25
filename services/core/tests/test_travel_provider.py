"""X01 (#85): provider interface, in-memory fake, Google Routes adapter (mock-tested only)."""

import json
from datetime import UTC, datetime

import httpx
import pytest

from hs_core.travel.provider import (
    Element,
    FakeTravelTimeProvider,
    GoogleRoutesProvider,
    Point,
    ProviderError,
    ProviderQuotaExceeded,
    ProviderRateLimited,
    ProviderTimeout,
)

A, B, C = Point(333_000, 32_590_000), Point(353_000, 32_615_000), Point(51_200, 32_463_700)
DEP = datetime(2027, 1, 19, 14, 0, tzinfo=UTC)


def test_fake_returns_scripted_asymmetric_durations():
    p = FakeTravelTimeProvider()
    p.set(A, B, seconds=1200)
    p.set(B, A, seconds=1800)
    out = p.matrix([A, B], [B, A], DEP)
    assert out[0][0].seconds == 1200 and out[1][1].seconds == 1800
    assert p.calls[0].n_origins == 2 and p.calls[0].n_dests == 2


def test_fake_scripted_errors_and_element_statuses():
    p = FakeTravelTimeProvider()
    p.queue_error(ProviderTimeout("slow"))
    with pytest.raises(ProviderTimeout):
        p.matrix([A], [B], DEP)
    p.set(A, B, status="no_route")
    assert p.matrix([A], [B], DEP)[0][0] == Element(None, "no_route")
    p.always_fail = ProviderQuotaExceeded("quota")
    with pytest.raises(ProviderQuotaExceeded):
        p.matrix([A], [B], DEP)


def mock_provider(handler, **kw):
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return GoogleRoutesProvider("test-key-not-real", http=client, **kw)  # secret-scan: allow


def ok_body(rows):
    return [
        {"originIndex": o, "destinationIndex": d, "duration": f"{s}s", "condition": "ROUTE_EXISTS"}
        for (o, d, s) in rows
    ]


def test_google_request_shape_and_parse():
    seen = {}

    def handler(req: httpx.Request):
        seen["url"] = str(req.url)
        seen["headers"] = req.headers
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json=ok_body([(0, 0, 900), (1, 0, 1500)]))

    out = mock_provider(handler).matrix([A, B], [C], DEP)
    assert seen["url"] == "https://routes.googleapis.com/distanceMatrix/v2:computeRouteMatrix"
    assert seen["headers"]["x-goog-api-key"] == "test-key-not-real"
    assert "duration" in seen["headers"]["x-goog-fieldmask"]
    body = seen["body"]
    assert body["travelMode"] == "DRIVE"
    assert body["routingPreference"].startswith("TRAFFIC_AWARE")
    assert body["departureTime"] == "2027-01-19T14:00:00Z"
    lat0 = body["origins"][0]["waypoint"]["location"]["latLng"]
    assert round(lat0["latitude"] * 1_000_000) == 333_000
    assert round(lat0["longitude"] * 1_000_000) == 32_590_000
    assert out[0][0].seconds == 900 and out[1][0].seconds == 1500


def test_google_no_route_and_missing_elements():
    def handler(req):
        return httpx.Response(
            200,
            json=[
                {"originIndex": 0, "destinationIndex": 0, "condition": "ROUTE_NOT_FOUND"},
                # element (1,0) missing entirely -> zero results for that pair
            ],
        )

    out = mock_provider(handler).matrix([A, B], [C], DEP)
    assert out[0][0] == Element(None, "no_route")
    assert out[1][0].status == "error"


def test_google_empty_response_is_all_errors_not_a_crash():
    out = mock_provider(lambda r: httpx.Response(200, json=[])).matrix([A], [B], DEP)
    assert out[0][0].status == "error"


def test_google_http_status_mapping():
    def run(status, **kw):
        return mock_provider(lambda r: httpx.Response(status, **kw)).matrix([A], [B], DEP)

    with pytest.raises(ProviderRateLimited) as e:
        run(429, headers={"Retry-After": "7"})
    assert e.value.retry_after == 7.0
    with pytest.raises(ProviderRateLimited) as e:
        run(429, headers={"Retry-After": "soon"})
    assert e.value.retry_after is None
    with pytest.raises(ProviderQuotaExceeded):
        run(403, json={"error": {"status": "RESOURCE_EXHAUSTED"}})
    with pytest.raises(ProviderError):
        run(500)
    with pytest.raises(ProviderError):
        run(400, json={"error": {"message": "bad"}})


def test_google_timeout_and_network_errors_are_provider_errors():
    def boom(req):
        raise httpx.ReadTimeout("slow", request=req)

    with pytest.raises(ProviderTimeout):
        mock_provider(boom).matrix([A], [B], DEP)

    def down(req):
        raise httpx.ConnectError("down", request=req)

    with pytest.raises(ProviderError):
        mock_provider(down).matrix([A], [B], DEP)


def test_google_garbage_body_is_a_provider_error():
    with pytest.raises(ProviderError):
        mock_provider(lambda r: httpx.Response(200, content=b"<html>")).matrix([A], [B], DEP)


def test_google_splits_calls_over_the_element_limit():
    calls = []

    def handler(req):
        body = json.loads(req.content)
        calls.append((len(body["origins"]), len(body["destinations"])))
        rows = [
            (o, d, 600)
            for o in range(len(body["origins"]))
            for d in range(len(body["destinations"]))
        ]
        return httpx.Response(200, json=ok_body(rows))

    p = mock_provider(handler, max_elements=4)
    out = p.matrix([A, B, C], [A, B], DEP)  # 6 elements -> must split
    assert all(o * d <= 4 for o, d in calls) and len(calls) >= 2
    assert len(out) == 3 and len(out[0]) == 2 and all(e.seconds == 600 for r in out for e in r)


def test_google_key_never_appears_in_errors_or_repr():
    p = mock_provider(lambda r: httpx.Response(500, text="key=test-key-not-real"))
    with pytest.raises(ProviderError) as e:
        p.matrix([A], [B], DEP)
    assert "test-key-not-real" not in str(e.value) and "test-key-not-real" not in repr(p)


def test_google_requires_a_key():
    with pytest.raises(ProviderError):
        GoogleRoutesProvider("")
