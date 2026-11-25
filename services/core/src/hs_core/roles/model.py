"""The role model (A03, #45): pure functions shared by the core guards and the docs.

Roles are Appwrite user labels, alphanumeric only (ADR 0012): `parent`, `guardian`, `teacher`,
`opsadmin`, `curator`, `safeguardinglead`, `superadmin`. Matching is exact and case-sensitive;
`ops_admin`, `OpsAdmin` or ` opsadmin` are not roles and grant nothing.

`docs/ops/roles-matrix.json` is the shared fixture: these tables are tested against it here and
`apps/admin/src/lib/roles.ts` is tested against the same file (F06/H09), so the two cannot drift.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any

ROLES = ("parent", "guardian", "teacher", "opsadmin", "curator", "safeguardinglead", "superadmin")
STAFF_ROLES = ("opsadmin", "curator", "safeguardinglead", "superadmin")
# Roles a phone sign-in or the system may hold without a human decision.
MOBILE_ROLES = ("parent", "guardian", "teacher")

# Which roles each client admits. "admin" is the Next.js dashboard (SEC-4: MFA, A04 owns login).
APP_ROLES: dict[str, tuple[str, ...]] = {
    "parent": ("parent", "guardian"),
    "teacher": ("teacher",),
    "admin": STAFF_ROLES,
}

# Admin routes (the sidebar of apps/admin plus guarded API routes) and who may open them. A user
# may hold several roles; superadmin is NOT implicit (SR 6.2 has no super admin column).
_ALL, _CAT = STAFF_ROLES, ("opsadmin", "curator")
_OPS, _OSG = ("opsadmin",), ("opsadmin", "safeguardinglead")
ADMIN_ROUTES: dict[str, tuple[str, ...]] = {
    "/": _ALL,
    "/queues": _OSG,
    "/teachers": _OSG,
    "/families": _OSG,
    "/matching": _OPS,
    "/sessions": _OSG,
    "/payments": _OPS,
    "/payouts": _OPS,
    "/safeguarding": ("safeguardinglead",),
    "/curriculum": _CAT,
    "/question-bank": _CAT,
    "/reports": _OPS,
    "/settings": ("superadmin",),
    "/audit-log": _OSG,
    "/api/search": ("opsadmin", "curator", "safeguardinglead"),
}

ACTIVE_STATUS = "active"


def clean_labels(labels: Any) -> frozenset[str]:
    """Exact match against the known labels. Anything else is ignored: unknown labels, case or
    underscore variants, non-strings, and a bare string (which would iterate as characters)."""
    if not isinstance(labels, list | tuple | set | frozenset):
        return frozenset()
    return frozenset(x for x in labels if isinstance(x, str) and x in ROLES)


def _get(user: Any, name: str) -> Any:
    if isinstance(user, Mapping):
        return user.get(name)
    return getattr(user, name, None)


def roles_for(user: Any) -> frozenset[str]:
    """The roles a user may act with right now: their valid labels, and nothing at all when the
    account is not active (suspended, deactivated, deleted, soft-deleted)."""
    if user is None:
        return frozenset()
    if _get(user, "deleted_at"):
        return frozenset()
    status = _get(user, "status")
    if status is not None and status != ACTIVE_STATUS:
        return frozenset()
    return clean_labels(_get(user, "labels"))


def _as_set(roles: Iterable[str] | None) -> frozenset[str]:
    if roles is None or isinstance(roles, str):
        return frozenset()
    return frozenset(r for r in roles if isinstance(r, str))


def app_admits(app: Any, roles: Iterable[str] | None) -> bool:
    allowed = APP_ROLES.get(app) if isinstance(app, str) else None
    return bool(allowed and _as_set(roles) & set(allowed))


def admin_admits(route: Any, roles: Iterable[str] | None) -> bool:
    """Longest-boundary match of a path against the admin routes; unknown routes are denied."""
    if not isinstance(route, str) or not route.startswith("/") or ".." in route.split("/"):
        return False
    path = route.split("?", 1)[0].split("#", 1)[0]
    best: str | None = None
    for href in ADMIN_ROUTES:
        hit = path == "/" if href == "/" else path == href or path.startswith(href + "/")
        if hit and (best is None or len(href) > len(best)):
            best = href
    return best is not None and bool(_as_set(roles) & set(ADMIN_ROUTES[best]))


class AppDecision(Enum):
    ADMIT = "admit"
    WRONG_APP = "wrong_app"  # holds a role, but not one this app admits
    SUSPENDED = "suspended"  # would be admitted, but the account is suspended
    NO_ROLE = "no_role"  # no valid role at all, or account deactivated/deleted


@dataclass(frozen=True)
class Decision:
    kind: AppDecision
    suggested_app: str | None = None


# Which app to point a wrong-role user to, in order of preference.
_SUGGEST_ORDER = ("parent", "teacher", "admin")


def app_decision(app: str, labels: Any, status: str | None = ACTIVE_STATUS) -> Decision:
    """What an app should do with a signed-in user: used by the docs and mirrored in Flutter."""
    roles = clean_labels(labels)
    if status in ("deactivated", "deleted") or not roles:
        return Decision(AppDecision.NO_ROLE)
    if not app_admits(app, roles):
        other = next((a for a in _SUGGEST_ORDER if a != app and app_admits(a, roles)), None)
        return Decision(AppDecision.WRONG_APP, other)
    if status not in (None, ACTIVE_STATUS):
        return Decision(AppDecision.SUSPENDED)
    return Decision(AppDecision.ADMIT)
