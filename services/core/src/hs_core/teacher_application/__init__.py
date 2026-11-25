"""Teacher application wizard backend (V01, #56)."""

from __future__ import annotations

from typing import Any

from ..settings import Settings
from .ports import (
    FakeCatalogue,
    FakeNotifier,
    FakeZones,
    NotificationServiceNotifier,
    RepositoryCatalogue,
    RepositoryZones,
    build_audit_sink,
)
from .service import ApplicationPolicy, ApplicationService

__all__ = ["ApplicationPolicy", "ApplicationService", "build_application_service"]


def notifier_for(notifications: Any):
    from .ports import LogNotifier

    return (
        NotificationServiceNotifier(notifications) if notifications is not None else LogNotifier()
    )


def build_application_service(
    settings: Settings, repo: Any, **overrides: Any
) -> ApplicationService:
    """Dev without Appwrite uses the in-memory repository, so reference data comes from the same
    repository (empty until seeded) exactly like production; tests inject fakes."""
    return ApplicationService(
        repo=repo,
        catalogue=overrides.get("catalogue") or RepositoryCatalogue(repo),
        zones=overrides.get("zones") or RepositoryZones(repo),
        audit=overrides.get("audit") or build_audit_sink(repo),
        notifier=overrides.get("notifier") or notifier_for(overrides.get("notifications")),
        policy=ApplicationPolicy.from_settings(settings),
    )


_ = (FakeCatalogue, FakeNotifier, FakeZones)  # re-exported for tests
