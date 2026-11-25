"""Tunable limits. Defaults are recommendations until tuned with real traffic (owner-blocked)."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import timedelta
from typing import Any


@dataclass(frozen=True)
class OtpPolicy:
    ttl_s: int = 300
    max_attempts: int = 5
    resend_cooldown_s: int = 30
    phone_requests_per_hour: int = 5
    phone_requests_per_day: int = 10
    ip_requests_per_hour: int = 20
    ip_requests_per_day: int = 60
    phone_failures_per_hour: int = 10
    ip_verifies_per_hour: int = 60
    dormant_days: int = 180
    app_hash: str = ""
    token_ttl_s: int = 120
    cleanup_grace: timedelta = field(default=timedelta(hours=1))

    @property
    def ttl(self) -> timedelta:
        return timedelta(seconds=self.ttl_s)

    @classmethod
    def from_settings(cls, s: Any) -> OtpPolicy:
        return cls(
            ttl_s=s.otp_ttl_s,
            max_attempts=s.otp_max_attempts,
            resend_cooldown_s=s.otp_resend_cooldown_s,
            phone_requests_per_hour=s.otp_phone_requests_per_hour,
            phone_requests_per_day=s.otp_phone_requests_per_day,
            ip_requests_per_hour=s.otp_ip_requests_per_hour,
            ip_requests_per_day=s.otp_ip_requests_per_day,
            phone_failures_per_hour=s.otp_phone_failures_per_hour,
            ip_verifies_per_hour=s.otp_ip_verifies_per_hour,
            dormant_days=s.otp_dormant_days,
            app_hash=s.otp_app_hash,
        )

    def with_(self, **kw: Any) -> OtpPolicy:
        return replace(self, **kw)
