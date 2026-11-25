"""FastAPI glue: the audit context of a request, built from the VERIFIED principal.

Not imported by `hs_core.audit` itself (it pulls in the auth stack); endpoints import it:

    ctx: AuditContext = Depends(audit_context)
    request.app.state.repository.update("dispute", id, data, ctx=ctx, action="dispute.resolve",
                                        reason=body.reason)

The actor and roles come only from the Appwrite JWT (current_principal); the IP is the trusted
client IP that A01 uses (Fly-Client-IP outside dev); the request id is the one the middleware set.
Nothing here reads the request body.
"""

from __future__ import annotations

from fastapi import Depends, Request

from ..auth import Principal, current_principal
from ..auth_otp.api import client_ip
from ..logging_setup import request_id_var
from .context import AuditContext


def audit_context(
    request: Request,
    principal: Principal = Depends(current_principal),  # noqa: B008
) -> AuditContext:
    return AuditContext.for_principal(
        principal.user_id,
        principal.roles,
        request_id=request_id_var.get(),
        ip=client_ip(request),
    )
