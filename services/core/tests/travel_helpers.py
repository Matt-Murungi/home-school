"""Helpers for the travel-time tests: Kampala-ish zones, fake clock, wired service."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from hs_core.travel.alerts import FakeTravelAlertSink
from hs_core.travel.budget import BudgetGuard, InMemoryUsageStore
from hs_core.travel.cache import InMemoryTravelCacheStore
from hs_core.travel.provider import FakeTravelTimeProvider, Point
from hs_core.travel.service import TravelConfig, TravelTimeService, ZoneRef

# Monday 18 Jan 2027 09:00 UTC = 12:00 Africa/Kampala (off-peak).
T0 = datetime(2027, 1, 18, 9, 0, tzinfo=UTC)
EVENING = datetime(2027, 1, 18, 14, 30, tzinfo=UTC)  # 17:30 Kampala, evening peak

# Integer micro-degree centroids (approximate real Kampala-area places; zone ids are opaque).
KOLOLO = ZoneRef("zone_kololo", 333_000, 32_590_000)
NTINDA = ZoneRef("zone_ntinda", 353_000, 32_615_000)
NAKAWA = ZoneRef("zone_nakawa", 330_000, 32_620_000)
MUNYONYO = ZoneRef("zone_munyonyo", 240_000, 32_620_000)
ENTEBBE = ZoneRef("zone_entebbe", 51_200, 32_463_700)
ZONES = [KOLOLO, NTINDA, NAKAWA, MUNYONYO, ENTEBBE]


def pt(z: ZoneRef) -> Point:
    return Point(z.lat_e6, z.lng_e6)


class FakeClock:
    def __init__(self, now: datetime = T0) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kw) -> None:
        from datetime import timedelta

        self.now += timedelta(**kw)


def make_env(
    *,
    daily=10_000,
    monthly=100_000,
    config: TravelConfig | None = None,
    provider=None,
    clock: FakeClock | None = None,
):
    clock = clock or FakeClock()
    provider = provider or FakeTravelTimeProvider()
    cache = InMemoryTravelCacheStore()
    alerts = FakeTravelAlertSink()
    usage = InMemoryUsageStore()
    budget = BudgetGuard(
        usage,
        daily_elements=daily,
        monthly_elements=monthly,
        cost_micro_usd_per_element=10_000,
        alerts=alerts,
        clock=clock,
    )
    sleeps: list[float] = []
    svc = TravelTimeService(
        provider,
        cache,
        budget,
        config or TravelConfig(),
        alerts=alerts,
        clock=clock,
        sleep=sleeps.append,
    )
    return SimpleNamespace(
        svc=svc,
        provider=provider,
        cache=cache,
        alerts=alerts,
        usage=usage,
        budget=budget,
        clock=clock,
        sleeps=sleeps,
    )
