"""Alert sink interface. Real sink (Sentry/email) is wired later; the log sink is the default."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol

log = logging.getLogger("hs_core.jobs.alerts")


@dataclass(frozen=True)
class Alert:
    kind: str  # "final_failure" | "consecutive_failures"
    job: str
    dedupe_key: str
    error: str
    count: int = 0  # consecutive failures when kind == "consecutive_failures"


class AlertSink(Protocol):
    def send(self, alert: Alert) -> None: ...


class FakeAlertSink:
    def __init__(self) -> None:
        self.sent: list[Alert] = []

    def send(self, alert: Alert) -> None:
        self.sent.append(alert)


class LogAlertSink:
    """Logs at ERROR with structured fields (picked up by Sentry's logging integration later)."""

    def send(self, alert: Alert) -> None:
        log.error(
            "job alert",
            extra={
                "alert_kind": alert.kind,
                "job": alert.job,
                "dedupe_key": alert.dedupe_key,
                "error": alert.error[:300],
                "count": alert.count,
            },
        )
