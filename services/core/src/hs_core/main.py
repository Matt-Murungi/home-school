"""FastAPI app factory: health, readiness, request IDs, error format, auth wiring."""

from __future__ import annotations

import logging
import re
import time
import uuid

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse

from . import errors
from .auth import AppwriteJwtVerifier, Principal, TokenVerifier, current_principal, require_roles
from .auth_otp import build_otp_service
from .auth_otp import jobs as otp_jobs
from .auth_otp.api import router as otp_router
from .jobs.alerts import AlertSink, LogAlertSink
from .jobs.api import router as jobs_router
from .jobs.runner import Runner
from .jobs.schedule import JOBS
from .logging_setup import configure_logging, request_id_var
from .notifications import build_notification_service
from .notifications import dispatch as notification_dispatch_job
from .observability import ErrorReporter, build_reporter, report_once
from .repository import Repository, build_repository
from .settings import Settings, load_settings
from .travel import jobs as travel_jobs
from .travel.factory import build_travel_service

log = logging.getLogger("hs_core")
_SAFE_ID = re.compile(r"[A-Za-z0-9._-]{1,64}")


def is_safe_request_id(value: str) -> bool:
    """fullmatch: `$` would admit a trailing newline."""
    return _SAFE_ID.fullmatch(value) is not None


def create_app(
    settings: Settings | None = None,
    repository: Repository | None = None,
    verifier: TokenVerifier | None = None,
    runner: Runner | None = None,
    alert_sink: AlertSink | None = None,
    reporter: ErrorReporter | None = None,
    otp_service=None,
    notification_service=None,
    sms_sender=None,
    user_directory=None,
    trust_proxy_ip: bool | None = None,
) -> FastAPI:
    settings = settings or load_settings()
    app = FastAPI(
        title="Home School core",
        version=settings.app_version,
        docs_url="/docs" if settings.hs_env == "dev" else None,
        redoc_url=None,
        openapi_url="/openapi.json" if settings.hs_env == "dev" else None,
    )
    app.state.settings = settings
    app.state.repository = repository or build_repository(settings)
    app.state.verifier = verifier or AppwriteJwtVerifier(
        settings.appwrite_endpoint, settings.appwrite_project_id
    )
    app.state.runner = runner or Runner(
        app.state.repository,
        JOBS,
        alert=alert_sink or LogAlertSink(),
        failure_alert_after=settings.job_alert_after_failures,
    )
    app.state.travel = build_travel_service(settings, app.state.repository)
    travel_jobs.configure(app.state.travel, app.state.repository)
    app.state.reporter = reporter or build_reporter(settings)
    app.state.otp = otp_service or build_otp_service(
        settings, app.state.repository, sms_sender, user_directory
    )
    app.state.notifications = notification_service or build_notification_service(
        settings, app.state.repository, alert_sink
    )
    # Fly sets Fly-Client-IP at its edge; outside dev we are always behind it.
    app.state.trust_proxy_ip = (
        settings.hs_env != "dev" if trust_proxy_ip is None else trust_proxy_ip
    )
    otp_jobs.bind(app.state.repository)
    notification_dispatch_job.bind(app.state.notifications)
    errors.install(app)
    app.include_router(jobs_router)
    app.include_router(otp_router)

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        rid = request.headers.get("x-request-id", "")
        if not is_safe_request_id(rid):
            rid = uuid.uuid4().hex
        token = request_id_var.set(rid)
        start = time.perf_counter()
        status = 500
        try:
            try:
                response = await call_next(request)
            except Exception as exc:  # noqa: BLE001 - mapped to the standard 500 body
                log.error("unhandled exception", exc_info=True)
                report_once(app.state.reporter, exc, rid)
                response = JSONResponse(errors.body("internal_error", "Internal server error"), 500)
            status = response.status_code
            response.headers["X-Request-ID"] = rid
            return response
        finally:
            log.info(
                "request",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "status": status,
                    "duration_ms": round((time.perf_counter() - start) * 1000, 1),
                },
            )
            request_id_var.reset(token)

    def health():
        return {"status": "ok", "version": settings.app_version, "git_sha": settings.git_sha}

    def ready(request: Request):
        try:
            request.app.state.repository.ping()
        except Exception as e:  # noqa: BLE001 - never leak backend details
            log.warning("readiness check failed", extra={"error_type": type(e).__name__})
            return JSONResponse(errors.body("not_ready", "Database unavailable"), 503)
        return {"status": "ready"}

    for path in ("/health", "/healthz"):
        app.get(path, include_in_schema=path == "/health")(health)
    for path in ("/ready", "/readyz"):
        app.get(path, include_in_schema=path == "/ready")(ready)

    @app.get("/v1/me")
    def me(p: Principal = Depends(current_principal)):  # noqa: B008
        log.info("me requested")
        return {"user_id": p.user_id, "roles": p.roles}

    @app.get("/v1/admin/ping")
    def admin_ping(p: Principal = Depends(require_roles("opsadmin"))):  # noqa: B008
        return {"ok": True}

    @app.get("/v1/echo")
    def echo(msg: str):
        return {"msg": msg}

    return app


def app_factory() -> FastAPI:
    """uvicorn --factory hs_core.main:app_factory ; fails fast on bad config."""
    s = load_settings()
    configure_logging(s.log_level)
    return create_app(s)
