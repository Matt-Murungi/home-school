"""Structured JSON logs; every line carries request_id."""

from __future__ import annotations

import json
import logging
import sys
from contextvars import ContextVar
from datetime import UTC, datetime

from .observability.scrub import FILTERED, _sensitive, scrub_data, scrub_text

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")
_RESERVED = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {"message", "asctime"}


def _safe_extra(key: str, value: object) -> object:
    """`extra=` fields are scrubbed like event data: sensitive keys masked, text patterns applied.

    Fail closed: anything that cannot be scrubbed within the caps is replaced, never logged raw.
    """
    if _sensitive(key):
        return FILTERED
    try:
        return scrub_data(value)
    except Exception:  # noqa: BLE001
        return FILTERED


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        d = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname.lower(),
            "logger": record.name,
            "message": scrub_text(record.getMessage()),
            "request_id": request_id_var.get(),
        }
        for k, v in record.__dict__.items():
            if k not in _RESERVED and k not in d:
                d[k] = _safe_extra(k, v)
        if record.exc_info:
            d["exc"] = scrub_text(self.formatException(record.exc_info))
        return json.dumps(d, default=str)


def configure_logging(level: str = "INFO") -> None:
    h = logging.StreamHandler(sys.stderr)
    h.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [h]
    root.setLevel(level.upper())
    for n in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(n)
        lg.handlers[:] = []
        lg.propagate = n != "uvicorn.access"  # access line is emitted by our middleware
