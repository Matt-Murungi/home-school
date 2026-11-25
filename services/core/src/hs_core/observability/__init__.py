"""Error tracking (F08): ErrorReporter interface, Sentry adapter, PII scrubber."""

from .reporter import (
    ErrorReporter,
    FakeReporter,
    NoopReporter,
    SentryReporter,
    build_reporter,
    report_once,
)
from .scrub import scrub_breadcrumb, scrub_event, scrub_text

__all__ = [
    "ErrorReporter",
    "FakeReporter",
    "NoopReporter",
    "SentryReporter",
    "build_reporter",
    "report_once",
    "scrub_breadcrumb",
    "scrub_event",
    "scrub_text",
]
