"""Builders for the role service and the reconcile policy (kept out of `__init__` so that
`hs_core.auth` can import `roles.model` without pulling in the service)."""

from __future__ import annotations

from typing import Any

from ..settings import Settings
from .reconcile import ReconcilePolicy
from .service import SIGNUP, Actor, RoleService

__all__ = ["SIGNUP", "Actor", "RoleService", "build_role_service", "reconcile_policy"]


def build_role_service(repo: Any, users: Any, permissions: Any | None = None) -> RoleService:
    """`repo` is the app's AuditedRepository: every role write is audited (M06)."""
    return RoleService(repo, users, permissions=permissions)


def reconcile_policy(settings: Settings) -> ReconcilePolicy:
    return ReconcilePolicy(
        grace_s=settings.role_reconcile_grace_s,
        remove_unrecorded_staff=settings.role_reconcile_remove_unrecorded_staff,
    )
