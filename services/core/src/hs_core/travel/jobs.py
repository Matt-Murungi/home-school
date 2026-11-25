"""`travel-cache-refresh`: the cache warming job (F04 job runner; listed in jobs/schedule.py).

Daily at 02:00 Kampala (quiet hour, after the budget day rolled over). Walks the zones in id
order, one origin zone per chunk (`Continue(cursor)`): for each warm band it refreshes the pairs
that are missing or within a day of expiry, so a weekly cycle emerges from the 7-day TTL. Fresh
pairs are skipped, so a re-run (or a replayed chunk) makes no provider calls: idempotent. Warming
may use at most `warm_fraction` (default half) of the daily element budget and stops cleanly
when that is reached; matching keeps the rest. A new zone gets its row on the next run (or on
demand, whichever comes first).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from appwrite.query import Query

from ..jobs.model import Continue, Done, JobContext
from ..repository import Repository
from .service import TravelTimeService, ZoneRef

log = logging.getLogger("hs_core.travel.job")
PAGE = 100
MAX_ZONES = 2_000  # far above Kampala's few dozen; bounds a runaway table


@dataclass
class _Runtime:
    service: TravelTimeService | None = None
    repo: Repository | None = None
    bands: tuple[str, ...] | None = None


_RT = _Runtime()


def configure(
    service: TravelTimeService | None,
    repo: Repository | None,
    *,
    bands: tuple[str, ...] | None = None,
) -> None:
    _RT.service, _RT.repo, _RT.bands = service, repo, bands


def load_zones(repo: Repository) -> list[ZoneRef]:
    """Live zones with a centroid, sorted by id (stable cursor)."""
    out: list[ZoneRef] = []
    offset = 0
    while offset < MAX_ZONES:
        rows = repo.list("zone", [Query.order_asc("$id"), Query.limit(PAGE), Query.offset(offset)])
        for r in rows:
            if r.get("deleted_at") is None and r.get("centroid_lat_e6") is not None:
                out.append(ZoneRef.from_row(r))
        if len(rows) < PAGE:
            break
        offset += PAGE
    return sorted(out, key=lambda z: z.id)


def travel_cache_refresh(ctx: JobContext) -> Done | Continue:
    svc, repo = _RT.service, _RT.repo
    if svc is None or repo is None:
        return Done(detail="travel service not configured")
    zones = load_zones(repo)
    cur = ctx.cursor if isinstance(ctx.cursor, dict) else {}
    i, refreshed, failed = (
        int(cur.get("i", 0)),
        int(cur.get("refreshed", 0)),
        int(cur.get("failed", 0)),
    )
    if i >= len(zones):
        return Done(detail=f"no more zones; refreshed {refreshed}, failed {failed}")
    bands = _RT.bands or svc.config.warm_bands
    items = 0
    for band in bands:
        if ctx.cancelled is not None and ctx.cancelled.is_set():
            return Continue({"i": i, "refreshed": refreshed, "failed": failed}, items=items)
        res = svc.warm_origin(zones[i], zones, band)
        items += res.refreshed
        refreshed += res.refreshed
        failed += res.failed
        if res.budget_stopped:
            log.info(
                "travel warming stopped at the budget share",
                extra={"origin_zone": zones[i].id, "band": band},
            )
            return Done(
                detail=f"stopped: warming budget reached; refreshed {refreshed}, failed {failed}",
                items=items,
            )
    if i + 1 < len(zones):
        return Continue({"i": i + 1, "refreshed": refreshed, "failed": failed}, items=items)
    detail = f"refreshed {refreshed}"
    if failed:
        detail += f", failed {failed} (served by fallback)"
    return Done(detail=detail, items=items)
