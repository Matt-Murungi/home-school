"""Startup wiring: build the PermissionService for the running app and bind the jobs to it."""

from __future__ import annotations

import dataclasses
from typing import Any

from ..jobs.alerts import AlertSink
from . import jobs as permission_jobs
from .policy import DEFAULT_POLICY
from .service import PermissionService, RetryQueue


def wire_permissions(
    repo: Any, runner: Any, alerts: AlertSink | None = None, settings: Any | None = None
) -> PermissionService:
    """The service writes with the same repository (API key) as everything else, queues its retry
    jobs on the app's runner and, when talking to a real Appwrite, writes file permissions through
    the SDK Storage service. Binds `address-revoke`, `permission-reconcile`, `permission-audit`."""
    from ..repository import AppwriteRepository

    files = None
    if isinstance(repo, AppwriteRepository):
        from .files import AppwriteFileStore

        files = AppwriteFileStore(repo._client)
    policy = DEFAULT_POLICY
    flag = getattr(settings, "audit_lead_blanket_read", None)  # M06's setting wins (owner Q12)
    if isinstance(flag, bool):
        policy = dataclasses.replace(policy, audit_log_lead_read=flag)
    svc = PermissionService(repo, files=files, alerts=alerts, policy=policy)
    svc.queue = RetryQueue(runner, svc.clock)
    permission_jobs.bind(lambda: svc)
    return svc
