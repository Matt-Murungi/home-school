"""`retention-purge` (F04 job, daily 03:30 Kampala). Registered in jobs/schedule.py.

F04 handlers receive only a JobContext, so the job is bound once at startup (`bind`, the same idiom
as the permission and OTP jobs); an unbound job fails loudly instead of silently doing nothing.
"""

from __future__ import annotations

import threading

from ..jobs.model import Continue, Done, JobContext
from .run import RetentionJob

_lock = threading.Lock()
_job: RetentionJob | None = None


def bind(job: RetentionJob | None) -> None:
    global _job
    with _lock:
        _job = job


def retention_purge(ctx: JobContext) -> Done | Continue:
    with _lock:
        job = _job
    if job is None:
        raise RuntimeError("retention-purge is not bound (call retention.jobs.bind)")
    return job(ctx)
