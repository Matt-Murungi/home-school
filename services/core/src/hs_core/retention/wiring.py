"""Builds the retention job from Settings (called by main.create_app)."""

from __future__ import annotations

from typing import Any

from ..jobs.alerts import AlertSink
from ..repository import Repository
from ..settings import Settings
from .files import FakeRetentionFileStore, RetentionFileStore
from .purger import PurgeConfig
from .run import AuditServiceSink, Deps, RetentionJob


def purge_config(settings: Settings) -> PurgeConfig:
    return PurgeConfig(
        enforce=settings.retention_enforce,
        quarantine_days=settings.retention_quarantine_days,
        max_delete_pct=settings.retention_max_delete_pct,
        blast_override=settings.retention_blast_override,
        allow_append_only=settings.retention_allow_append_only_purge,
    )


def build_file_store(settings: Settings) -> RetentionFileStore:
    """Real Storage when Appwrite is configured, else the in-memory fake (dev and tests only)."""
    if settings.appwrite_endpoint and settings.appwrite_api_key.get_secret_value():
        from appwrite.client import Client

        from .files import AppwriteRetentionFileStore

        client = (
            Client()
            .set_endpoint(settings.appwrite_endpoint)
            .set_project(settings.appwrite_project_id)
            .set_key(settings.appwrite_api_key.get_secret_value())
        )
        return AppwriteRetentionFileStore(client)
    return FakeRetentionFileStore()


def build_retention_job(
    settings: Settings,
    raw_repository: Repository,
    alert: AlertSink | None,
    audit_service: Any | None = None,
    files: RetentionFileStore | None = None,
) -> RetentionJob:
    """The purge writes through the RAW repository: it is the one allowed hard-deleter, and it is
    audited by one summary event per run (docs/ops/audit.md "Hooks"), not row by row."""
    return RetentionJob(
        Deps(
            repo=raw_repository,
            files=files or build_file_store(settings),
            config=purge_config(settings),
            overrides_text=settings.retention_periods,
            lease_s=settings.retention_lease_s,
            alert=alert,
            audit=AuditServiceSink(audit_service) if audit_service is not None else None,
        )
    )
