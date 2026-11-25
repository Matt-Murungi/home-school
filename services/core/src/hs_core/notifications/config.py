"""Notification tuning, built from Settings (all have safe defaults; see .env.example)."""

from __future__ import annotations

from dataclasses import dataclass

from ..settings import Settings


@dataclass(frozen=True)
class NotificationConfig:
    quiet_start_hour: int = 21
    quiet_end_hour: int = 7
    rate_limit: int = 5  # non-critical sends per recipient and template ...
    rate_window_s: int = 600  # ... per window
    max_attempts: int = 4  # provider attempts before a row fails for good
    retry_base_s: int = 60  # backoff: base * 2^(attempt-1)
    lease_s: int = 120  # a dispatching worker owns a row this long (crash recovery)
    escalate_after_min: int = 15  # safeguarding: backup is escalated to if unacknowledged

    @classmethod
    def from_settings(cls, s: Settings) -> NotificationConfig:
        return cls(
            quiet_start_hour=s.notify_quiet_start_hour,
            quiet_end_hour=s.notify_quiet_end_hour,
            rate_limit=s.notify_rate_limit,
            rate_window_s=s.notify_rate_window_s,
            max_attempts=s.notify_max_attempts,
            retry_base_s=s.notify_retry_base_s,
            lease_s=s.notify_lease_s,
            escalate_after_min=s.notify_escalate_after_min,
        )


class LiveNotificationConfig:
    """NotificationConfig whose business-rule fields are read from the settings service at every
    use (H05): a change by the super admin applies without a restart. `retry_base_s` and
    `lease_s` are infrastructure tuning and stay in the environment."""

    def __init__(self, base: NotificationConfig, app_settings) -> None:
        self._base = base
        self._s = app_settings

    @property
    def quiet_start_hour(self) -> int:
        return int(self._s.get("notify.quiet_hours")["start_hour"])

    @property
    def quiet_end_hour(self) -> int:
        return int(self._s.get("notify.quiet_hours")["end_hour"])

    @property
    def rate_limit(self) -> int:
        return int(self._s.get("notify.rate_limit"))

    @property
    def rate_window_s(self) -> int:
        return int(self._s.get("notify.rate_window_s"))

    @property
    def max_attempts(self) -> int:
        return int(self._s.get("notify.max_attempts"))

    @property
    def escalate_after_min(self) -> int:
        return int(self._s.get("notify.escalate_after_min"))

    def __getattr__(self, name: str):
        return getattr(self._base, name)
