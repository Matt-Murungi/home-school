"""Zone-pair cache. One row per (origin zone, destination zone, band).

Row design: `minutes`/`fetched_at`/`expires_at` are the LAST GOOD value and when to refresh it.
A failure never overwrites them: it only sets `retry_at` (negative cache), `failure_reason`
and `fail_count`, so a bad or failed refresh keeps the previous value (cache-poisoning rule).
Rows hold zone ids and minutes only: no coordinates, no personal data.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import NamedTuple, Protocol

from appwrite.query import Query

from ..jobs.idempotency import row_id
from ..repository import Conflict, Repository

TABLE = "travel_time_cache"


class CacheKey(NamedTuple):
    origin_zone_id: str
    dest_zone_id: str
    band: str


@dataclass(frozen=True)
class CacheEntry:
    origin_zone_id: str
    dest_zone_id: str
    band: str
    minutes: int | None = None
    fetched_at: datetime | None = None
    expires_at: datetime | None = None
    retry_at: datetime | None = None
    failure_reason: str | None = None
    fail_count: int = 0

    @property
    def key(self) -> CacheKey:
        return CacheKey(self.origin_zone_id, self.dest_zone_id, self.band)

    def with_failure(self, until: datetime, reason: str) -> CacheEntry:
        return replace(self, retry_at=until, failure_reason=reason, fail_count=self.fail_count + 1)


class TravelCacheStore(Protocol):
    def get(self, key: CacheKey) -> CacheEntry | None: ...
    def get_many(self, keys: list[CacheKey]) -> dict[CacheKey, CacheEntry]: ...
    def put(self, entry: CacheEntry) -> None: ...


class InMemoryTravelCacheStore:
    def __init__(self) -> None:
        self._d: dict[CacheKey, CacheEntry] = {}
        self._lock = threading.Lock()

    def get(self, key):
        with self._lock:
            return self._d.get(CacheKey(*key))

    def get_many(self, keys):
        with self._lock:
            return {k: self._d[k] for k in map(lambda k: CacheKey(*k), keys) if k in self._d}

    def put(self, entry):
        with self._lock:
            self._d[entry.key] = entry

    def all(self) -> list[CacheEntry]:
        with self._lock:
            return list(self._d.values())


def _iso(dt: datetime | None) -> str | None:
    return dt.astimezone(UTC).isoformat() if dt else None


def _dt(v: str | None) -> datetime | None:
    return datetime.fromisoformat(v.replace("Z", "+00:00")) if v else None


def cache_row_id(key: CacheKey) -> str:
    return row_id("travel_time_cache", *key)


class RepositoryTravelCacheStore:
    """Appwrite-backed (through the F03 Repository). Deterministic row ids make create atomic;
    a lost race falls back to update (last write wins, both writes are valid values)."""

    PAGE = 100

    def __init__(self, repo: Repository) -> None:
        self._repo = repo

    def _entry(self, row: dict) -> CacheEntry:
        return CacheEntry(
            row["origin_zone_id"],
            row["dest_zone_id"],
            row["band"],
            row.get("minutes"),
            _dt(row.get("fetched_at")),
            _dt(row.get("expires_at")),
            _dt(row.get("retry_at")),
            row.get("failure_reason"),
            row.get("fail_count") or 0,
        )

    def get(self, key):
        from ..repository import NotFound

        try:
            return self._entry(self._repo.get(TABLE, cache_row_id(CacheKey(*key))))
        except NotFound:
            return None

    def get_many(self, keys):
        out: dict[CacheKey, CacheEntry] = {}
        groups: dict[tuple[str, str], list[str]] = {}
        for k in map(lambda k: CacheKey(*k), keys):
            groups.setdefault((k.dest_zone_id, k.band), []).append(k.origin_zone_id)
        for (dest, band), origins in groups.items():
            for i in range(0, len(origins), self.PAGE):
                part = origins[i : i + self.PAGE]
                rows = self._repo.list(
                    TABLE,
                    [
                        Query.equal("dest_zone_id", [dest]),
                        Query.equal("band", [band]),
                        Query.equal("origin_zone_id", part),
                        Query.limit(self.PAGE),
                    ],
                )
                for r in rows:
                    e = self._entry(r)
                    out[e.key] = e
        return out

    def put(self, entry):
        data = {
            "origin_zone_id": entry.origin_zone_id,
            "dest_zone_id": entry.dest_zone_id,
            "band": entry.band,
            "pair_key": ":".join(entry.key),
            "minutes": entry.minutes,
            "fetched_at": _iso(entry.fetched_at),
            "expires_at": _iso(entry.expires_at),
            "retry_at": _iso(entry.retry_at),
            "failure_reason": entry.failure_reason,
            "fail_count": entry.fail_count,
        }
        rid = cache_row_id(entry.key)
        try:
            self._repo.create(TABLE, rid, data)
        except Conflict:
            self._repo.update(TABLE, rid, data)
