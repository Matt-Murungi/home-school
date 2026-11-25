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

    def problems(self) -> list[str]:
        """Variable-name-only list of what is wrong for this environment."""
        if self.hs_env == "dev":
            return []
        out: list[str] = []
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
