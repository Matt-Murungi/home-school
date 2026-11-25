"""Configuration. Secrets are never shown in repr and never read from a file by this package."""

from __future__ import annotations

import enum
from dataclasses import dataclass, field

from .errors import MomoConfigError

SANDBOX_URL = "https://sandbox.momodeveloper.mtn.com"
PRODUCTION_URL = "https://proxy.momoapi.mtn.com"


class Environment(enum.StrEnum):
    SANDBOX = "sandbox"
    PRODUCTION = "production"


@dataclass(frozen=True)
class MomoConfig:
    """One per product (Collections and Disbursements have separate keys and users).

    Sandbox restriction (public docs, unverified): the sandbox only accepts currency EUR, so
    amounts there are test numbers and not UGX. Production uses UGX.
    `target_environment` is the X-Target-Environment header: "sandbox" in the sandbox, the
    country environment (Uganda: "mtnuganda", unverified) in production.
    min/max_amount_ugx are OUR guard rails (assumptions, configurable): confirm real limits
    from the MTN agreement.
    """

    environment: Environment
    subscription_key: str = field(repr=False)
    api_user: str = field(repr=False)
    api_key: str = field(repr=False)
    callback_url: str | None = None
    base_url: str | None = None
    target_environment: str | None = None
    currency: str | None = None
    min_amount_ugx: int | None = 500
    max_amount_ugx: int | None = 5_000_000
    timeout_seconds: float = 15.0
    max_attempts: int = 4
    backoff_base_seconds: float = 0.5
    backoff_max_seconds: float = 30.0
    token_expiry_margin_seconds: float = 60.0

    def __post_init__(self):
        env = Environment(self.environment)
        object.__setattr__(self, "environment", env)
        for name in ("subscription_key", "api_user", "api_key"):
            if not getattr(self, name):
                raise MomoConfigError(f"{name} is required")
        sandbox = env is Environment.SANDBOX
        defaults = {
            "base_url": SANDBOX_URL if sandbox else PRODUCTION_URL,
            "target_environment": "sandbox" if sandbox else "mtnuganda",
            "currency": "EUR" if sandbox else "UGX",
        }
        for k, v in defaults.items():
            if getattr(self, k) is None:
                object.__setattr__(self, k, v)
        if not sandbox and self.currency != "UGX":
            raise MomoConfigError("production currency must be UGX")
        if sandbox and self.currency not in ("EUR", "UGX"):
            raise MomoConfigError("sandbox currency must be EUR (sandbox restriction)")
        if not sandbox and not self.base_url.startswith("https://"):
            raise MomoConfigError("production base_url must be https")
        if not sandbox and self.target_environment == "sandbox":
            raise MomoConfigError("production cannot target the sandbox environment")
        if self.callback_url and not self.callback_url.startswith("https://") and not sandbox:
            raise MomoConfigError("production callback_url must be https")
        if self.max_attempts < 1 or self.timeout_seconds <= 0:
            raise MomoConfigError("max_attempts >= 1 and timeout_seconds > 0 required")

    @property
    def msisdn_strict(self) -> bool:
        return self.environment is Environment.PRODUCTION
