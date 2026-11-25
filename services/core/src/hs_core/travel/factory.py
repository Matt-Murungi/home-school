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


class LiveTravelConfig:
    """TravelConfig whose business-rule fields are read from the settings service at every use
    (H05): a change by the super admin applies without a restart. Other fields: the base."""

    def __init__(self, base: TravelConfig, app_settings) -> None:
        self._base = base
        self._s = app_settings

    @property
    def cache_ttl(self) -> timedelta:
        return timedelta(days=int(self._s.get("travel.cache_ttl_days")))

    @property
    def within_zone_minutes(self) -> int:
        return int(self._s.get("travel.within_zone_minutes"))

    @property
    def fallback_speed_kmh(self) -> float:
        return float(self._s.get("travel.fallback_speed_kmh"))

    @property
    def fallback_circuity(self) -> float:
        return float(self._s.get("travel.fallback_circuity"))

    def __getattr__(self, name: str):
        return getattr(self._base, name)


def build_travel_service(
    settings: Settings,
    repo: Repository,
    *,
    app_settings=None,
    provider: TravelTimeProvider | None = None,
    alerts: TravelAlertSink | None = None,
    clock: Callable[[], datetime] | None = None,
) -> TravelTimeService:
    clock = clock or (lambda: datetime.now(UTC))
    alerts = alerts or LogTravelAlertSink()
    key = settings.google_maps_server_key.get_secret_value().strip()
    provider = provider or (GoogleRoutesProvider(key) if key else NullTravelTimeProvider())
    daily: int | Callable[[], int] = settings.travel_daily_element_budget
    monthly: int | Callable[[], int] = settings.travel_monthly_element_budget
    config = config_from_settings(settings)
    if app_settings is not None:  # H05: the super admin's value wins over the environment default
        daily = lambda: int(app_settings.get("travel.daily_element_budget"))  # noqa: E731
        monthly = lambda: int(app_settings.get("travel.monthly_element_budget"))  # noqa: E731
        config = LiveTravelConfig(config, app_settings)
    budget = BudgetGuard(
        RepositoryUsageStore(repo),
        daily_elements=daily,
        monthly_elements=monthly,
        cost_micro_usd_per_element=settings.travel_element_cost_micro_usd,
        alerts=alerts,
        clock=clock,
    )
    return TravelTimeService(
        provider,
        RepositoryTravelCacheStore(repo),
        budget,
        config,
        alerts=alerts,
        clock=clock,
        sleep=time.sleep,
    )
