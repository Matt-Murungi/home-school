"""Counters for the travel service, emitted as one structured log line (no PII, ids only)."""

from __future__ import annotations

import logging
import threading

log = logging.getLogger("hs_core.travel")

COUNTERS = (
    "lookups",
    "same_zone",
    "default_estimates",
    "cache_hits",
    "cache_misses",
    "negative_hits",
    "stale_served",
    "fallbacks",
    "provider_calls",
    "provider_elements",
    "provider_failures",
    "rate_limited",
    "rejected_responses",
    "budget_denied",
    "close_call_lookups",
    "cost_micro_usd",
)


class TravelMetrics:
    def __init__(self) -> None:
        self._c = dict.fromkeys(COUNTERS, 0)
        self._lock = threading.Lock()

    def inc(self, name: str, n: int = 1) -> None:
        with self._lock:
            self._c[name] += n

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            return dict(self._c)

    def log_snapshot(self) -> None:
        log.info("travel metrics", extra={"metrics": self.snapshot()})
