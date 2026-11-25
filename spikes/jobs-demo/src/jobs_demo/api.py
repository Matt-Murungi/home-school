"""Internal job endpoint called by the Appwrite Function on every tick.

Authentication: the function signs `"{timestamp}.{body}"` with HMAC-SHA256 using
JOB_TICK_SECRET (shared only between the function and the core service). The
core service rejects missing or wrong signatures and timestamps older than
5 minutes (replay protection). A user JWT is never accepted here.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import time
from dataclasses import asdict

from fastapi import FastAPI, Header, HTTPException, Request

from .runner import Runner

MAX_SKEW_SECONDS = 300


def sign(secret: str, timestamp: str, body: bytes) -> str:
    return hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()


def create_app(runner: Runner, secret: str | None = None) -> FastAPI:
    secret = secret or os.environ["JOB_TICK_SECRET"]
    app = FastAPI(title="jobs-demo")

    @app.post("/internal/jobs/tick")
    async def tick(request: Request,
                   x_job_timestamp: str = Header(default=""),
                   x_job_signature: str = Header(default="")) -> dict:
        body = await request.body()
        try:
            age = abs(time.time() - int(x_job_timestamp))
        except ValueError:
            raise HTTPException(401, "missing or invalid timestamp") from None
        if age > MAX_SKEW_SECONDS:
            raise HTTPException(401, "stale timestamp")
        if not hmac.compare_digest(sign(secret, x_job_timestamp, body), x_job_signature):
            raise HTTPException(401, "bad signature")
        return asdict(runner.tick())

    return app
