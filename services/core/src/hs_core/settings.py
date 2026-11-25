"""Settings from environment variables only (no secrets in code)."""

from __future__ import annotations

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
        if self.appwrite_endpoint.strip():
            u = urlparse(self.appwrite_endpoint)
            if u.scheme != "https":
                out.append("APPWRITE_ENDPOINT must use https")
            if u.hostname in {"localhost", "127.0.0.1", "0.0.0.0"}:  # noqa: S104
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
