"""TravelTimeService: minutes of travel between zones, answered from a zone-pair cache.

Never fails matching: every error path ends in a flagged estimate (`estimated=True`).

Order of answers for a pair (origin zone -> destination zone, time band):
1. same zone -> configured within-zone default (no provider, no cache);
2. a zone without a usable centroid -> flagged default;
3. fresh cache row -> `cache`;
4. negatively cached failure (short TTL) -> last known value or straight-line fallback;
5. provider call (single-flight per pair, batched per destination, budget-guarded, bounded
   retries) -> `provider`; the answer is sanity-checked before it can enter the cache;
6. otherwise last known (stale) value -> `stale_cache`, else haversine x circuity / speed
   -> `haversine`; both flagged `estimated=True`.
"""

from __future__ import annotations

import hashlib
import logging
import math
import threading
from collections.abc import Callable, Sequence
from concurrent.futures import Future
from dataclasses import dataclass
from datetime import datetime, timedelta

from .alerts import LogTravelAlertSink, TravelAlert, TravelAlertSink, safe_send
from .bands import BANDS, band_for, departure_for
from .budget import BudgetGuard
from .cache import CacheEntry, CacheKey, TravelCacheStore
from .geo import (
    BAND_SPEED_FACTOR,
    InvalidCoordinate,
    fallback_minutes,
    haversine_km,
    outside_uganda,
)
from .metrics import TravelMetrics
from .provider import (
    Element,
    Point,
    ProviderError,
    ProviderQuotaExceeded,
    ProviderRateLimited,
    ProviderTimeout,
    TravelTimeProvider,
)

log = logging.getLogger("hs_core.travel")


@dataclass(frozen=True)
class TravelConfig:
    cache_ttl: timedelta = timedelta(days=7)  # weekly refresh (SR 7.2)
    ttl_jitter: float = 0.1  # +-10% per key so a weekly refresh does not stampede
    refresh_ahead: timedelta = timedelta(days=1)  # warming refreshes entries this close to expiry
    negative_ttl: timedelta = timedelta(minutes=5)  # timeouts, 5xx, rate limits
    quota_negative_ttl: timedelta = timedelta(minutes=30)
    no_route_ttl: timedelta = timedelta(days=1)
    implausible_ttl: timedelta = timedelta(hours=1)
    fallback_speed_kmh: float = 20.0
    fallback_circuity: float = 1.4
    within_zone_minutes: int = 10
    unknown_minutes: int = 30  # zone without a centroid: a flagged, deliberately middling guess
    max_attempts: int = 3
    backoff_base_s: float = 0.5
    backoff_cap_s: float = 4.0
    retry_after_cap_s: float = 5.0  # never wait longer than this for a Retry-After
    flight_wait_s: float = 15.0
    max_plausible_seconds: int = 6 * 3600
    max_plausible_kmh: float = 130.0
    warm_fraction: float = 0.5  # warming may use at most this share of the daily budget
    warm_bands: tuple[str, ...] = ("evening_peak", "off_peak")


@dataclass(frozen=True)
class ZoneRef:
    id: str
    lat_e6: int | None = None
    lng_e6: int | None = None

    @classmethod
    def from_row(cls, row: dict) -> ZoneRef:
        return cls(str(row["$id"]), row.get("centroid_lat_e6"), row.get("centroid_lng_e6"))


@dataclass(frozen=True)
class TravelEstimate:
    minutes: int
    estimated: bool  # True for anything that did not come from the provider or its cache
    source: str  # same_zone | cache | provider | session | stale_cache | haversine | default
    band: str


@dataclass
class WarmResult:
    refreshed: int = 0
    skipped: int = 0
    failed: int = 0
    budget_stopped: bool = False


def needs_session_lookup(*, estimate_minutes: int, limit_minutes: int, margin_minutes: int) -> bool:
    """Per-session (exact point) lookups only for close calls: the zone-pair estimate is within
    `margin_minutes` of the limit being decided (e.g. a teacher's max travel time)."""
    return abs(limit_minutes - estimate_minutes) <= margin_minutes


class TravelTimeService:
    def __init__(
        self,
        provider: TravelTimeProvider,
        cache: TravelCacheStore,
        budget: BudgetGuard,
        config: TravelConfig | None = None,
        *,
        alerts: TravelAlertSink | None = None,
        clock: Callable[[], datetime],
        sleep: Callable[[float], None],
        metrics: TravelMetrics | None = None,
    ) -> None:
        self._provider = provider
        self._cache = cache
        self._budget = budget
        self.config = config or TravelConfig()
        self._alerts = alerts or LogTravelAlertSink()
        self._clock = clock
        self._sleep = sleep
        self.metrics = metrics or TravelMetrics()
        self._flight_lock = threading.Lock()
        self._inflight: dict[CacheKey, Future] = {}
        self._warned_zones: set[str] = set()
        self._quota_alert_day: str | None = None

    # ------------------------------------------------------------------ public API

    def minutes(self, origin: ZoneRef, dest: ZoneRef, when: datetime) -> TravelEstimate:
        return self.minutes_many([origin], dest, when)[origin.id]

    def minutes_many(
        self, origins: Sequence[ZoneRef], dest: ZoneRef, when: datetime
    ) -> dict[str, TravelEstimate]:
        """N teacher zones -> one child zone, one provider call for everything not cached."""
        band = band_for(when)
        now = self._clock()
        out: dict[str, TravelEstimate] = {}
        need: list[ZoneRef] = []
        seen: set[str] = set()
        dest_ok = self._point(dest) is not None
        for o in origins:
            if o.id in seen:
                continue
            seen.add(o.id)
            self.metrics.inc("lookups")
            if o.id == dest.id:
                self.metrics.inc("same_zone")
                out[o.id] = TravelEstimate(
                    self.config.within_zone_minutes, False, "same_zone", band
                )
            elif not dest_ok or self._point(o) is None:
                self.metrics.inc("default_estimates")
                log.info(
                    "travel default (zone without usable centroid)",
                    extra={"origin_zone": o.id, "dest_zone": dest.id, "band": band},
                )
                out[o.id] = TravelEstimate(self.config.unknown_minutes, True, "default", band)
            else:
                need.append(o)
        if need:
            keys = [CacheKey(o.id, dest.id, band) for o in need]
            entries = self._read(keys)
            todo: list[ZoneRef] = []
            for o, k in zip(need, keys, strict=True):
                est = self._from_entry(entries.get(k), o, dest, band, now)
                if est is not None:
                    out[o.id] = est
                else:
                    todo.append(o)
            if todo:
                res, _ = self._refresh_group(todo, [dest], band, entries)
                for o in todo:
                    out[o.id] = res[CacheKey(o.id, dest.id, band)]
        return out

    def session_minutes(
        self,
        origin_zone: ZoneRef,
        dest_zone: ZoneRef,
        origin_point: Point,
        dest_point: Point,
        when: datetime,
        *,
        close_call: bool,
    ) -> TravelEstimate:
        """Exact-point lookup, only when `close_call`; otherwise (or when it cannot be made) the
        zone-pair answer. Home coordinates go to the provider only: never cached, never logged."""
        band = band_for(when)
        if close_call and self._budget.reserve(1) == 1:
            self.metrics.inc("close_call_lookups")
            self._count_elements(1)
            try:
                m = self._call_with_retry(
                    [origin_point], [dest_point], departure_for(band, self._clock())
                )
                el = m[0][0]
                dist = haversine_km(
                    origin_point.lat_e6, origin_point.lng_e6, dest_point.lat_e6, dest_point.lng_e6
                )
                if el.status == "ok" and self._plausible(el.seconds, dist):
                    return TravelEstimate(_minutes(el.seconds), False, "session", band)
                self.metrics.inc("rejected_responses")
            except Exception as exc:  # noqa: BLE001 - never fail matching
                self._note_failure(exc, origin_zone.id, dest_zone.id, band)
        elif close_call:
            self.metrics.inc("budget_denied")
        return self.minutes(origin_zone, dest_zone, when)

    def warm_origin(
        self, origin: ZoneRef, dests: Sequence[ZoneRef], band: str, *, fraction: float | None = None
    ) -> WarmResult:
        """Refresh missing/expiring entries from one origin to many destinations (the job)."""
        if band not in BANDS:
            raise ValueError(f"unknown band {band!r}")
        fraction = self.config.warm_fraction if fraction is None else fraction
        now = self._clock()
        res = WarmResult()
        usable = [d for d in dests if d.id != origin.id and self._point(d) and self._point(origin)]
        entries = self._read([CacheKey(origin.id, d.id, band) for d in usable])
        todo = []
        for d in usable:
            e = entries.get(CacheKey(origin.id, d.id, band))
            fresh = (
                e is not None
                and e.minutes is not None
                and e.expires_at is not None
                and now < e.expires_at - self.config.refresh_ahead
            )
            negative = e is not None and e.retry_at is not None and now < e.retry_at
            if fresh or negative:
                res.skipped += 1
            else:
                todo.append(d)
        if todo:
            est, denied = self._refresh_group([origin], todo, band, entries, fraction=fraction)
            res.budget_stopped = bool(denied)
            for k, v in est.items():
                if k in denied:
                    continue
                if v.source == "provider":
                    res.refreshed += 1
                elif v.source == "cache":
                    res.skipped += 1
                else:
                    res.failed += 1
        return res

    # ------------------------------------------------------------------ cache reads

    def _point(self, z: ZoneRef) -> Point | None:
        if z.lat_e6 is None or z.lng_e6 is None:
            return None
        try:
            p = Point(z.lat_e6, z.lng_e6)
        except InvalidCoordinate:
            self._warn_zone_once(z.id, "zone centroid is not a valid coordinate")
            return None
        if outside_uganda(p.lat_e6, p.lng_e6):
            self._warn_zone_once(z.id, "zone centroid outside_uganda (used anyway)")
        return p

    def _warn_zone_once(self, zone_id: str, msg: str) -> None:
        with self._flight_lock:
            if zone_id in self._warned_zones:
                return
            self._warned_zones.add(zone_id)
        log.warning(msg, extra={"zone_id": zone_id})

    def _read(self, keys: list[CacheKey]) -> dict[CacheKey, CacheEntry]:
        try:
            return self._cache.get_many(keys)
        except Exception as exc:  # noqa: BLE001 - a cache outage is a miss, not an error
            log.warning("travel cache read failed", extra={"error": type(exc).__name__})
            return {}

    def _from_entry(
        self, e: CacheEntry | None, o: ZoneRef, d: ZoneRef, band: str, now: datetime
    ) -> TravelEstimate | None:
        """Answer from a cache row if it settles the question, else None (needs a refresh)."""
        if e and e.minutes is not None and e.expires_at and now < e.expires_at:
            self.metrics.inc("cache_hits")
            return TravelEstimate(e.minutes, False, "cache", band)
        self.metrics.inc("cache_misses")
        if e and e.retry_at and now < e.retry_at:
            self.metrics.inc("negative_hits")
            return self._fallback(o, d, band, e)
        return None

    def _fallback(self, o: ZoneRef, d: ZoneRef, band: str, e: CacheEntry | None) -> TravelEstimate:
        self.metrics.inc("fallbacks")
        if e is not None and e.minutes is not None:
            self.metrics.inc("stale_served")
            log.info(
                "travel fallback: last known value",
                extra={"origin_zone": o.id, "dest_zone": d.id, "band": band},
            )
            return TravelEstimate(e.minutes, True, "stale_cache", band)
        po, pd = self._point(o), self._point(d)
        if po is None or pd is None:
            return TravelEstimate(self.config.unknown_minutes, True, "default", band)
        km = haversine_km(po.lat_e6, po.lng_e6, pd.lat_e6, pd.lng_e6)
        mins = fallback_minutes(
            km,
            speed_kmh=self.config.fallback_speed_kmh * BAND_SPEED_FACTOR[band],
            circuity=self.config.fallback_circuity,
        )
        log.info(
            "travel fallback: straight line",
            extra={"origin_zone": o.id, "dest_zone": d.id, "band": band},
        )
        return TravelEstimate(mins, True, "haversine", band)

    # ------------------------------------------------------------------ refresh (single flight)

    def _refresh_group(
        self,
        origins: list[ZoneRef],
        dests: list[ZoneRef],
        band: str,
        entries: dict[CacheKey, CacheEntry],
        *,
        fraction: float = 1.0,
    ) -> tuple[dict[CacheKey, TravelEstimate], set[CacheKey]]:
        """All pairs origins x dests (one side has a single zone) need a refresh. Pairs already
        being fetched by another thread are awaited; the rest are fetched here."""
        pairs = {CacheKey(o.id, d.id, band): (o, d) for o in origins for d in dests}
        owned: dict[CacheKey, Future] = {}
        waiting: dict[CacheKey, Future] = {}
        with self._flight_lock:
            for k in pairs:
                if k in self._inflight:
                    waiting[k] = self._inflight[k]
                else:
                    owned[k] = self._inflight[k] = Future()
        results: dict[CacheKey, TravelEstimate] = {}
        denied: set[CacheKey] = set()
        try:
            if owned:
                got, denied = self._fetch_owned(
                    {k: pairs[k] for k in owned}, band, entries, fraction
                )
                results.update(got)
        except Exception as exc:  # noqa: BLE001 - last resort: never raise into matching
            log.error("travel refresh crashed", extra={"error": type(exc).__name__})
        finally:
            for k, fut in owned.items():
                o, d = pairs[k]
                if k not in results:
                    results[k] = self._fallback(o, d, band, entries.get(k))
                with self._flight_lock:
                    self._inflight.pop(k, None)
                fut.set_result(results[k])
        for k, fut in waiting.items():
            try:
                results[k] = fut.result(timeout=self.config.flight_wait_s)
            except Exception:  # noqa: BLE001
                o, d = pairs[k]
                results[k] = self._fallback(o, d, band, entries.get(k))
        return results, denied

    def _fetch_owned(
        self,
        pairs: dict[CacheKey, tuple[ZoneRef, ZoneRef]],
        band: str,
        entries: dict[CacheKey, CacheEntry],
        fraction: float,
    ) -> tuple[dict[CacheKey, TravelEstimate], set[CacheKey]]:
        now = self._clock()
        results: dict[CacheKey, TravelEstimate] = {}
        # Double-check: another thread may have filled these between our read and our claim.
        cur = self._read(list(pairs))
        entries.update(cur)
        todo: list[tuple[CacheKey, ZoneRef, ZoneRef]] = []
        for k, (o, d) in pairs.items():
            e = cur.get(k)
            if (
                e
                and e.minutes is not None
                and e.expires_at
                and now
                < e.expires_at - (self.config.refresh_ahead if fraction < 1.0 else timedelta(0))
            ):
                results[k] = TravelEstimate(e.minutes, False, "cache", band)
            elif e and e.retry_at and now < e.retry_at:
                results[k] = self._fallback(o, d, band, e)
            else:
                todo.append((k, o, d))
        if not todo:
            return results, set()
        billable = getattr(self._provider, "billable", True)
        granted = self._budget.reserve(len(todo), fraction=fraction) if billable else len(todo)
        denied = {k for k, _, _ in todo[granted:]}
        for k, o, d in todo[granted:]:
            self.metrics.inc("budget_denied")
            results[k] = self._fallback(o, d, band, entries.get(k))
        todo = todo[:granted]
        if todo:
            if billable:
                self._count_elements(len(todo))
            results.update(self._call_groups(todo, band, entries))
        return results, denied

    def _call_groups(
        self,
        todo: list[tuple[CacheKey, ZoneRef, ZoneRef]],
        band: str,
        entries: dict[CacheKey, CacheEntry],
    ) -> dict[CacheKey, TravelEstimate]:
        out: dict[CacheKey, TravelEstimate] = {}
        size = max(1, getattr(self._provider, "max_elements", 100))
        one_dest = len({d.id for _, _, d in todo}) == 1
        departure = departure_for(band, self._clock())
        for i in range(0, len(todo), size):
            chunk = todo[i : i + size]
            if one_dest:
                o_pts = [self._point(o) for _, o, _ in chunk]
                d_pts = [self._point(chunk[0][2])]
            else:
                o_pts = [self._point(chunk[0][1])]
                d_pts = [self._point(d) for _, _, d in chunk]
            try:
                m = self._call_with_retry(o_pts, d_pts, departure)
            except Exception as exc:  # noqa: BLE001 - whole chunk failed
                reason, ttl = self._note_failure(exc, chunk[0][1].id, chunk[0][2].id, band)
                for k, o, d in chunk:
                    out[k] = self._negative(k, o, d, band, entries, reason, ttl)
                continue
            log.info(
                "travel provider call",
                extra={
                    "origin_zones": [o.id for _, o, _ in chunk],
                    "dest_zones": [d.id for _, _, d in chunk] if not one_dest else [chunk[0][2].id],
                    "band": band,
                    "elements": len(chunk),
                },
            )
            for j, (k, o, d) in enumerate(chunk):
                el = m[j][0] if one_dest else m[0][j]
                out[k] = self._accept(k, o, d, band, entries, el)
        return out

    def _accept(self, k, o, d, band, entries, el: Element) -> TravelEstimate:
        cfg = self.config
        if el.status == "no_route":
            return self._negative(k, o, d, band, entries, "no_route", cfg.no_route_ttl)
        if el.status != "ok":
            return self._negative(k, o, d, band, entries, "error", cfg.negative_ttl)
        po, pd = self._point(o), self._point(d)
        dist = haversine_km(po.lat_e6, po.lng_e6, pd.lat_e6, pd.lng_e6)
        if not self._plausible(el.seconds, dist):
            self.metrics.inc("rejected_responses")
            log.warning(
                "travel provider answer rejected (implausible)",
                extra={"origin_zone": o.id, "dest_zone": d.id, "band": band},
            )
            return self._negative(k, o, d, band, entries, "implausible", cfg.implausible_ttl)
        now = self._clock()
        minutes = _minutes(el.seconds)
        entry = CacheEntry(k.origin_zone_id, k.dest_zone_id, band, minutes, now, now + self._ttl(k))
        self._put(entry)
        entries[k] = entry
        return TravelEstimate(minutes, False, "provider", band)

    def _negative(self, k, o, d, band, entries, reason: str, ttl: timedelta) -> TravelEstimate:
        prev = entries.get(k) or CacheEntry(k.origin_zone_id, k.dest_zone_id, band)
        entry = prev.with_failure(self._clock() + ttl, reason)
        self._put(entry)
        entries[k] = entry
        return self._fallback(o, d, band, prev)

    def _put(self, entry: CacheEntry) -> None:
        try:
            self._cache.put(entry)
        except Exception as exc:  # noqa: BLE001 - the answer is still valid without the write
            log.warning("travel cache write failed", extra={"error": type(exc).__name__})

    def _ttl(self, k: CacheKey) -> timedelta:
        h = int(hashlib.sha256("|".join(k).encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
        return self.config.cache_ttl * (1 - self.config.ttl_jitter + 2 * self.config.ttl_jitter * h)

    def _plausible(self, seconds: int | None, dist_km: float) -> bool:
        if seconds is None or seconds <= 0 or seconds > self.config.max_plausible_seconds:
            return False
        return not (dist_km >= 0.5 and dist_km / (seconds / 3600) > self.config.max_plausible_kmh)

    # ------------------------------------------------------------------ provider calls

    def _count_elements(self, n: int) -> None:
        self.metrics.inc("provider_elements", n)
        self.metrics.inc("cost_micro_usd", n * self._budget.cost_per_element)

    def _call_with_retry(self, origins, dests, departure):
        cfg = self.config
        for attempt in range(cfg.max_attempts):
            self.metrics.inc("provider_calls")
            try:
                m = self._provider.matrix(origins, dests, departure)
                if len(m) != len(origins) or any(len(r) != len(dests) for r in m):
                    raise ProviderError("provider returned a ragged matrix", retryable=False)
                return m
            except ProviderQuotaExceeded:
                raise
            except ProviderRateLimited as exc:
                self.metrics.inc("rate_limited")
                if exc.retry_after is not None and exc.retry_after > cfg.retry_after_cap_s:
                    raise  # never block on a long Retry-After
                delay = exc.retry_after if exc.retry_after is not None else self._backoff(attempt)
                last: Exception = exc
            except ProviderError as exc:
                if not exc.retryable:
                    raise
                delay = self._backoff(attempt)
                last = exc
            except Exception as exc:  # noqa: BLE001 - adapter bug: no retry
                raise ProviderError("unexpected provider failure", retryable=False) from exc
            if attempt == cfg.max_attempts - 1:
                raise last
            self._sleep(delay)
        raise AssertionError("unreachable")

    def _backoff(self, attempt: int) -> float:
        return min(self.config.backoff_cap_s, self.config.backoff_base_s * 2**attempt)

    def _note_failure(
        self, exc: Exception, o_id: str, d_id: str, band: str
    ) -> tuple[str, timedelta]:
        cause = (
            exc.__cause__
            if exc.__cause__ is not None and not isinstance(exc, ProviderQuotaExceeded)
            else exc
        )
        self.metrics.inc("provider_failures")
        if isinstance(exc, ProviderQuotaExceeded):
            reason, ttl = "quota", self.config.quota_negative_ttl
            day = self._budget.day_key()
            if self._quota_alert_day != day:
                self._quota_alert_day = day
                safe_send(self._alerts, TravelAlert("provider_quota_exceeded", {"day": day[2:]}))
        elif isinstance(exc, ProviderTimeout):
            reason, ttl = "timeout", self.config.negative_ttl
        elif isinstance(exc, ProviderRateLimited):
            reason, ttl = "rate_limited", self.config.negative_ttl
        else:
            reason, ttl = "error", self.config.negative_ttl
        # Class names and ids only: provider messages are never logged.
        log.warning(
            "travel provider call failed",
            extra={
                "origin_zone": o_id,
                "dest_zone": d_id,
                "band": band,
                "reason": reason,
                "error": type(cause).__name__,
            },
        )
        return reason, ttl


def _minutes(seconds: int) -> int:
    return max(1, math.ceil(seconds / 60))
