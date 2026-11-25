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
    "teacher_app_min_weekly_hours": (1, 40),
    "teacher_app_reapply_cooldown_days": (0, 730),
    "teacher_app_draft_saves_per_hour": (10, 2000),
    "teacher_app_submits_per_hour": (1, 60),
}
# M06 audit: allowed (min, max) of the audit-recover tuning settings.
AUDIT_RANGES: dict[str, tuple[int, int]] = {
    "audit_recover_lookback_hours": (1, 720),
    "audit_recover_grace_s": (30, 3600),
    "admin_audit_rate_per_min": (1, 6000),
}
# N01: allowed (min, max) of each notification tuning setting.
NOTIFY_RANGES: dict[str, tuple[int, int]] = {
    "notify_quiet_start_hour": (0, 23),
    "notify_quiet_end_hour": (0, 23),
    "notify_rate_limit": (1, 100),
    "notify_rate_window_s": (10, 86400),
    "notify_max_attempts": (1, 10),
    "notify_retry_base_s": (1, 3600),
    "notify_lease_s": (30, 900),
    "notify_escalate_after_min": (1, 240),
}


_PLACEHOLDERS = {"changeme", "change-me", "secret", "password", "example", "test", "xxx"}


def _is_local_host(host: str | None) -> bool:
    """localhost, *.localhost, and loopback or unspecified addresses (127/8, ::1, 0.0.0.0, ::)."""
    if not host:
        return False
    h = host.strip("[]").rstrip(".").lower()
    if h == "localhost" or h.endswith(".localhost"):
        return True
    try:
        ip = ipaddress.ip_address(h)
    except ValueError:
        return False
    return ip.is_loopback or ip.is_unspecified


class ConfigError(RuntimeError):
    """Raised at startup when configuration is missing or insecure. Never contains values."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    hs_env: Env = "dev"
    hs_debug: bool = False
    log_level: str = "INFO"
    app_version: str = "0.1.0"
    git_sha: str = "unknown"
    appwrite_endpoint: str = ""
    appwrite_project_id: str = ""
    appwrite_api_key: SecretStr = SecretStr("")
    appwrite_database_id: str = ""
    appwrite_connect_timeout_s: float = 3.05
    appwrite_read_timeout_s: float = 10.0
    # Shared with the Appwrite Function job-tick (ADR 0002 rule 8); never logged.
    job_tick_secret: SecretStr = SecretStr("")
    # Alert after this many consecutive failed job attempts (NFR-17).
    job_alert_after_failures: int = 3
    sentry_dsn: SecretStr = SecretStr("")  # empty = error reporting disabled
    # A01 phone OTP. Africa's Talking: owner-blocked (no account yet); empty = SMS unavailable.
    africastalking_api_key: SecretStr = SecretStr("")
    africastalking_username: str = ""  # "sandbox" selects the sandbox endpoint
    africastalking_sender_id: str = ""  # empty = the provider's default sender
    otp_pepper: SecretStr = SecretStr("")  # HMAC key for code and identifier hashes
    otp_app_hash: str = ""  # Android SMS Retriever hash (placeholder until the signing key exists)
    otp_ttl_s: int = 300
    otp_max_attempts: int = 5
    otp_resend_cooldown_s: int = 30
    otp_phone_requests_per_hour: int = 5
    otp_phone_requests_per_day: int = 10
    otp_ip_requests_per_hour: int = 20
    otp_ip_requests_per_day: int = 60
    otp_phone_failures_per_hour: int = 10
    otp_ip_verifies_per_hour: int = 60
    otp_dormant_days: int = 180

    # Teacher application (V01). Defaults are recommendations until the owner confirms.
    teacher_app_min_weekly_hours: int = 6
    teacher_app_reapply_cooldown_days: int = 90
    teacher_app_draft_saves_per_hour: int = 240
    teacher_app_submits_per_hour: int = 10
    # Travel time (X01). No key = straight-line estimates only (flagged estimated).
    google_maps_server_key: SecretStr = SecretStr("")
    travel_daily_element_budget: int = 1000  # provider elements (origin x destination) per day
    travel_monthly_element_budget: int = 15000  # alert at 80% and 100%, hard stop at 100%
    travel_cache_ttl_days: int = 7  # weekly refresh (SR 7.2)
    travel_negative_ttl_s: int = 300  # how long a failed lookup is not retried
    travel_fallback_speed_kmh: float = 20.0  # straight-line fallback; see travel/geo.py
    travel_fallback_circuity: float = 1.4
    travel_within_zone_minutes: int = 10  # two homes in the same zone
    travel_element_cost_micro_usd: int = 10000  # USD 0.01 per element (estimate; owner to check)
    travel_warm_bands: str = "evening_peak,off_peak"  # bands the daily warming job refreshes

    def _travel_problems(self) -> list[str]:
        from .travel.bands import BANDS

        out: list[str] = []
        for name in (
            "travel_daily_element_budget",
            "travel_monthly_element_budget",
            "travel_cache_ttl_days",
            "travel_negative_ttl_s",
            "travel_element_cost_micro_usd",
            "travel_within_zone_minutes",
        ):
            if getattr(self, name) <= 0:
                out.append(f"{name.upper()} must be a positive number")
        for name in ("travel_fallback_speed_kmh", "travel_fallback_circuity"):
            if getattr(self, name) <= 0:
                out.append(f"{name.upper()} must be a positive number")
        bands = [b.strip() for b in self.travel_warm_bands.split(",") if b.strip()]
        if not bands or any(b not in BANDS for b in bands):
            out.append("TRAVEL_WARM_BANDS must list time bands: " + ", ".join(BANDS))
        return out

    # N01 notifications. Quiet hours are GLOBAL (SR 8: 21:00-07:00 Africa/Kampala).
    notify_quiet_start_hour: int = 21
    notify_quiet_end_hour: int = 7
    notify_rate_limit: int = 5
    notify_rate_window_s: int = 600
    notify_max_attempts: int = 4
    notify_retry_base_s: int = 60
    notify_lease_s: int = 120
    notify_escalate_after_min: int = 15
    # K01 catalogue read API. Published versions never change: a year, immutable.
    catalogue_immutable_max_age_s: int = 31536000
    catalogue_current_max_age_s: int = 60  # pointers: current version, curricula, version lists
    catalogue_bundle_max_bytes: int = 1048576  # hard cap of one tree/bundle response (uncompressed)
    catalogue_cache_entries: int = 32  # in-process LRU of rendered published content

    def _catalogue_problems(self) -> list[str]:
        return [
            f"{name.upper()} must be a positive number"
            for name in (
                "catalogue_immutable_max_age_s",
                "catalogue_current_max_age_s",
                "catalogue_bundle_max_bytes",
                "catalogue_cache_entries",
            )
            if getattr(self, name) <= 0
        ]

    # M06 audit log. Owner question 12: the safeguarding lead reads the whole audit log (default
    # on, as the ADR 0012 deviation); off = opsadmin and superadmin only.
    audit_lead_blanket_read: bool = True
    audit_recover_lookback_hours: int = 6  # how far back audit-recover looks for crashed changes
    audit_recover_grace_s: int = 300  # in-flight changes younger than this are left alone
    admin_audit_rate_per_min: int = 60  # admin-UI audit events per admin per minute (429 above)

    def problems(self) -> list[str]:
        """Variable-name-only list of what is wrong for this environment."""
        out: list[str] = []
        dsn = self.sentry_dsn.get_secret_value().strip()
        if dsn and urlparse(dsn).scheme != "https":
            out.append("SENTRY_DSN must be an https DSN")
        for name, (lo, hi) in OTP_RANGES.items():
            if not lo <= getattr(self, name) <= hi:
                out.append(f"{name.upper()} must be between {lo} and {hi}")
        out += self._travel_problems()
        out += self._catalogue_problems()
        for name, (lo, hi) in NOTIFY_RANGES.items():
            if not lo <= getattr(self, name) <= hi:
                out.append(f"{name.upper()} must be between {lo} and {hi}")
        if self.notify_quiet_start_hour == self.notify_quiet_end_hour:
            out.append("NOTIFY_QUIET_START_HOUR and NOTIFY_QUIET_END_HOUR must differ")
        for name, (lo, hi) in AUDIT_RANGES.items():
            if not lo <= getattr(self, name) <= hi:
                out.append(f"{name.upper()} must be between {lo} and {hi}")
        if self.hs_env == "dev":
            return out
        for name in REQUIRED_DEPLOYED:
            v = getattr(self, name)
            v = v.get_secret_value() if isinstance(v, SecretStr) else v
            if not v.strip():
                out.append(f"{name.upper()} is required in {self.hs_env}")
        key = self.appwrite_api_key.get_secret_value().strip()
        if key and (key.lower() in _PLACEHOLDERS or len(key) < 32):
            out.append("APPWRITE_API_KEY looks like a placeholder or is too short")
        tick_key = self.job_tick_secret.get_secret_value().strip()
        if not tick_key:
            out.append(f"JOB_TICK_SECRET is required in {self.hs_env}")
        elif tick_key.lower() in _PLACEHOLDERS or len(tick_key) < 32:
            out.append("JOB_TICK_SECRET looks like a placeholder or is too short")
        pepper = self.otp_pepper.get_secret_value().strip()
        if not pepper or pepper.lower() in _PLACEHOLDERS or len(pepper) < 32:
            out.append(f"OTP_PEPPER is required in {self.hs_env} (at least 32 characters)")
        if self.appwrite_endpoint.strip():
            u = urlparse(self.appwrite_endpoint)
            if u.scheme != "https":
                out.append("APPWRITE_ENDPOINT must use https")
            if _is_local_host(u.hostname):
                out.append("APPWRITE_ENDPOINT must not point at localhost")
        if self.hs_env == "production" and self.hs_debug:
            out.append("HS_DEBUG must be false in production")
        return out


def load_settings(env_file: str | None = ".env") -> Settings:
    try:
        s = Settings(_env_file=env_file)
    except ValidationError as e:
        names = ", ".join(sorted({str(err["loc"][0]).upper() for err in e.errors() if err["loc"]}))
        raise ConfigError(f"Invalid configuration for: {names}") from None
    probs = s.problems()
    if probs:
        raise ConfigError("Invalid configuration: " + "; ".join(probs))
    return s
