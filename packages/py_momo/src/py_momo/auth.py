"""OAuth token management: create, cache with expiry margin, refresh on demand. Thread-safe."""

from __future__ import annotations

import base64
import json
import threading
from collections.abc import Callable

from .config import MomoConfig
from .errors import AuthError, RateLimited, ServerError
from .transport import HttpRequest, Transport


class TokenManager:
    def __init__(
        self,
        config: MomoConfig,
        transport: Transport,
        product: str,
        clock: Callable[[], float],
    ):
        self._config = config
        self._transport = transport
        self._product = product  # "collection" | "disbursement"
        self._clock = clock
        self._lock = threading.Lock()
        self._token: str | None = None
        self._expires_at = 0.0

    def get(self) -> str:
        with self._lock:  # single flight: concurrent callers share one token request
            if self._token and self._clock() < self._expires_at:
                return self._token
            return self._fetch()

    def invalidate(self, bad_token: str | None = None) -> None:
        with self._lock:
            # Only drop the token that was actually rejected, so a concurrent refresh survives.
            if bad_token is None or self._token == bad_token:
                self._token = None
                self._expires_at = 0.0

    def _fetch(self) -> str:
        c = self._config
        basic = base64.b64encode(f"{c.api_user}:{c.api_key}".encode()).decode()
        req = HttpRequest(
            "POST",
            f"{c.base_url}/{self._product}/token/",
            {
                "Authorization": f"Basic {basic}",
                "Ocp-Apim-Subscription-Key": c.subscription_key,
                "Content-Length": "0",
            },
            b"",
            c.timeout_seconds,
        )
        resp = self._transport.send(req)
        if resp.status in (401, 403):
            raise AuthError("token request rejected: check API user/key and subscription key")
        if resp.status == 429:
            raise RateLimited()
        if resp.status >= 500:
            raise ServerError(resp.status)
        if resp.status != 200:
            raise AuthError(f"token request failed with HTTP {resp.status}")
        try:
            data = json.loads(resp.body)
            token = data["access_token"]
            ttl = float(data.get("expires_in", 3600))
        except (ValueError, KeyError, TypeError) as exc:
            raise AuthError("malformed token response") from exc
        self._token = token
        self._expires_at = self._clock() + max(0.0, ttl - c.token_expiry_margin_seconds)
        return token
