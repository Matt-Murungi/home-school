"""Cost guard. Counts provider ELEMENTS (origin x destination pairs; Routes bills per element)
per Africa/Kampala day and month, persisted in `travel_usage` so it survives restarts and is
shared by instances. Appwrite has no atomic increment: usage is read-modify-write under an
in-process lock, so concurrent instances can overshoot a cap by about one batch. Documented and
accepted: the Google Cloud budget alert and quota cap (owner setup) are the hard stop.

Cost is integer micro-USD per element (settings); it is an estimate for the alert and the
metrics, not accounting (money in the product is UGX; this is infrastructure spend).
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol

from ..jobs.idempotency import row_id
from ..repository import Conflict, NotFound, Repository
from .alerts import TravelAlert, TravelAlertSink, safe_send
from .bands import KAMPALA

TABLE = "travel_usage"
THRESHOLDS = (80, 100)  # monthly alert percentages


class UsageStore(Protocol):
    def get(self, period_key: str) -> dict | None: ...
    def put(self, period_key: str, row: dict) -> None: ...


class InMemoryUsageStore:
    def __init__(self) -> None:
        self._d: dict[str, dict] = {}

    def get(self, period_key):
        r = self._d.get(period_key)
        return dict(r) if r else None

    def put(self, period_key, row):
        self._d[period_key] = dict(row)


class RepositoryUsageStore:
    def __init__(self, repo: Repository) -> None:
        self._repo = repo

    @staticmethod
    def _id(period_key: str) -> str:
        return row_id("travel_usage", period_key)

    def get(self, period_key):
        try:
            return self._repo.get(TABLE, self._id(period_key))
        except NotFound:
            return None

    def put(self, period_key, row):
        data = {
            "period_key": period_key,
            "elements": int(row.get("elements", 0)),
            "cost_micro_usd": int(row.get("cost_micro_usd", 0)),
            "alerted_pct": int(row.get("alerted_pct", 0)),
            "updated_at": datetime.now(UTC).isoformat(),
        }
        try:
            self._repo.create(TABLE, self._id(period_key), data)
        except Conflict:
            self._repo.update(TABLE, self._id(period_key), data)


class BudgetGuard:
    def __init__(
        self,
        store: UsageStore,
        *,
        daily_elements: int,
        monthly_elements: int,
        cost_micro_usd_per_element: int,
        alerts: TravelAlertSink,
        clock: Callable[[], datetime],
    ) -> None:
        self._store = store
        self.daily_elements = daily_elements
        self.monthly_elements = monthly_elements
        self.cost_per_element = cost_micro_usd_per_element
        self._alerts = alerts
        self._clock = clock
        self._lock = threading.Lock()

    def day_key(self) -> str:
        return "d:" + self._clock().astimezone(KAMPALA).date().isoformat()

    def month_key(self) -> str:
        return "m:" + self._clock().astimezone(KAMPALA).strftime("%Y-%m")

    def _row(self, key: str) -> dict:
        return self._store.get(key) or {"elements": 0, "cost_micro_usd": 0, "alerted_pct": 0}

    def usage_today(self) -> dict:
        return self._row(self.day_key())

    def reserve(self, n: int, *, fraction: float = 1.0) -> int:
        """Reserve up to `n` elements; returns how many were granted (0..n). `fraction` < 1
        limits the DAILY use to that share of the cap (cache warming leaves room for matching)."""
        if n <= 0:
            return 0
        with self._lock:
            dkey, mkey = self.day_key(), self.month_key()
            day, month = self._row(dkey), self._row(mkey)
            day_room = int(self.daily_elements * fraction) - day["elements"]
            month_room = self.monthly_elements - month["elements"]
            granted = max(0, min(n, day_room, month_room))
            if (
                granted < n
                and fraction >= 1.0
                and day_room <= month_room
                and day["alerted_pct"] < 100
            ):
                day["alerted_pct"] = 100
                safe_send(
                    self._alerts,
                    TravelAlert(
                        "daily_budget_exhausted", {"day": dkey[2:], "cap": self.daily_elements}
                    ),
                )
            if granted:
                for row in (day, month):
                    row["elements"] += granted
                    row["cost_micro_usd"] += granted * self.cost_per_element
                self._monthly_alerts(month, mkey)
            self._store.put(dkey, day)
            self._store.put(mkey, month)
            return granted

    def _monthly_alerts(self, month: dict, mkey: str) -> None:
        pct = month["elements"] * 100 // self.monthly_elements if self.monthly_elements else 100
        for t in THRESHOLDS:
            if pct >= t > month["alerted_pct"]:
                month["alerted_pct"] = t
                safe_send(
                    self._alerts,
                    TravelAlert(
                        "monthly_budget_threshold",
                        {"month": mkey[2:], "pct": t, "elements": month["elements"]},
                    ),
                )
