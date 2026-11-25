"""X01 (#85): travel-cache-refresh job (F04 schedule), warming, store, settings, wiring."""

import ast
import json
from datetime import timedelta
from pathlib import Path

import pytest

from hs_core.jobs import schedule
from hs_core.jobs.model import Continue, Done, JobContext
from hs_core.repository import InMemoryRepository
from hs_core.settings import Settings
from hs_core.travel import jobs as tjobs
from hs_core.travel.bands import band_for
from hs_core.travel.budget import RepositoryUsageStore
from hs_core.travel.cache import (
    CacheEntry,
    CacheKey,
    InMemoryTravelCacheStore,
    RepositoryTravelCacheStore,
)
from hs_core.travel.factory import build_travel_service
from hs_core.travel.provider import FakeTravelTimeProvider, GoogleRoutesProvider, ProviderError
from hs_core.travel.service import TravelConfig
from travel_helpers import ENTEBBE, KOLOLO, MUNYONYO, NAKAWA, NTINDA, T0, ZONES, make_env, pt

BANDS = ("evening_peak", "off_peak")


def ctx(cursor=None, chunk=0):
    return JobContext(
        "travel-cache-refresh", "r1", "travel-cache-refresh:p", T0, {}, cursor, chunk, None
    )


def zone_rows(zones=ZONES, **extra):
    return [
        {
            "$id": z.id,
            "name": z.id,
            "centroid_lat_e6": z.lat_e6,
            "centroid_lng_e6": z.lng_e6,
            "deleted_at": None,
            **extra,
        }
        for z in zones
    ]


def seed_repo(rows):
    repo = InMemoryRepository()
    for r in rows:
        rid = r["$id"]
        repo.create("zone", rid, {k: v for k, v in r.items() if k != "$id"})
    return repo


def run_job(env, repo, **kw):
    """Drive the job to completion, like the runner would (chunk after chunk)."""
    tjobs.configure(env.svc, repo, **kw)
    cursor, chunks, items = None, 0, 0
    while True:
        out = tjobs.travel_cache_refresh(ctx(cursor, chunks))
        items += out.items
        chunks += 1
        if isinstance(out, Done):
            return out, chunks, items
        assert isinstance(out, Continue) and chunks < 50
        cursor = out.cursor


@pytest.fixture(autouse=True)
def _reset():
    yield
    tjobs.configure(None, None)


def test_job_is_in_the_one_schedule_file_with_a_daily_kampala_slot():
    job = next(j for j in schedule.JOBS if j.name == "travel-cache-refresh")
    assert job.handler is tjobs.travel_cache_refresh
    assert job.schedule.every == timedelta(days=1)
    anchor = job.schedule.anchor.astimezone(__import__("zoneinfo").ZoneInfo("Africa/Kampala"))
    assert (anchor.hour, anchor.minute) == (2, 0)  # quiet hour, after the Kampala budget reset


def test_unconfigured_job_is_a_clean_noop():
    tjobs.configure(None, None)
    assert tjobs.travel_cache_refresh(ctx()) == Done(
        detail="travel service not configured", items=0
    )


def test_job_computes_the_matrix_for_seed_zones_in_chunks():
    env = make_env()
    repo = seed_repo(zone_rows())
    out, chunks, items = run_job(env, repo, bands=BANDS)
    assert chunks == len(ZONES)  # one origin per chunk
    n = len(ZONES)
    assert items == n * (n - 1) * len(BANDS)
    for o in ZONES:
        for d in ZONES:
            if o.id != d.id:
                for band in BANDS:
                    assert env.cache.get(CacheKey(o.id, d.id, band)).minutes >= 1
    # each call is one origin to all destinations
    assert all(c.n_origins == 1 and c.n_dests == n - 1 for c in env.provider.calls)


def test_job_rerun_is_idempotent_and_makes_no_provider_calls():
    env = make_env()
    repo = seed_repo(zone_rows())
    run_job(env, repo, bands=BANDS)
    calls = len(env.provider.calls)
    out, _, items = run_job(env, repo, bands=BANDS)
    assert items == 0 and len(env.provider.calls) == calls


def test_job_refreshes_only_entries_about_to_expire():
    env = make_env()
    repo = seed_repo(zone_rows())
    run_job(env, repo, bands=("off_peak",))
    calls = len(env.provider.calls)
    env.clock.advance(days=6, hours=20)  # inside the 1-day refresh-ahead window (given jitter)
    run_job(env, repo, bands=("off_peak",))
    assert len(env.provider.calls) > calls


def test_job_respects_the_budget_and_stops_cleanly():
    env = make_env(daily=30)  # warming may use at most half of it (15 elements)
    repo = seed_repo(zone_rows())
    out, _, items = run_job(env, repo, bands=BANDS)
    assert isinstance(out, Done) and "budget" in out.detail
    assert env.usage.get(env.budget.day_key())["elements"] <= 15
    assert env.alerts.sent == []  # warming stopping early is not an exhausted-budget alert


def test_job_survives_provider_failure_and_reports_it():
    prov = FakeTravelTimeProvider()
    prov.always_fail = ProviderError("down", retryable=False)
    env = make_env(provider=prov)
    repo = seed_repo(zone_rows())
    out, _, items = run_job(env, repo, bands=("off_peak",))
    assert isinstance(out, Done) and "failed" in out.detail
    assert items == 0


def test_job_skips_deleted_zones_and_zones_without_centroid():
    rows = zone_rows()
    rows[1]["deleted_at"] = "2027-01-01T00:00:00+00:00"
    rows[2]["centroid_lat_e6"] = None
    env = make_env()
    repo = seed_repo(rows)
    run_job(env, repo, bands=("off_peak",))
    ids = {c.origins[0] for c in env.provider.calls}
    assert pt(NTINDA) not in ids and pt(NAKAWA) not in ids
    assert all(pt(NTINDA) not in c.dests and pt(NAKAWA) not in c.dests for c in env.provider.calls)


def test_job_polls_cancellation_between_provider_calls():
    import threading

    env = make_env()
    repo = seed_repo(zone_rows())
    tjobs.configure(env.svc, repo, bands=BANDS)
    ev = threading.Event()
    ev.set()
    c = JobContext("travel-cache-refresh", "r", "k", T0, {}, None, 0, ev)
    out = tjobs.travel_cache_refresh(c)
    assert env.provider.calls == [] and isinstance(out, Continue | Done)


def test_job_chunk_logs_zone_ids_only(caplog):
    caplog.set_level("INFO", logger="hs_core.travel")
    env = make_env()
    repo = seed_repo(zone_rows())
    run_job(env, repo, bands=("off_peak",))
    text = json.dumps([r.__dict__.get("zone_id", "") for r in caplog.records], default=str)
    assert "32590000" not in "".join(r.getMessage() for r in caplog.records) and text


# --- store ------------------------------------------------------------------------------------


def test_repository_cache_store_round_trips_and_uses_deterministic_ids():
    repo = InMemoryRepository()
    store = RepositoryTravelCacheStore(repo)
    key = CacheKey("zone_a", "zone_b", "off_peak")
    assert store.get(key) is None
    entry = CacheEntry(
        key.origin_zone_id, key.dest_zone_id, key.band, 21, T0, T0 + timedelta(days=7)
    )
    store.put(entry)
    got = store.get(key)
    assert got.minutes == 21 and got.expires_at == T0 + timedelta(days=7) and got.retry_at is None
    store.put(CacheEntry(*key, 22, T0, T0 + timedelta(days=7)))  # update path (409 -> update)
    assert store.get(key).minutes == 22 and len(repo.list("travel_time_cache")) == 1
    assert store.get_many([key, CacheKey("zone_x", "zone_b", "off_peak")]).keys() == {key}


def test_repository_cache_store_paginates_big_batches():
    repo = InMemoryRepository()
    store = RepositoryTravelCacheStore(repo)
    keys = [CacheKey(f"zone_{i}", "zone_dest", "off_peak") for i in range(230)]
    for k in keys:
        store.put(CacheEntry(*k, 10, T0, T0 + timedelta(days=7)))
    assert len(store.get_many(keys)) == 230


def test_repository_usage_store_and_budget_survive_in_the_repository():
    from hs_core.travel.budget import BudgetGuard

    repo = InMemoryRepository()
    e = make_env()
    kw = dict(
        daily_elements=5,
        monthly_elements=50,
        cost_micro_usd_per_element=10_000,
        alerts=e.alerts,
        clock=e.clock,
    )
    g = BudgetGuard(RepositoryUsageStore(repo), **kw)
    assert g.reserve(5) == 5
    g2 = BudgetGuard(RepositoryUsageStore(repo), **kw)
    assert g2.reserve(1) == 0 and g2.usage_today()["elements"] == 5


# --- settings, factory, wiring ----------------------------------------------------------------


def test_new_settings_have_safe_defaults_and_validation():
    s = Settings(_env_file=None)
    assert s.google_maps_server_key.get_secret_value() == ""
    assert s.travel_cache_ttl_days == 7 and s.travel_daily_element_budget > 0
    for bad in (
        {"travel_daily_element_budget": 0},
        {"travel_fallback_speed_kmh": 0},
        {"travel_cache_ttl_days": 0},
        {"travel_warm_bands": "bogus"},
    ):
        assert Settings(_env_file=None, **bad).problems(), bad


def test_factory_without_a_key_uses_straight_line_only_and_never_fails():
    repo = InMemoryRepository()
    svc = build_travel_service(Settings(_env_file=None), repo)
    got = svc.minutes(KOLOLO, ENTEBBE, T0)
    assert got.estimated and got.source == "haversine"


def test_factory_with_a_key_builds_the_google_adapter_without_calling_it():
    s = Settings(_env_file=None, google_maps_server_key="k" * 40)  # secret-scan: allow
    svc = build_travel_service(s, InMemoryRepository())
    assert isinstance(svc._provider, GoogleRoutesProvider)
    assert "k" * 40 not in repr(svc._provider)


def test_app_wiring_configures_the_job_runtime():
    from fastapi.testclient import TestClient

    from hs_core.main import create_app

    app = create_app(settings=Settings(_env_file=None), repository=InMemoryRepository())
    assert app.state.travel is not None
    TestClient(app)  # constructs without error
    assert tjobs.travel_cache_refresh(ctx()).detail != "travel service not configured"


def test_no_travel_module_imports_a_network_client_except_the_adapter():
    root = Path(tjobs.__file__).parent
    for path in root.glob("*.py"):
        if path.name == "provider.py":
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Import | ast.ImportFrom):
                names = [a.name for a in node.names] + [getattr(node, "module", "") or ""]
                assert not any(
                    n.split(".")[0] in {"httpx", "requests", "urllib3"} for n in names
                ), path


def test_warm_bands_default_is_the_after_school_window():
    assert Settings.model_fields["travel_warm_bands"].default == "evening_peak,off_peak"
    assert band_for(T0) == "off_peak" and MUNYONYO and TravelConfig() and InMemoryTravelCacheStore


def test_every_column_the_stores_write_exists_in_the_geo_schema_and_nothing_else():
    root = Path(__file__).resolve().parents[3]
    geo = json.loads((root / "appwrite/schema/geo.json").read_text())
    cols = {t["$id"]: {c["key"] for c in t["columns"]} for t in geo["tables"]}
    repo = InMemoryRepository()
    RepositoryTravelCacheStore(repo).put(
        CacheEntry("zone_a", "zone_b", "off_peak", 5, T0, T0, T0, "x", 1)
    )
    row = repo.list("travel_time_cache")[0]
    assert {k for k in row if not k.startswith("$")} == cols["travel_time_cache"]
    from hs_core.travel.budget import BudgetGuard

    e = make_env()
    BudgetGuard(
        RepositoryUsageStore(repo),
        daily_elements=5,
        monthly_elements=9,
        cost_micro_usd_per_element=1,
        alerts=e.alerts,
        clock=e.clock,
    ).reserve(1)
    assert {k for r in repo.list("travel_usage") for k in r if not k.startswith("$")} == cols[
        "travel_usage"
    ]
