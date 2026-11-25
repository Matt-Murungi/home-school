"""Idempotency keys. A job keyed by (name, period) can only ever be created once: the run's row
ID is a hash of the key and Appwrite refuses a second row with the same ID (409)."""

from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime


def dedupe_key(name: str, period: str | date | datetime) -> str:
    """`payout` + `2027-W03` -> `payout:2027-W03`. Datetimes are normalised to UTC ISO."""
    if not name or not name.strip():
        raise ValueError("job name is required")
    if isinstance(period, datetime):
        period = period.astimezone(UTC).isoformat()
    elif isinstance(period, date):
        period = period.isoformat()
    if not period or not str(period).strip():
        raise ValueError("period is required")
    return f"{name}:{period}"


def row_id(*parts: object) -> str:
    """Deterministic Appwrite-safe row ID (<= 36 chars, [0-9a-f])."""
    return hashlib.sha256("\x1f".join(map(str, parts)).encode()).hexdigest()[:36]


def run_row_id(key: str) -> str:
    return row_id("job_run", key)
