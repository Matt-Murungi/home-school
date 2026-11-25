"""HTTP surface of the role model (A03).

- `GET /v1/me/access?app=parent|teacher|admin`: what the signed-in caller may do in that app,
  decided from the VERIFIED Appwrite account (labels) and the stored account status, never from
  anything the client sends. The apps call it on start and resume, so a role change shows up
  without a new sign-in ("role claims refreshed").
- `POST /v1/admin/roles/{user_id}/grant|revoke`: the only HTTP way to change a role. The actor is
  the verified caller; the body carries the role and a reason and nothing else (extra fields are
  422, so a spoofed `actor`/`roles` field is refused loudly).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field, StrictStr

from ..audit import AuditContext
from ..audit.api import audit_context
from ..auth import Principal, current_principal, require_roles
from ..errors import AppError
from ..repository import NotFound, RepositoryError
from .model import APP_ROLES, AppDecision, app_decision
from .service import Actor, RoleError

router = APIRouter(tags=["roles"])


class ActorLimiter:
    """At most `limit` role changes per actor per hour (sliding window, per instance). The
    endpoints are already guarded by role; this bounds a stolen or runaway admin session."""

    def __init__(self, limit: int, clock) -> None:
        import threading

        self.limit, self.clock, self._lock = limit, clock, threading.Lock()
        self._hits: dict[str, list[float]] = {}

    def check(self, actor_id: str) -> int | None:
        """None = allowed (and counted); otherwise seconds until a slot frees."""
        now = self.clock().timestamp()
        with self._lock:
            hits = [t for t in self._hits.get(actor_id, []) if now - t < 3600]
            if len(hits) >= self.limit:
                self._hits[actor_id] = hits
                return max(1, int(3600 - (now - hits[0])) + 1)
            hits.append(now)
            self._hits[actor_id] = hits
            return None


def _limit(request: Request, actor_id: str) -> None:
    lim = getattr(request.app.state, "role_limiter", None)
    wait = lim.check(actor_id) if lim else None
    if wait is not None:
        raise AppError(
            429,
            "rate_limited",
            "Too many role changes. Try again later.",
            {"Retry-After": str(wait)},
        )


class ChangeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: StrictStr = Field(max_length=32)
    reason: StrictStr = Field(default="", max_length=300)


def _raise(e: RoleError) -> AppError:
    return AppError(e.status, e.code, e.message)


def account_status(repo, user_id: str, app: str) -> str:
    """active | suspended | deactivated | deleted: the profile row, plus the teacher profile for
    the teacher app (a suspended teacher still has a parent account if they hold that role)."""
    from appwrite.query import Query

    try:
        p = repo.get("user_profile", user_id)
    except NotFound:
        p = {}
    if p.get("deleted_at"):
        return "deleted"
    status = p.get("status") or "active"
    if status != "active":
        return status
    if app == "teacher":
        rows = repo.list("teacher_profile", [Query.equal("user_id", [user_id]), Query.limit(5)])
        if any(r.get("status") == "suspended" and not r.get("deleted_at") for r in rows):
            return "suspended"
    return "active"


@router.get("/v1/me/access")
def my_access(
    request: Request,
    response: Response,
    app: str,
    principal: Principal = Depends(current_principal),  # noqa: B008
) -> dict:
    if app not in APP_ROLES:
        raise AppError(422, "invalid_app", "Unknown app")
    response.headers["Cache-Control"] = "no-store"
    try:
        status = account_status(request.app.state.repository, principal.user_id, app)
    except RepositoryError:
        raise AppError(503, "unavailable", "Temporarily unavailable") from None
    d = app_decision(app, principal.roles, status)
    return {
        "user_id": principal.user_id,
        "app": app,
        "roles": principal.roles,
        "status": status,
        "decision": d.kind.value,
        "suggested_app": d.suggested_app,
        # SEC-4: the admin dashboard additionally requires MFA; A04 owns that login flow.
        "mfa_required": app == "admin" and d.kind is AppDecision.ADMIT,
    }


def _actor(p: Principal) -> Actor:
    return Actor.user(p.user_id, p.roles)  # roles from the verified JWT/account only


@router.post("/v1/admin/roles/{user_id}/grant")
def grant(
    user_id: str,
    body: ChangeBody,
    request: Request,
    p: Principal = Depends(require_roles("opsadmin", "superadmin")),  # noqa: B008
    ctx: AuditContext = Depends(audit_context),  # noqa: B008
) -> dict:
    _limit(request, p.user_id)
    try:
        res = request.app.state.roles.grant_role(
            _actor(p), user_id, body.role, reason=body.reason, ctx=ctx
        )
    except RoleError as e:
        raise _raise(e) from None
    return {"user_id": res.user_id, "role": res.role, "changed": res.changed, "roles": res.roles}


@router.post("/v1/admin/roles/{user_id}/revoke")
def revoke(
    user_id: str,
    body: ChangeBody,
    request: Request,
    p: Principal = Depends(require_roles("opsadmin", "superadmin")),  # noqa: B008
    ctx: AuditContext = Depends(audit_context),  # noqa: B008
) -> dict:
    _limit(request, p.user_id)
    try:
        res = request.app.state.roles.revoke_role(
            _actor(p), user_id, body.role, reason=body.reason, ctx=ctx
        )
    except RoleError as e:
        raise _raise(e) from None
    return {"user_id": res.user_id, "role": res.role, "changed": res.changed, "roles": res.roles}
