"""Teacher application wizard backend (V01, #56)."""

from __future__ import annotations

from typing import Any

from ..settings import Settings
from .ports import (
    FakeCatalogue,
    FakeNotifier,
    FakeZones,
    LogNotifier,
    RepositoryAuditSink,
    RepositoryCatalogue,
    RepositoryZones,
)
from .service import ApplicationPolicy, ApplicationService

__all__ = ["ApplicationPolicy", "ApplicationService", "build_application_service"]


def build_application_service(
    settings: Settings, repo: Any, **overrides: Any
) -> ApplicationService:
    """Dev without Appwrite uses the in-memory repository, so reference data comes from the same
    repository (empty until seeded) exactly like production; tests inject fakes."""
    return ApplicationService(
        repo=repo,
        catalogue=overrides.get("catalogue") or RepositoryCatalogue(repo),
        zones=overrides.get("zones") or RepositoryZones(repo),
        audit=overrides.get("audit") or RepositoryAuditSink(repo),
        notifier=overrides.get("notifier") or LogNotifier(),
        policy=ApplicationPolicy.from_settings(settings),
    )


_ = (FakeCatalogue, FakeNotifier, FakeZones)  # re-exported for tests
