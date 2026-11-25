"""HMAC signing of tick requests: sign(`{timestamp}.{body}`) with JOB_TICK_SECRET."""

from __future__ import annotations

import hashlib
import hmac
import re

MAX_SKEW_SECONDS = 300
_TS = re.compile(r"[0-9]{1,12}")


def sign(secret: str, timestamp: str, body: bytes) -> str:
    return hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()


def verify(secret: str, timestamp: str, signature: str, body: bytes, now: float) -> bool:
    """True only for a fresh timestamp and a matching signature. Never raises on bad input."""
    if not secret or not _TS.fullmatch(timestamp or ""):
        return False
    if abs(now - int(timestamp)) > MAX_SKEW_SECONDS:
        return False
    # Compare bytes: str compare_digest raises TypeError on non-ASCII input.
    return hmac.compare_digest(
        sign(secret, timestamp, body).encode(), (signature or "").strip().encode()
    )
