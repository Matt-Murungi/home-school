"""X01 (#85): TravelTimeService: zone-pair cache, TTL, negative cache, single-flight, batching,
budget guard, fallback, poisoning checks, session lookups, metrics and log hygiene."""

import io
import json
import logging
import threading
import time
from datetime import timedelta

import pytest

from hs_core.logging_setup import JsonFormatter
from hs_core.repository import InMemoryRepository
from hs_core.travel.bands import band_for
from hs_core.travel.cache import CacheKey, RepositoryTravelCacheStore
from hs_core.travel.provider import (
    Element,
    FakeTravelTimeProvider,
    Point,
    ProviderError,
    ProviderQuotaExceeded,
    ProviderRateLimited,
    ProviderTimeout,
)
from hs_core.travel.service import TravelConfig, TravelEstimate, ZoneRef, needs_session_lookup
from travel_helpers import (
    ENTEBBE,
    EVENING,
    KOLOLO,
    MUNYONYO,
    NAKAWA,
    NTINDA,
    T0,
    ZONES,
    FakeClock,
    make_env,
    pt,
)

# --- identical zones, missing centroid --------------------------------------------------------


def test_identical_zones_use_the_within_zone_default_without_the_provider():
    e = make_env()
    got = e.svc.minutes(KOLOLO, KOLOLO, T0)
    assert got == TravelEstimate(10, False, "same_zone", band_for(T0))
    assert (
        e.provider.calls == [] and e.cache.get(CacheKey(KOLOLO.id, KOLOLO.id, band_for(T0))) is None
    )


@pytest.mark.parametrize(
    "bad",
    [
        ZoneRef("zone_new", None, None),
        ZoneRef("zone_bad", 0, 0),
        ZoneRef("zone_bad2", 999_999_999, 5),
    ],
)
def test_zone_without_a_usable_centroid_gets_a_flagged_default_and_no_call(bad):
    e = make_env()
    for a, b in ((bad, KOLOLO), (KOLOLO, bad)):
        got = e.svc.minutes(a, b, T0)
        assert (got.minutes, got.estimated, got.source) == (30, True, "default")
    assert e.provider.calls == []


# --- cache: AC "zone-pair matrix cached and refreshed weekly" ---------------------------------


def test_second_lookup_is_served_from_the_cache():
    e = make_env()
    e.provider.set(pt(KOLOLO), pt(NTINDA), seconds=1230)
    first = e.svc.minutes(KOLOLO, NTINDA, T0)
    second = e.svc.minutes(KOLOLO, NTINDA, T0 + timedelta(hours=1))
    assert (first.minutes, first.estimated, first.source) == (21, False, "provider")
    assert (second.minutes, second.estimated, second.source) == (21, False, "cache")
    assert len(e.provider.calls) == 1
    snap = e.svc.metrics.snapshot()
    assert snap["cache_misses"] == 1 and snap["cache_hits"] == 1 and snap["provider_calls"] == 1


def test_cache_key_is_origin_destination_and_time_band():
    e = make_env()
    e.provider.set(pt(KOLOLO), pt(NTINDA), seconds=1200)
    e.svc.minutes(KOLOLO, NTINDA, T0)  # off-peak
    e.svc.minutes(KOLOLO, NTINDA, EVENING)  # evening peak: a different key
    e.svc.minutes(KOLOLO, NTINDA, EVENING + timedelta(minutes=20))  # same band: hit
    assert len(e.provider.calls) == 2
    assert e.cache.get(CacheKey(KOLOLO.id, NTINDA.id, "off_peak")).minutes == 20
    assert e.cache.get(CacheKey(KOLOLO.id, NTINDA.id, "evening_peak")).minutes == 20


def test_provider_is_asked_for_the_band_representative_departure_time():
    e = make_env()
    e.svc.minutes(KOLOLO, NTINDA, EVENING)
    dep = e.provider.calls[0].departure
    assert dep > EVENING and band_for(dep) == "evening_peak"


def test_travel_times_are_asymmetric():
    e = make_env()
    e.provider.set(pt(KOLOLO), pt(NTINDA), seconds=1200)
    e.provider.set(pt(NTINDA), pt(KOLOLO), seconds=1800)
    assert e.svc.minutes(KOLOLO, NTINDA, T0).minutes == 20
    assert e.svc.minutes(NTINDA, KOLOLO, T0).minutes == 30
    assert len(e.provider.calls) == 2


def test_entry_is_fresh_for_six_days_and_refreshed_after_eight():
    e = make_env()
    e.provider.set(pt(KOLOLO), pt(NTINDA), seconds=1200)
    e.svc.minutes(KOLOLO, NTINDA, T0)
    e.clock.advance(days=6)
    assert e.svc.minutes(KOLOLO, NTINDA, e.clock()).source == "cache"
    e.provider.set(pt(KOLOLO), pt(NTINDA), seconds=1500)
    e.clock.advance(days=2)
    got = e.svc.minutes(KOLOLO, NTINDA, e.clock())
    assert (got.minutes, got.source) == (25, "provider") and len(e.provider.calls) == 2


def test_expiry_is_jittered_so_a_weekly_refresh_does_not_stampede():
    e = make_env()
    e.svc.minutes_many([KOLOLO, NTINDA, NAKAWA, MUNYONYO], ENTEBBE, T0)
    spans = {
        (e.cache.get(CacheKey(o.id, ENTEBBE.id, "off_peak")).expires_at - T0)
        for o in (KOLOLO, NTINDA, NAKAWA, MUNYONYO)
    }
    assert len(spans) > 1
    assert all(timedelta(days=6.3) <= s <= timedelta(days=7.7) for s in spans)


def test_new_zone_row_is_computed_on_demand_only():
    e = make_env()
    e.svc.minutes_many([KOLOLO, NTINDA], NAKAWA, T0)
    assert len(e.provider.calls) == 1
    fresh = ZoneRef("zone_brand_new", 340_000, 32_600_000)
    got = e.svc.minutes_many([KOLOLO, NTINDA, fresh], NAKAWA, T0)
    assert got[fresh.id].source == "provider" and not got[fresh.id].estimated
    assert e.provider.calls[1].n_origins == 1  # only the new pair was fetched


# --- batch ------------------------------------------------------------------------------------


def test_n_teachers_to_one_child_zone_is_a_single_provider_call():
    e = make_env()
    out = e.svc.minutes_many([KOLOLO, NTINDA, NAKAWA, MUNYONYO], ENTEBBE, T0)
    assert set(out) == {z.id for z in (KOLOLO, NTINDA, NAKAWA, MUNYONYO)}
    assert len(e.provider.calls) == 1
    assert (e.provider.calls[0].n_origins, e.provider.calls[0].n_dests) == (4, 1)
    e.svc.minutes_many([KOLOLO, NTINDA, NAKAWA, MUNYONYO], ENTEBBE, T0)
    assert len(e.provider.calls) == 1


def test_batch_skips_cached_same_zone_and_duplicate_origins():
    e = make_env()
    e.svc.minutes(KOLOLO, ENTEBBE, T0)
    out = e.svc.minutes_many([KOLOLO, NTINDA, NTINDA, ENTEBBE], ENTEBBE, T0)
    assert out[ENTEBBE.id].source == "same_zone"
    assert out[KOLOLO.id].source == "cache"
    assert len(e.provider.calls) == 2 and e.provider.calls[1].n_origins == 1


def test_batch_larger_than_the_provider_limit_is_split():
    prov = FakeTravelTimeProvider(max_elements=3)
    e = make_env(provider=prov)
    e.svc.minutes_many([KOLOLO, NTINDA, NAKAWA, MUNYONYO], ENTEBBE, T0)
    assert len(prov.calls) == 2 and all(c.n_origins * c.n_dests <= 3 for c in prov.calls)


# --- single flight ----------------------------------------------------------------------------


def run_threads(fn, n=8):
    out, errs = [], []

    def work():
        try:
            out.append(fn())
        except Exception as exc:  # noqa: BLE001
            errs.append(exc)

    ts = [threading.Thread(target=work) for _ in range(n)]
    for t in ts:
        t.start()
    return ts, out, errs


def test_concurrent_requests_for_one_pair_make_one_provider_call():
    prov = FakeTravelTimeProvider()
    prov.gate = threading.Event()
    e = make_env(provider=prov)
    ts, out, errs = run_threads(lambda: e.svc.minutes(KOLOLO, NTINDA, T0))
    assert prov.entered.wait(5)
    time.sleep(0.2)  # let the others reach the in-flight marker
    prov.gate.set()
    for t in ts:
        t.join(10)
    assert errs == [] and len(out) == 8 and len({o.minutes for o in out}) == 1
    assert len(prov.calls) == 1


def test_concurrent_batches_share_in_flight_pairs():
    prov = FakeTravelTimeProvider()
    prov.gate = threading.Event()
    e = make_env(provider=prov)
    ts, out, errs = run_threads(lambda: e.svc.minutes_many([KOLOLO, NTINDA], NAKAWA, T0), n=6)
    assert prov.entered.wait(5)
    time.sleep(0.2)
    prov.gate.set()
    for t in ts:
        t.join(10)
    assert errs == [] and len(out) == 6 and len(prov.calls) == 1


def test_stale_entry_refresh_stampede_makes_one_call():
    prov = FakeTravelTimeProvider()
    e = make_env(provider=prov)
    e.svc.minutes(KOLOLO, NTINDA, T0)
    e.clock.advance(days=9)
    prov.gate = threading.Event()
    prov.entered.clear()
    ts, out, errs = run_threads(lambda: e.svc.minutes(KOLOLO, NTINDA, e.clock()))
    assert prov.entered.wait(5)
    time.sleep(0.2)
    prov.gate.set()
    for t in ts:
        t.join(10)
    assert errs == [] and len(prov.calls) == 2  # the first fill + exactly one refresh


def test_a_late_claimer_with_a_stale_snapshot_rechecks_the_cache_before_calling():
    """Thread B read the cache before A's refresh landed, then claims after A is done."""
    e = make_env()
    e.svc.minutes(KOLOLO, NTINDA, T0)
    assert len(e.provider.calls) == 1
    got, denied = e.svc._refresh_group([KOLOLO], [NTINDA], band_for(T0), entries={})
    assert [v.source for v in got.values()] == ["cache"] and not denied
    assert len(e.provider.calls) == 1


def test_failed_flight_releases_waiters_with_a_fallback_and_one_call():
    prov = FakeTravelTimeProvider()
    prov.gate = threading.Event()
    prov.always_fail = ProviderError("down", retryable=False)
    e = make_env(provider=prov)
    ts, out, errs = run_threads(lambda: e.svc.minutes(KOLOLO, NTINDA, T0))
    assert prov.entered.wait(5)
    time.sleep(0.2)
    prov.gate.set()
    for t in ts:
        t.join(10)
    assert errs == [] and all(o.estimated for o in out) and len(prov.calls) == 1


# --- failures: fallback, negative cache, backoff ----------------------------------------------

ERRORS = [
    ProviderTimeout("slow"),
    ProviderRateLimited("429", retry_after=None),
    ProviderQuotaExceeded("over quota"),
    ProviderError("500", retryable=True),
    ProviderError("bad request", retryable=False),
    RuntimeError("unexpected bug in adapter"),
]


@pytest.mark.parametrize("exc", ERRORS, ids=lambda x: type(x).__name__)
def test_provider_failure_falls_back_to_straight_line_flagged_estimated(exc):
    prov = FakeTravelTimeProvider()
    prov.always_fail = exc
    e = make_env(provider=prov)
    got = e.svc.minutes(KOLOLO, ENTEBBE, T0)
    # 34.3 km straight x 1.4 / 20 km/h = about 144 minutes
    assert got.estimated and got.source == "haversine" and 135 <= got.minutes <= 155
    assert e.svc.metrics.snapshot()["fallbacks"] == 1


def test_fallback_uses_the_configured_speed():
    prov = FakeTravelTimeProvider()
    prov.always_fail = ProviderError("down", retryable=False)
    slow = make_env(provider=prov, config=TravelConfig(fallback_speed_kmh=10.0))
    fast = make_env(provider=prov, config=TravelConfig(fallback_speed_kmh=40.0))
    assert (
        slow.svc.minutes(KOLOLO, NTINDA, T0).minutes
        > fast.svc.minutes(KOLOLO, NTINDA, T0).minutes * 3
    )


def test_cache_store_outage_never_fails_the_lookup():
    repo = InMemoryRepository()
    e = make_env()
    e.svc._cache = RepositoryTravelCacheStore(repo)
    repo.available = False
    got = e.svc.minutes(KOLOLO, NTINDA, T0)
    assert got.minutes >= 1  # served (provider answer), not raised


def test_failure_is_negatively_cached_for_a_short_time():
    prov = FakeTravelTimeProvider()
    prov.always_fail = ProviderError("down", retryable=False)
    e = make_env(provider=prov)
    e.svc.minutes(KOLOLO, NTINDA, T0)
    e.clock.advance(seconds=60)
    e.svc.minutes(KOLOLO, NTINDA, e.clock())
    assert len(prov.calls) == 1 and e.svc.metrics.snapshot()["negative_hits"] == 1
    prov.always_fail = None
    e.clock.advance(seconds=300)  # past the 5 minute negative TTL
    assert e.svc.minutes(KOLOLO, NTINDA, e.clock()).source == "provider"
    entry = e.cache.get(CacheKey(KOLOLO.id, NTINDA.id, band_for(T0)))
    assert entry.fail_count == 0 and entry.retry_at is None


def test_negative_entry_does_not_destroy_the_last_good_value():
    prov = FakeTravelTimeProvider()
    prov.set(pt(KOLOLO), pt(NTINDA), seconds=1200)
    e = make_env(provider=prov)
    e.svc.minutes(KOLOLO, NTINDA, T0)
    e.clock.advance(days=8)
    prov.always_fail = ProviderError("down", retryable=False)
    got = e.svc.minutes(KOLOLO, NTINDA, e.clock())
    assert (got.minutes, got.estimated, got.source) == (20, True, "stale_cache")
    entry = e.cache.get(CacheKey(KOLOLO.id, NTINDA.id, band_for(T0)))
    assert entry.minutes == 20 and entry.retry_at is not None and entry.fail_count == 1


def test_no_route_result_is_a_fallback_with_a_long_negative_ttl():
    prov = FakeTravelTimeProvider()
    prov.set(pt(KOLOLO), pt(NTINDA), status="no_route")
    e = make_env(provider=prov)
    got = e.svc.minutes(KOLOLO, NTINDA, T0)
    assert got.estimated and got.source == "haversine"
    e.clock.advance(hours=3)
    e.svc.minutes(KOLOLO, NTINDA, e.clock())
    assert len(prov.calls) == 1  # still negatively cached after 3 h
    assert e.cache.get(CacheKey(KOLOLO.id, NTINDA.id, band_for(T0))).failure_reason == "no_route"


def test_retryable_errors_back_off_exponentially_and_then_give_up():
    prov = FakeTravelTimeProvider()
    prov.always_fail = ProviderTimeout("slow")
    e = make_env(provider=prov)
    got = e.svc.minutes(KOLOLO, NTINDA, T0)
    assert got.estimated and len(prov.calls) == 3 and e.sleeps == [0.5, 1.0]


def test_a_transient_failure_is_retried_and_succeeds():
    prov = FakeTravelTimeProvider()
    prov.queue_error(ProviderTimeout("slow"))
    e = make_env(provider=prov)
    got = e.svc.minutes(KOLOLO, NTINDA, T0)
    assert got.source == "provider" and not got.estimated and e.sleeps == [0.5]


def test_rate_limit_honours_a_short_retry_after_but_never_a_long_one():
    prov = FakeTravelTimeProvider()
    prov.queue_error(ProviderRateLimited("429", retry_after=2.0))
    e = make_env(provider=prov)
    assert e.svc.minutes(KOLOLO, NTINDA, T0).source == "provider" and e.sleeps == [2.0]

    prov2 = FakeTravelTimeProvider()
    prov2.queue_error(ProviderRateLimited("429", retry_after=600.0))
    e2 = make_env(provider=prov2)
    got = e2.svc.minutes(KOLOLO, NTINDA, T0)
    assert got.estimated and e2.sleeps == [] and len(prov2.calls) == 1  # never blocks on 600 s
    assert e2.svc.metrics.snapshot()["rate_limited"] == 1


def test_quota_exceeded_is_not_retried_alerts_and_backs_off_longer():
    prov = FakeTravelTimeProvider()
    prov.always_fail = ProviderQuotaExceeded("over quota")
    e = make_env(provider=prov)
    e.svc.minutes(KOLOLO, NTINDA, T0)
    assert len(prov.calls) == 1 and e.sleeps == []
    assert "provider_quota_exceeded" in [a.kind for a in e.alerts.sent]
    e.clock.advance(minutes=20)
    e.svc.minutes(KOLOLO, NTINDA, e.clock())
    assert len(prov.calls) == 1  # 30 min negative TTL for quota


# --- cache poisoning --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "seconds",
    [0, -5, 7 * 3600, 6 * 3600 + 1],
    ids=["zero", "negative", "seven-hours", "just-over-six-hours"],
)
def test_nonsensical_durations_are_rejected_and_never_cached(seconds):
    prov = FakeTravelTimeProvider()
    prov.set(pt(KOLOLO), pt(NTINDA), seconds=seconds)
    e = make_env(provider=prov)
    got = e.svc.minutes(KOLOLO, NTINDA, T0)
    assert got.estimated and got.source == "haversine"
    entry = e.cache.get(CacheKey(KOLOLO.id, NTINDA.id, band_for(T0)))
    assert entry.minutes is None and entry.failure_reason == "implausible"
    assert e.svc.metrics.snapshot()["rejected_responses"] == 1


def test_impossible_speed_is_rejected():
    prov = FakeTravelTimeProvider()
    prov.set(pt(KOLOLO), pt(ENTEBBE), seconds=300)  # 34 km in 5 minutes
    e = make_env(provider=prov)
    assert e.svc.minutes(KOLOLO, ENTEBBE, T0).estimated


def test_bad_response_keeps_the_previous_good_value():
    prov = FakeTravelTimeProvider()
    prov.set(pt(KOLOLO), pt(NTINDA), seconds=1200)
    e = make_env(provider=prov)
    e.svc.minutes(KOLOLO, NTINDA, T0)
    e.clock.advance(days=8)
    prov.set(pt(KOLOLO), pt(NTINDA), seconds=0)
    got = e.svc.minutes(KOLOLO, NTINDA, e.clock())
    assert (got.minutes, got.source, got.estimated) == (20, "stale_cache", True)
    assert e.cache.get(CacheKey(KOLOLO.id, NTINDA.id, band_for(T0))).minutes == 20


def test_a_good_value_for_one_pair_survives_a_bad_one_in_the_same_batch():
    prov = FakeTravelTimeProvider()
    prov.set(pt(KOLOLO), pt(ENTEBBE), seconds=0)
    prov.set(pt(NTINDA), pt(ENTEBBE), seconds=3000)
    e = make_env(provider=prov)
    out = e.svc.minutes_many([KOLOLO, NTINDA], ENTEBBE, T0)
    assert out[KOLOLO.id].estimated and not out[NTINDA.id].estimated


# --- budget: daily cap, midnight reset, monthly alert -----------------------------------------


def test_daily_cap_returns_fallbacks_and_alerts_once_and_never_fails():
    e = make_env(daily=3)
    out = e.svc.minutes_many(
        [KOLOLO, NTINDA, NAKAWA, MUNYONYO, ZoneRef("zone_x", 345_000, 32_600_000)], ENTEBBE, T0
    )
    assert sum(1 for v in out.values() if v.source == "provider") == 3
    assert sum(1 for v in out.values() if v.estimated and v.source == "haversine") == 2
    again = e.svc.minutes_many([KOLOLO, NTINDA, NAKAWA, MUNYONYO], ENTEBBE, T0)
    assert (
        len(e.provider.calls) == 1
        and [a.kind for a in e.alerts.sent].count("daily_budget_exhausted") == 1
    )
    assert sum(1 for v in again.values() if v.source == "cache") >= 2
    assert e.svc.metrics.snapshot()["budget_denied"] >= 2


def test_over_budget_serves_last_known_value_flagged_estimated():
    prov = FakeTravelTimeProvider()
    prov.set(pt(KOLOLO), pt(NTINDA), seconds=1200)
    e = make_env(provider=prov, daily=1)
    e.svc.minutes(KOLOLO, NTINDA, T0)
    e.clock.advance(days=8)  # next days: fresh budget, but cap is 1 and we spend it elsewhere
    e.svc.minutes(KOLOLO, NAKAWA, e.clock())
    got = e.svc.minutes(KOLOLO, NTINDA, e.clock())
    assert (got.minutes, got.estimated, got.source) == (20, True, "stale_cache")


def test_budget_resets_at_midnight_in_kampala_not_utc():
    clock = FakeClock(T0.replace(hour=20, minute=59, second=30))  # 23:59:30 Kampala
    e = make_env(daily=1, clock=clock)
    assert e.svc.minutes(KOLOLO, NTINDA, clock()).source == "provider"
    assert e.svc.minutes(KOLOLO, NAKAWA, clock()).source == "haversine"  # cap hit
    clock.advance(minutes=1)  # 00:00:30 Kampala = 21:00:30 UTC (UTC has not rolled over)
    assert e.svc.minutes(KOLOLO, NAKAWA, clock()).source == "provider"


def test_utc_midnight_in_the_middle_of_a_kampala_day_does_not_reset_the_budget():
    clock = FakeClock(T0.replace(hour=21, minute=30))  # 00:30 Kampala on the 19th
    e = make_env(daily=1, clock=clock)
    e.svc.minutes(KOLOLO, NTINDA, clock())
    clock.advance(hours=2, minutes=40)  # 00:10 UTC on the 19th... = 03:10 Kampala, same day
    assert e.svc.minutes(KOLOLO, NAKAWA, clock()).source == "haversine"


def test_monthly_budget_alerts_at_80_and_100_percent_once_each_and_resets_next_month():
    e = make_env(daily=1000, monthly=10)
    assert e.budget.reserve(7) == 7 and e.alerts.sent == []
    assert e.budget.reserve(1) == 1
    assert [(a.kind, a.detail["pct"]) for a in e.alerts.sent] == [("monthly_budget_threshold", 80)]
    assert e.budget.reserve(2) == 2
    assert [a.detail["pct"] for a in e.alerts.sent] == [80, 100]
    assert e.budget.reserve(1) == 0  # hard stop at the monthly cap
    assert [a.detail["pct"] for a in e.alerts.sent] == [80, 100]
    e.clock.advance(days=32)
    assert e.budget.reserve(8) == 8
    assert [a.detail["pct"] for a in e.alerts.sent] == [80, 100, 80]


def test_cost_is_tracked_in_integer_micro_usd():
    e = make_env()
    e.svc.minutes_many([KOLOLO, NTINDA, NAKAWA], ENTEBBE, T0)
    assert e.svc.metrics.snapshot()["cost_micro_usd"] == 3 * 10_000
    assert isinstance(e.budget.usage_today()["cost_micro_usd"], int)


def test_budget_state_survives_a_restart_through_the_usage_store():
    from hs_core.travel.budget import BudgetGuard

    e = make_env(daily=2)
    e.budget.reserve(2)
    fresh = BudgetGuard(
        e.usage,
        daily_elements=2,
        monthly_elements=100,
        cost_micro_usd_per_element=1,
        alerts=e.alerts,
        clock=e.clock,
    )
    assert fresh.reserve(1) == 0


# --- per-session lookups only for close calls -------------------------------------------------


def test_needs_session_lookup_only_near_the_limit():
    assert needs_session_lookup(estimate_minutes=38, limit_minutes=40, margin_minutes=5)
    assert needs_session_lookup(estimate_minutes=44, limit_minutes=40, margin_minutes=5)
    assert not needs_session_lookup(estimate_minutes=20, limit_minutes=40, margin_minutes=5)
    assert not needs_session_lookup(estimate_minutes=70, limit_minutes=40, margin_minutes=5)


HOME_A, HOME_B = Point(341_234, 32_598_765), Point(338_001, 32_604_321)


def test_session_lookup_is_not_made_unless_close_call():
    e = make_env()
    got = e.svc.session_minutes(KOLOLO, NTINDA, HOME_A, HOME_B, T0, close_call=False)
    assert got.source == "provider" and len(e.provider.calls) == 1
    assert (e.provider.calls[0].origins, e.provider.calls[0].dests) == (
        (pt(KOLOLO),),
        (pt(NTINDA),),
    )


def test_close_call_asks_the_provider_about_the_exact_points_and_counts_the_budget():
    e = make_env()
    e.provider.set(HOME_A, HOME_B, seconds=900)
    got = e.svc.session_minutes(KOLOLO, NTINDA, HOME_A, HOME_B, T0, close_call=True)
    assert (got.minutes, got.source, got.estimated) == (15, "session", False)
    assert e.provider.calls[-1].origins == (HOME_A,)
    assert e.svc.metrics.snapshot()["close_call_lookups"] == 1
    assert e.usage.get(e.budget.day_key())["elements"] == 1
    # home coordinates are never written to the cache
    assert all(entry.origin_zone_id.startswith("zone_") for entry in e.cache.all())


def test_close_call_failure_or_budget_falls_back_to_the_zone_pair_estimate():
    e = make_env(daily=1)
    e.svc.minutes(KOLOLO, NTINDA, T0)  # spends the whole daily budget
    got = e.svc.session_minutes(KOLOLO, NTINDA, HOME_A, HOME_B, T0, close_call=True)
    assert got.source == "cache" and not got.estimated

    prov = FakeTravelTimeProvider()
    e2 = make_env(provider=prov)
    e2.svc.minutes(KOLOLO, NTINDA, T0)
    prov.always_fail = ProviderTimeout("slow")
    got2 = e2.svc.session_minutes(KOLOLO, NTINDA, HOME_A, HOME_B, T0, close_call=True)
    assert got2.source == "cache"


# --- metrics and log hygiene ------------------------------------------------------------------


def capture_logs():
    buf = io.StringIO()
    h = logging.StreamHandler(buf)
    h.setFormatter(JsonFormatter())
    lg = logging.getLogger("hs_core.travel")
    lg.addHandler(h)
    lg.setLevel(logging.DEBUG)
    return buf, h, lg


def test_logs_carry_zone_ids_and_never_coordinates():
    buf, h, lg = capture_logs()
    try:
        prov = FakeTravelTimeProvider()
        e = make_env(provider=prov)
        e.svc.minutes(KOLOLO, NTINDA, T0)
        e.svc.minutes(KOLOLO, NTINDA, T0)
        e.svc.session_minutes(KOLOLO, NTINDA, HOME_A, HOME_B, T0, close_call=True)
        prov.always_fail = ProviderError("down at 0.341234,32.598765", retryable=False)
        e.svc.minutes(KOLOLO, NAKAWA, T0)
        e.svc.minutes(ZoneRef("zone_out", 51_500_000, -127_000), KOLOLO, T0)
        e.svc.metrics.log_snapshot()
    finally:
        lg.removeHandler(h)
    text = buf.getvalue()
    assert "zone_kololo" in text and "zone_ntinda" in text
    for needle in (
        "341234",
        "32598765",
        "32.598765",
        "0.341234",
        "338001",
        "32604321",
        "333000",
        "32590000",
    ):
        assert needle not in text, needle
    for line in text.splitlines():
        json.loads(line)  # every line is valid structured JSON


def test_metrics_snapshot_counts_calls_hits_misses_fallbacks_cost():
    prov = FakeTravelTimeProvider()
    e = make_env(provider=prov)
    e.svc.minutes(KOLOLO, NTINDA, T0)
    e.svc.minutes(KOLOLO, NTINDA, T0)
    prov.always_fail = ProviderError("down", retryable=False)
    e.svc.minutes(KOLOLO, NAKAWA, T0)
    s = e.svc.metrics.snapshot()
    assert (s["provider_calls"], s["cache_hits"], s["cache_misses"], s["fallbacks"]) == (2, 1, 2, 1)
    assert s["provider_failures"] == 1 and s["cost_micro_usd"] == 20_000
    assert all(isinstance(v, int) for v in s.values())


def test_centroid_outside_uganda_warns_once_but_is_still_used(caplog):
    caplog.set_level("WARNING", logger="hs_core.travel")
    e = make_env()
    far = ZoneRef("zone_far", 51_500_000, -127_000)
    e.svc.minutes(far, KOLOLO, T0)
    e.svc.minutes(far, NTINDA, T0)
    msgs = [r for r in caplog.records if getattr(r, "zone_id", "") == "zone_far"]
    assert len(msgs) == 1 and "outside_uganda" in msgs[0].getMessage()


def test_every_zone_pair_of_the_seed_zones_can_be_answered():
    e = make_env()
    for dest in ZONES:
        out = e.svc.minutes_many(ZONES, dest, T0)
        assert len(out) == len(ZONES) and all(v.minutes >= 1 for v in out.values())
    assert e.svc.metrics.snapshot()["provider_calls"] == len(ZONES)
    assert Element  # imported for type clarity
