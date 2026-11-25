"""FastAPI app factory: health, readiness, request IDs, error format, auth wiring."""

from __future__ import annotations

import logging
import re
import time
import uuid

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse

from . import errors
from .audit import AuditedRepository, AuditService, client_ip_var
from .audit import recover as audit_recover
from .audit.adapters import LedgerAuditAdapter, TemplateAuditAdapter
from .audit.admin_events import RateLimiter
from .audit.admin_events import router as audit_events_router
from .audit.context import server_request_key_var
from .auth import AppwriteJwtVerifier, Principal, TokenVerifier, current_principal, require_roles
from .auth_otp import build_otp_service
from .auth_otp import jobs as otp_jobs
from .auth_otp.api import client_ip
from .auth_otp.api import router as otp_router
from .catalogue import build_catalogue_service
from .catalogue.api import router as catalogue_router
from .jobs.alerts import Alert, AlertSink, LogAlertSink
from .jobs.api import router as jobs_router
from .jobs.runner import Runner
from .jobs.schedule import JOBS
from .ledger import AppwriteLedgerStore, LedgerService
from .logging_setup import configure_logging, request_id_var
from .notifications import build_notification_service
from .notifications import dispatch as notification_dispatch_job
from .observability import ErrorReporter, build_reporter, report_once
from .permissions.wiring import wire_permissions
from .repository import Repository, build_repository
from .retention import jobs as retention_jobs
from .retention.wiring import build_retention_job
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
    catalogue_service=None,
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
    raw_repository = repository or build_repository(settings)
    # M06: the app only hands out the audited wrapper; audited tables need an AuditContext.
    sink = alert_sink or LogAlertSink()

    def audit_alert(kind: str, info) -> None:  # key collisions, unverifiable intents
        sink.send(Alert(kind=kind, job="audit", dedupe_key=kind, error=str(dict(info))[:300]))

    app.state.audit = AuditService(
        raw_repository, lead_blanket_read=settings.audit_lead_blanket_read, alert=audit_alert
    )
    app.state.repository = AuditedRepository(raw_repository, app.state.audit)
    app.state.audit_rate = RateLimiter(settings.admin_audit_rate_per_min)
    audit_recover.bind(
        raw_repository,
        app.state.audit,
        lookback_hours=settings.audit_recover_lookback_hours,
        grace_s=settings.audit_recover_grace_s,
    )
    app.state.verifier = verifier or AppwriteJwtVerifier(
        settings.appwrite_endpoint, settings.appwrite_project_id
    )
    app.state.runner = runner or Runner(
        app.state.repository,
        JOBS,
        alert=sink,
        failure_alert_after=settings.job_alert_after_failures,
    )
    app.state.travel = build_travel_service(settings, app.state.repository)
    travel_jobs.configure(app.state.travel, app.state.repository)
    # M05 permission sync rewrites `$permissions` of rows, many of them in audited tables, as
    # derived state with no actor (M05 drift report `permission-audit` is its check): it gets the
    # raw repository, not the audited wrapper (docs/ops/audit.md "Hooks", M05 row).
    app.state.permissions = wire_permissions(  # M05: binds the permission jobs
        raw_repository, app.state.runner, alert_sink, settings
    )
    app.state.reporter = reporter or build_reporter(settings)
    app.state.otp = otp_service or build_otp_service(
        settings, app.state.repository, sms_sender, user_directory
    )
    app.state.notifications = notification_service or build_notification_service(
        settings, app.state.repository, alert_sink, audit=TemplateAuditAdapter(app.state.audit)
    )
    # $01: the ledger writes through the raw repository (append-only, its own seal protocol) and is
    # audited by summary events (M06 round 3 decision: docs/ops/audit.md "Hooks").
    app.state.ledger = LedgerService(
        AppwriteLedgerStore(raw_repository), audit=LedgerAuditAdapter(app.state.audit)
    )
    app.state.catalogue = catalogue_service or build_catalogue_service(
        settings, app.state.repository
    )
    # Fly sets Fly-Client-IP at its edge; outside dev we are always behind it.
    app.state.trust_proxy_ip = (
        settings.hs_env != "dev" if trust_proxy_ip is None else trust_proxy_ip
    )
    # C03: the purge job is the one allowed hard-deleter, so it gets the raw repository and writes
    # ONE audit summary per run (docs/ops/audit.md "Hooks"); dry run unless RETENTION_ENFORCE.
    app.state.retention = build_retention_job(settings, raw_repository, sink, app.state.audit)
    retention_jobs.bind(app.state.retention)
    otp_jobs.bind(app.state.repository)
    notification_dispatch_job.bind(app.state.notifications)
    errors.install(app)
    app.include_router(jobs_router)
    app.include_router(otp_router)
    app.include_router(audit_events_router)
    app.include_router(catalogue_router)

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        rid = request.headers.get("x-request-id", "")
        if not is_safe_request_id(rid):
            rid = uuid.uuid4().hex
        token = request_id_var.set(rid)
        # the audit dedupe key comes from the SERVER, never from the client's header
        audit_rk = server_request_key_var.set(uuid.uuid4().hex)
        ip_token = client_ip_var.set(client_ip(request))
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
            client_ip_var.reset(ip_token)
            server_request_key_var.reset(audit_rk)
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
