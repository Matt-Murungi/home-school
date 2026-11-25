"""HTTP endpoints of the notification channels (N02).

- `PUT /v1/me/devices` registers the caller's FCM token (call after sign-in), `DELETE` removes it
  (call on sign-out, BEFORE the session is destroyed). Authenticated; the token belongs to the
  caller only (M04 upsert rule moves a token that changes user on a shared phone).
- `POST /v1/webhooks/resend`: public, authenticated by the Svix signature, not by a user. Reads at
  most 256 KiB. 200 = accepted (also for a duplicate), 401 bad signature or timestamp, 400 signed
  but malformed, 413 too large, 503 provider not configured, 5xx on our own failure (the provider
  retries). Responses never echo input.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field, StrictStr

from ..auth import Principal, current_principal
from ..errors import AppError
from .channels import WebhookRejected
from .devices import APPS, PLATFORMS
from .resend import MAX_WEBHOOK_BODY

log = logging.getLogger("hs_core.notifications.api")
router = APIRouter(tags=["notifications"])
_STAFF = {"opsadmin", "superadmin", "curator", "safeguardinglead"}
_APP_ROLES = {"parent": {"parent", "guardian"}, "teacher": {"teacher"}, "admin": _STAFF}


class DeviceBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    app: StrictStr = Field(max_length=16)
    platform: StrictStr = Field(max_length=16)
    token: StrictStr = Field(min_length=1, max_length=1024)


class TokenBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: StrictStr = Field(min_length=1, max_length=1024)


@router.put("/v1/me/devices", status_code=204)
def register_device(
    body: DeviceBody,
    request: Request,
    p: Principal = Depends(current_principal),  # noqa: B008
) -> None:
    if body.app not in APPS or body.platform not in PLATFORMS:
        raise AppError(422, "invalid_device", "Unknown app or platform")
    if not set(p.roles) & _APP_ROLES[body.app]:
        raise AppError(403, "forbidden", "Insufficient role")
    request.app.state.notifications.devices.register(p.user_id, body.app, body.platform, body.token)


@router.delete("/v1/me/devices", status_code=204)
def unregister_device(
    body: TokenBody,
    request: Request,
    p: Principal = Depends(current_principal),  # noqa: B008
) -> None:
    request.app.state.notifications.devices.unregister(p.user_id, body.token)  # idempotent


async def _read_body(request: Request) -> bytes:
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > MAX_WEBHOOK_BODY:
        raise AppError(413, "payload_too_large", "Request body too large")
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_WEBHOOK_BODY:
            raise AppError(413, "payload_too_large", "Request body too large")
        chunks.append(chunk)
    return b"".join(chunks)


@router.post("/v1/webhooks/resend")
async def resend_webhook(request: Request) -> dict:
    processor = request.app.state.email_webhooks
    body = await _read_body(request)
    try:
        out = processor.handle(request.headers, body)
    except WebhookRejected as e:
        log.warning("webhook rejected", extra={"reason": e.code})
        status = e.status if e.status in (400, 401, 413) else 400
        raise AppError(status, "webhook_rejected", "Webhook rejected") from None
    except NotImplementedError:
        raise AppError(503, "not_configured", "Email provider not configured") from None
    return {"ok": True, "duplicate": out.duplicate}
