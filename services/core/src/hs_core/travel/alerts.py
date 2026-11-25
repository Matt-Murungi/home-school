"""Alert hook for the travel service (budget thresholds, provider quota). The default sink logs
at ERROR with structured fields (Sentry's logging integration picks it up, F08); a real sink
(email/Slack) can be wired later. Alert details carry counts and ids, never coordinates."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Protocol

log = logging.getLogger("hs_core.travel.alerts")


@dataclass(frozen=True)
class TravelAlert:
    kind: str  # daily_budget_exhausted | monthly_budget_threshold | provider_quota_exceeded
    detail: dict = field(default_factory=dict)


class TravelAlertSink(Protocol):
    def send(self, alert: TravelAlert) -> None: ...


class FakeTravelAlertSink:
    def __init__(self) -> None:
        self.sent: list[TravelAlert] = []

    def send(self, alert: TravelAlert) -> None:
        self.sent.append(alert)

    def kinds(self) -> list[str]:
        return [a.kind for a in self.sent]


class LogTravelAlertSink:
    def send(self, alert: TravelAlert) -> None:
        log.error("travel alert", extra={"alert": "travel-" + alert.kind, **alert.detail})


def safe_send(sink: TravelAlertSink, alert: TravelAlert) -> None:
    """A broken sink must never break matching."""
    try:
        sink.send(alert)
    except Exception:  # noqa: BLE001
        log.warning("travel alert sink failed", extra={"alert_kind": alert.kind})
