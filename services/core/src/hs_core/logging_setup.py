"""Structured JSON logs; every line carries request_id."""

from __future__ import annotations

import json
import logging
import sys
from contextvars import ContextVar
from datetime import UTC, datetime

from .observability.scrub import scrub_text

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")
_RESERVED = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {"message", "asctime"}


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
                d[k] = v
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
