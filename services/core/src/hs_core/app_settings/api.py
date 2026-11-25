"""`/v1/admin/settings`: the super admin's settings API (H05 #134, A-50, NFR-18).

Superadmin only, for reading as well as writing (SR 6.1/PRD: platform settings are the super
admin's; owner question 48 asks whether ops may read). Every write is audited with the
reason, the actor comes from the verified JWT (`Depends(audit_context)`), bodies refuse unknown
fields (mass assignment) and are size-capped.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field, StrictInt

from ..audit.api import audit_context
from ..audit.context import AuditContext
from ..audit.errors import AuditUnavailable
from ..auth import require_roles
from ..errors import AppError
from .model import SettingDef, SettingsError, encode
from .service import AppSettings, SettingState

router = APIRouter(prefix="/v1/admin/settings", tags=["settings"])
MAX_BODY_BYTES = 4096
KEY_RE = re.compile(r"[a-z][a-z0-9_.]{0,63}")
SUPERADMIN = require_roles("superadmin")


class ChangeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: Any
    reason: str = Field(max_length=2000)
    revision: StrictInt = Field(ge=0)
    effective_at: datetime | None = None


class ReasonBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(max_length=2000)
    revision: StrictInt = Field(ge=0)


def _svc(request: Request) -> AppSettings:
    return request.app.state.app_settings


def _key(key: str) -> str:
    if not KEY_RE.fullmatch(key):
        raise AppError(404, "unknown_setting", "Unknown setting")
    return key


def _cap(request: Request) -> None:
    declared = request.headers.get("content-length", "0")
    if declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        raise AppError(413, "payload_too_large", "Request body too large")


def _def_view(d: SettingDef) -> dict:
    out: dict[str, Any] = {
        "key": d.key,
        "group": d.group,
        "kind": d.kind,
        "description": d.description,
        "unit": d.unit,
        "applies": d.applies,
        "requires_reason": d.requires_reason,
        "restart_required": d.restart_required,
    }
    if d.kind == "map":
        out["fields"] = [{"name": f.name, "min": f.lo, "max": f.hi} for f in d.fields]
        if d.sum_to is not None:
            out["sum_to"] = d.sum_to
    else:
        out["min"], out["max"] = d.lo, d.hi
    return out


def _state_view(spec, s: SettingState) -> dict:
    out = _def_view(spec.defs[s.key])
    out.update(
        value=s.value,
        default=s.default,
        source=s.source,
        revision=s.revision,
        pending=None
        if s.pending is None
        else {
            "revision": s.pending.revision,
            "value": s.pending.value,
            "effective_at": s.pending.effective_at.isoformat(),
        },
    )
    return out


def _history_view(row: dict) -> dict:
    import json

    def val(v):
        return None if v is None else json.loads(v)

    return {
        "revision": row["revision"],
        "op": row["op"],
        "value": val(row.get("value")),
        "previous_value": val(row.get("previous_value")),
        "effective_at": row.get("effective_at"),
        "cancels_revision": row.get("cancels_revision"),
        "reason": row.get("reason"),
        "actor_id": row.get("actor_id"),
        "created_at": row.get("created_at"),
    }


def _guard(fn):
    try:
        return fn()
    except AuditUnavailable:
        raise AppError(503, "audit_unavailable", "Audit log unavailable") from None
    except SettingsError as e:  # pragma: no cover
        raise AppError(500, "settings_error", str(e)) from None


@router.get("")
def list_settings(request: Request, _admin=Depends(SUPERADMIN)) -> dict:  # noqa: B008
    svc = _svc(request)
    return {
        "groups": [{"id": g, "title": t} for g, t in svc.spec.groups],
        "settings": [_state_view(svc.spec, s) for s in svc.states()],
    }


@router.put("/{key}")
def put_setting(
    key: str,
    body: ChangeBody,
    request: Request,
    ctx: AuditContext = Depends(audit_context),  # noqa: B008
    _admin=Depends(SUPERADMIN),  # noqa: B008
) -> dict:
    _cap(request)
    svc = _svc(request)
    k = _key(key)
    state = _guard(
        lambda: svc.change(
            k, body.value, reason=body.reason, expected_revision=body.revision, ctx=ctx,
            effective_at=body.effective_at,
        )
    )  # fmt: skip
    return _state_view(svc.spec, state)


@router.post("/{key}/rollback")
def rollback_setting(
    key: str,
    body: ReasonBody,
    request: Request,
    ctx: AuditContext = Depends(audit_context),  # noqa: B008
    _admin=Depends(SUPERADMIN),  # noqa: B008
) -> dict:
    _cap(request)
    svc = _svc(request)
    state = _guard(
        lambda: svc.rollback(
            _key(key), reason=body.reason, expected_revision=body.revision, ctx=ctx
        )
    )
    return _state_view(svc.spec, state)


@router.post("/{key}/cancel-pending")
def cancel_pending(
    key: str,
    body: ReasonBody,
    request: Request,
    ctx: AuditContext = Depends(audit_context),  # noqa: B008
    _admin=Depends(SUPERADMIN),  # noqa: B008
) -> dict:
    _cap(request)
    svc = _svc(request)
    state = _guard(
        lambda: svc.cancel_pending(
            _key(key), reason=body.reason, expected_revision=body.revision, ctx=ctx
        )
    )
    return _state_view(svc.spec, state)


@router.get("/{key}/history")
def setting_history(
    key: str,
    request: Request,
    before: int | None = None,
    limit: int = 50,
    _admin=Depends(SUPERADMIN),  # noqa: B008
) -> dict:
    svc = _svc(request)
    if limit < 1 or limit > 100 or (before is not None and before < 1):
        raise AppError(422, "validation_error", "Invalid paging")
    rows = _guard(lambda: svc.history(_key(key), before=before, limit=limit))
    return {
        "key": key,
        "changes": [_history_view(r) for r in rows],
        "next_before": rows[-1]["revision"] if len(rows) == limit and rows else None,
    }


__all__ = ["router", "encode"]
