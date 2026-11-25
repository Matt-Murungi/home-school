"""POST /v1/auth/otp/request and /v1/auth/otp/verify (unauthenticated by nature).

The mobile apps call these and then exchange the returned custom token for a session with
`account.createSession(userId, secret)`. The apps never see the SMS provider key or the pepper.
The app (`parent` or `teacher`) comes from the request, but it only ever selects between the two
self-service labels: it is bound to the challenge when the code is requested and checked again on
verify, and no privileged label can be reached this way (see `accounts.SELF_SERVICE_ROLES`).
"""

from __future__ import annotations

import ipaddress

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, ConfigDict, Field, StrictStr

from ..errors import AppError

router = APIRouter(prefix="/v1/auth/otp", tags=["auth"])
MAX_BODY_BYTES = 8 * 1024


class RequestBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    phone: StrictStr = Field(max_length=64)
    app: StrictStr = Field(max_length=16)


class Device(BaseModel):
    model_config = ConfigDict(extra="ignore")
    platform: StrictStr | None = Field(default=None, max_length=16)
    app_version: StrictStr | None = Field(default=None, max_length=32)
    model: StrictStr | None = Field(default=None, max_length=64)


class VerifyBody(RequestBody):
    code: StrictStr = Field(max_length=16)
    device: Device | None = None


def client_ip(request: Request) -> str:
    """Fly sets `Fly-Client-IP` at its edge; it is trusted only when deployed behind Fly (a
    direct caller could spoof it otherwise). Anything that is not an IP address is ignored."""
    if request.app.state.trust_proxy_ip:
        raw = request.headers.get("fly-client-ip", "").strip()
        try:
            return str(ipaddress.ip_address(raw))
        except ValueError:
            pass
    return request.client.host if request.client else "unknown"


def _check_size(request: Request) -> None:
    declared = request.headers.get("content-length", "0")
    if declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        raise AppError(413, "payload_too_large", "Request body too large")


@router.post("/request")
def request_code(body: RequestBody, request: Request, response: Response) -> dict:
    _check_size(request)
    res = request.app.state.otp.request_code(body.phone, app=body.app, client_ip=client_ip(request))
    response.headers["Cache-Control"] = "no-store"
    return {
        "status": "sent",
        "resend_after_s": res.resend_after_s,
        "expires_in_s": res.expires_in_s,
    }


@router.post("/verify")
def verify_code(body: VerifyBody, request: Request, response: Response) -> dict:
    _check_size(request)
    device = body.device.model_dump(exclude_none=True) if body.device else {}
    res = request.app.state.otp.verify_code(
        body.phone, body.code, app=body.app, client_ip=client_ip(request), device=device
    )
    response.headers["Cache-Control"] = "no-store"
    return {
        "user_id": res.user_id,
        "secret": res.secret,
        "expire": res.expire.isoformat(),
        "is_new_user": res.is_new_user,
        "dormant": res.dormant,
    }
