"""Settings from environment variables only (no secrets in code)."""

from __future__ import annotations

import ipaddress
from typing import Literal
from urllib.parse import urlparse

from pydantic import SecretStr, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

Env = Literal["dev", "staging", "production"]
REQUIRED_DEPLOYED = (
    "appwrite_endpoint",
    "appwrite_project_id",
    "appwrite_api_key",
    "appwrite_database_id",
)
# A01: allowed (min, max) of each OTP tuning setting; out-of-range values fail startup.
OTP_RANGES: dict[str, tuple[int, int]] = {
    "otp_ttl_s": (60, 900),
    "otp_max_attempts": (1, 10),
    "otp_resend_cooldown_s": (5, 300),
    "otp_phone_requests_per_hour": (1, 20),
    "otp_phone_requests_per_day": (1, 50),
    "otp_ip_requests_per_hour": (1, 500),
    "otp_ip_requests_per_day": (1, 2000),
    "otp_phone_failures_per_hour": (1, 50),
    "otp_ip_verifies_per_hour": (1, 1000),
    "otp_dormant_days": (30, 3650),
}
        for name, (lo, hi) in AUDIT_RANGES.items():
            if not lo <= getattr(self, name) <= hi:
                out.append(f"{name.upper()} must be between {lo} and {hi}")
