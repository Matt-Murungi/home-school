"""Build the TravelTimeService from Settings. No key = straight-line only (dev, CI, and any
deployment before the owner creates the Google Maps key): matching still works, flagged
`estimated=true`."""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from ..repository import Repository
from ..settings import Settings
from .alerts import LogTravelAlertSink, TravelAlertSink
from .budget import BudgetGuard, RepositoryUsageStore
from .cache import RepositoryTravelCacheStore
from .provider import GoogleRoutesProvider, ProviderError, TravelTimeProvider
from .service import TravelConfig, TravelTimeService


class NullTravelTimeProvider:
    """Used when no API key is configured: every call is a clean, free, non-retryable failure."""

    max_elements = 100
    billable = False

    def matrix(self, origins, dests, departure):
        raise ProviderError("no Google Maps key configured", retryable=False)


def config_from_settings(s: Settings) -> TravelConfig:
    return TravelConfig(
        cache_ttl=timedelta(days=s.travel_cache_ttl_days),
        negative_ttl=timedelta(seconds=s.travel_negative_ttl_s),
        fallback_speed_kmh=s.travel_fallback_speed_kmh,
        fallback_circuity=s.travel_fallback_circuity,
        within_zone_minutes=s.travel_within_zone_minutes,
        warm_bands=tuple(b.strip() for b in s.travel_warm_bands.split(",") if b.strip()),
    )


def build_travel_service(
    settings: Settings,
    repo: Repository,
    *,
    provider: TravelTimeProvider | None = None,
    alerts: TravelAlertSink | None = None,
    clock: Callable[[], datetime] | None = None,
) -> TravelTimeService:
    clock = clock or (lambda: datetime.now(UTC))
    alerts = alerts or LogTravelAlertSink()
    key = settings.google_maps_server_key.get_secret_value().strip()
    provider = provider or (GoogleRoutesProvider(key) if key else NullTravelTimeProvider())
    budget = BudgetGuard(
        RepositoryUsageStore(repo),
        daily_elements=settings.travel_daily_element_budget,
        monthly_elements=settings.travel_monthly_element_budget,
        cost_micro_usd_per_element=settings.travel_element_cost_micro_usd,
        alerts=alerts,
        clock=clock,
    )
    return TravelTimeService(
        provider,
        RepositoryTravelCacheStore(repo),
        budget,
        config_from_settings(settings),
        alerts=alerts,
        clock=clock,
        sleep=time.sleep,
    )
