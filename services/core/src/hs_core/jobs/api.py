"""POST /internal/jobs/tick: called by the Appwrite Function job-tick once a minute.

The credential is an HMAC over `{timestamp}.{body}` (JOB_TICK_SECRET). A user JWT is never read
or accepted here. Every failure returns the same 401 so a caller learns nothing about why.
"""

from __future__ import annotations

import time
from dataclasses import asdict

from fastapi import APIRouter, Request
from fastapi.concurrency import run_in_threadpool

from ..errors import AppError
from .signing import verify

router = APIRouter()
MAX_BODY_BYTES = 64 * 1024


@router.post("/internal/jobs/tick", include_in_schema=False)
async def tick(request: Request) -> dict:
    tick_key = request.app.state.settings.job_tick_secret.get_secret_value()
    if not tick_key:
        raise AppError(503, "job_tick_disabled", "Job tick is not configured")
    declared = request.headers.get("content-length", "0")
    if declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        raise AppError(413, "payload_too_large", "Request body too large")
    body = await request.body()
    if len(body) > MAX_BODY_BYTES:
        raise AppError(413, "payload_too_large", "Request body too large")
    ok = verify(
        tick_key,
        request.headers.get("x-job-timestamp", ""),
        request.headers.get("x-job-signature", ""),
        body,
        time.time(),
    )
    if not ok:
        raise AppError(401, "unauthenticated", "Invalid job credential")
    # tick() blocks on storage calls: keep it off the event loop.
    return asdict(await run_in_threadpool(request.app.state.runner.tick))
