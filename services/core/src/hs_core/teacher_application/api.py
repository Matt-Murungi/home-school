"""`/v1/teacher/application`: the teacher sees and changes only their own application (the user id
comes from the verified token, never from the URL or body)."""

from __future__ import annotations

import re

from fastapi import APIRouter, Depends, Header, Request, Response

from ..auth import Principal, require_roles
from ..errors import AppError
from ..repository import RepositoryUnavailable
from .models import DraftBody, SubmitBody

MAX_BODY_BYTES = 64 * 1024
_KEY = re.compile(r"[A-Za-z0-9_-]{16,64}")


def _guard(request: Request) -> None:
    declared = request.headers.get("content-length", "0")
    if declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        raise AppError(413, "payload_too_large", "Request body too large")


router = APIRouter(
    prefix="/v1/teacher/application", tags=["teacher-application"], dependencies=[Depends(_guard)]
)


def _call(request: Request, name: str, *args):
    """Run a service method; a storage outage is a retriable 503, never a 500 with details."""
    try:
        return getattr(request.app.state.teacher_application, name)(*args)
    except RepositoryUnavailable:
        raise AppError(503, "unavailable", "Please try again shortly") from None


def _private(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


@router.get("")
def get_application(
    request: Request,
    response: Response,
    p: Principal = Depends(require_roles("teacher")),  # noqa: B008
) -> dict:
    _private(response)
    return _call(request, "get", p)


@router.get("/reference")
def reference(
    request: Request,
    response: Response,
    p: Principal = Depends(require_roles("teacher")),  # noqa: B008
) -> dict:
    _private(response)
    return _call(request, "reference", p)


@router.put("/draft")
def save_draft(
    body: DraftBody,
    request: Request,
    response: Response,
    p: Principal = Depends(require_roles("teacher")),  # noqa: B008
) -> dict:
    _private(response)
    return _call(request, "save_draft", p, body)


@router.post("/submit")
def submit(
    body: SubmitBody,
    request: Request,
    response: Response,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    p: Principal = Depends(require_roles("teacher")),  # noqa: B008
) -> dict:
    _private(response)
    if idempotency_key is None or _KEY.fullmatch(idempotency_key) is None:
        raise AppError(400, "idempotency_key_required", "Send a unique Idempotency-Key header")
    return _call(request, "submit", p, body, idempotency_key)


@router.post("/reopen")
def reopen(
    request: Request,
    response: Response,
    p: Principal = Depends(require_roles("teacher")),  # noqa: B008
) -> dict:
    _private(response)
    return _call(request, "reopen", p)
