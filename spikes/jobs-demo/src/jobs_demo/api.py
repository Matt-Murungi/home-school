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
from fastapi.concurrency import run_in_threadpool

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
        # Compare as bytes: str compare_digest raises TypeError on non-ASCII input (would be a 500).
        if not hmac.compare_digest(sign(secret, x_job_timestamp, body).encode(), x_job_signature.encode()):
            raise HTTPException(401, "bad signature")
        # tick() is synchronous (storage calls block); keep it off the event loop.
        return asdict(await run_in_threadpool(runner.tick))

    return app
