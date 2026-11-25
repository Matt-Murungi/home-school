"""PII-safe logging helpers. Never log tokens, keys, full MSISDNs or raw callback bodies."""

from __future__ import annotations

import logging

log = logging.getLogger("py_momo")


def mask_msisdn(msisdn: str) -> str:
    digits = "".join(c for c in str(msisdn) if c.isdigit())
    if len(digits) <= 6:
        return "***"
    return f"{digits[:3]}***{digits[-3:]}"
