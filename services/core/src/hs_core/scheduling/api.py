"""POST /admin/scheduling/generate-now: an operations admin (re)generates one assignment's sessions.
Reason required, audited."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field, field_validator

from ..audit.api import audit_context
from ..audit.context import AuditContext
from ..auth import require_roles
from ..errors import AppError
from .generator import AssignmentNotFound

router = APIRouter()


class GenerateNow(BaseModel):
    assignment_id: str = Field(min_length=1, max_length=36)
    reason: str = Field(min_length=3, max_length=500)
    reconcile_from: date | None = (
        None  # also prune surplus future sessions from this Kampala date (plan/slot change)
    )

    @field_validator("reason")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if len(v.strip()) < 3:
            raise ValueError("reason is required")
        return v.strip()


@router.post("/admin/scheduling/generate-now")
async def generate_now(
    body: GenerateNow,
    request: Request,
    _admin=Depends(require_roles("opsadmin")),  # noqa: B008
    ctx: AuditContext = Depends(audit_context),  # noqa: B008
) -> dict:
    gen = request.app.state.scheduler
    try:
        res = await run_in_threadpool(
            lambda: gen.generate_now(
                body.assignment_id,
                ctx=ctx,
                reason=body.reason,
                reconcile_from=body.reconcile_from,
            )
        )
    except AssignmentNotFound:
        raise AppError(404, "not_found", "Assignment not found") from None
    return {
        "assignment_id": res.assignment_id,
        "status": res.status,
        "reason": res.reason,
        "created": res.created,
        "existing": res.existing,
        "conflicts": res.conflicts,
        "flagged": res.flagged,
        "leave_cancelled": res.leave_cancelled,
        "pruned": res.pruned,
        "resolved": res.resolved,
        "lag_days": res.lag_days,
    }
