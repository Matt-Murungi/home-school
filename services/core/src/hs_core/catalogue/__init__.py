"""Curriculum catalogue read API (K01). See `service.CatalogueService` and `api.router`."""

from __future__ import annotations

from ..repository import Repository
from ..settings import Settings
from .service import CatalogueService, Viewer


def build_catalogue_service(settings: Settings, repo: Repository) -> CatalogueService:
    return CatalogueService(repo, cache_entries=settings.catalogue_cache_entries)


__all__ = ["CatalogueService", "Viewer", "build_catalogue_service"]
