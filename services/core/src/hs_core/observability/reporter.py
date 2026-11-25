"""ErrorReporter interface, Sentry adapter, no-op and fake."""

from __future__ import annotations

import logging
import os
from typing import Any, Protocol

from .scrub import scrub_breadcrumb, scrub_event, scrub_text

log = logging.getLogger("hs_core.observability")


class ErrorReporter(Protocol):
    enabled: bool

    def capture_exception(
        self, exc: BaseException, *, request_id: str | None = None, tags: dict | None = None
    ) -> None: ...

    def capture_message(
        self,
        message: str,
        level: str = "error",
        *,
        request_id: str | None = None,
        tags: dict | None = None,
    ) -> None: ...

    def flush(self, timeout: float = 2.0) -> None: ...


class NoopReporter:
    """Used when no DSN is configured (dev, tests): reports nothing."""

    enabled = False

    def capture_exception(self, exc, *, request_id=None, tags=None) -> None:
        return None

    def capture_message(self, message, level="error", *, request_id=None, tags=None) -> None:
        return None

    def flush(self, timeout: float = 2.0) -> None:
        return None


class FakeReporter:
    """Records what would be sent, after the real scrubber ran. For tests."""

    enabled = True

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def capture_exception(self, exc, *, request_id=None, tags=None) -> None:
        self.events.append(
            {
                "type": type(exc).__name__,
                "message": scrub_text(str(exc)),
                "request_id": request_id,
                "tags": dict(tags or {}),
            }
        )

    def capture_message(self, message, level="error", *, request_id=None, tags=None) -> None:
        self.events.append(
            {
                "type": "message",
                "message": scrub_text(message),
                "level": level,
                "request_id": request_id,
                "tags": dict(tags or {}),
            }
        )

    def flush(self, timeout: float = 2.0) -> None:
        return None


class SentryReporter:
    """Sentry adapter. Uses its own Client/Scope (no global hooks, no auto-integrations), so
    only explicit captures are sent. DSN comes from configuration only."""

    enabled = True

    def __init__(
        self,
        dsn: str,
        *,
        environment: str,
        release: str,
        transport: Any = None,
        sample_rate: float = 1.0,
    ) -> None:
        if transport is None and "PYTEST_CURRENT_TEST" in os.environ:
            raise RuntimeError("refusing to build a network Sentry transport under test")
        import sentry_sdk
        from sentry_sdk.scope import use_scope

        self._use_scope = use_scope
        self._sdk = sentry_sdk
        self._client = sentry_sdk.Client(
            dsn=dsn,
            environment=environment,
            release=release,
            transport=transport,
            sample_rate=sample_rate,
            before_send=scrub_event,
            before_breadcrumb=scrub_breadcrumb,
            max_request_body_size="never",
            traces_sample_rate=0.0,
            default_integrations=False,
            auto_enabling_integrations=False,
            server_name="",
        )

    def _scope(self, request_id, tags):
        scope = self._sdk.Scope(client=self._client)
        scope.set_tag("request_id", request_id or "-")
        for k, v in (tags or {}).items():
            scope.set_tag(k, str(v))
        return scope

    def capture_exception(self, exc, *, request_id=None, tags=None) -> None:
        try:
            scope = self._scope(request_id, tags)
            with self._use_scope(scope):
                scope.capture_exception(exc)
        except Exception:  # noqa: BLE001 - monitoring must never break the request
            log.warning("error reporting failed")

    def capture_message(self, message, level="error", *, request_id=None, tags=None) -> None:
        try:
            scope = self._scope(request_id, tags)
            with self._use_scope(scope):
                scope.capture_message(scrub_text(message), level=level)
        except Exception:  # noqa: BLE001
            log.warning("error reporting failed")

    def flush(self, timeout: float = 2.0) -> None:
        try:
            self._client.flush(timeout=timeout)
        except Exception:  # noqa: BLE001
            log.warning("error reporting flush failed")


def build_reporter(settings, transport: Any = None) -> ErrorReporter:
    dsn = settings.sentry_dsn.get_secret_value().strip()
    if not dsn:
        return NoopReporter()
    return SentryReporter(
        dsn,
        environment=settings.hs_env,
        release=settings.git_sha if settings.git_sha != "unknown" else settings.app_version,
        transport=transport,
    )


_REPORTED = "_hs_reported"


def report_once(reporter: ErrorReporter, exc: BaseException, request_id: str | None) -> None:
    """Report an unhandled exception at most once (two handlers can see the same one)."""
    if getattr(exc, _REPORTED, False):
        return
    try:
        setattr(exc, _REPORTED, True)
    except Exception:  # noqa: BLE001, S110 - exotic exception types
        pass
    reporter.capture_exception(exc, request_id=request_id)
