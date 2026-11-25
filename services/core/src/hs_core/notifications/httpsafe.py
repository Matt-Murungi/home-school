"""Shared HTTP hygiene for provider adapters (FCM, OAuth token endpoint, Resend).

- Redirects are NEVER followed (`follow_redirects=False`): the MoMo review found that auto-followed
  redirects can carry credentials to another host. A 3xx is a failure of the call.
- Timeouts are explicit and short; the connection pool is small.
- Proxies: httpx honours `HTTPS_PROXY`/`HTTP_PROXY`/`NO_PROXY` from the environment
  (`trust_env=True`). That is intended for deployments that must egress through a proxy; a proxy
  sees TLS traffic only as CONNECT tunnels. Documented in docs/ops/notifications.md.
- `Retry-After` is untrusted input: parsed defensively and clamped to [0, cap].
"""

from __future__ import annotations

import math
import re
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

import httpx

MAX_RETRY_AFTER_S = 3600.0
_DELTA = re.compile(r"\s*([+-]?[0-9]+(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?)\s*\Z", re.ASCII)


def build_client(connect_s: float = 3.05, read_s: float = 10.0) -> httpx.Client:
    return httpx.Client(
        timeout=httpx.Timeout(connect=connect_s, read=read_s, write=5.0, pool=3.05),
        follow_redirects=False,
        limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
    )


def parse_retry_after(
    value: str | None, now: datetime, cap: float = MAX_RETRY_AFTER_S
) -> float | None:
    """Seconds to wait, clamped to [0, cap], or None when the header is absent or unusable.
    Accepts delta-seconds and an HTTP date (relative to the injected `now`, never the wall clock).
    Negative values clamp to 0; huge values clamp to `cap`; nan, inf and garbage give None."""
    if value is None:
        return None
    text = value.strip()
    if not text or len(text) > 64:
        return None
    m = _DELTA.fullmatch(text)
    if m:
        try:
            seconds = float(m.group(1))
        except ValueError:
            return None
        if math.isnan(seconds):
            return None
        return min(max(seconds, 0.0), cap)
    try:
        when = parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError, OverflowError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return min(max((when - now).total_seconds(), 0.0), cap)
