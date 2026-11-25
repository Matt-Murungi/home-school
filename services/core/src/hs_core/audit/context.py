"""Who is acting, and from where (M06 #41).

The actor ALWAYS comes from the verified principal (the JWT/role context) or from the F04 job
context, never from a request body. A context is built once per request (or per job chunk) and
carries the counters that make audit rows idempotent: row key = (request or job key, entity,
entity id, n-th change of that entity in this context).
"""

from __future__ import annotations

import hashlib
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Literal

ROLES = ("parent", "guardian", "teacher", "opsadmin", "curator", "safeguardinglead", "superadmin")
ADMIN_ROLES = frozenset({"opsadmin", "curator", "safeguardinglead", "superadmin"})
Source = Literal["user", "admin", "system"]

current_audit_context: ContextVar[AuditContext | None] = ContextVar("audit_context", default=None)
# Set by the request middleware (trusted client IP, see api.py); read where a service has no
# Request in hand (A01 sign-up).
client_ip_var: ContextVar[str | None] = ContextVar("audit_client_ip", default=None)


# Set by the request middleware to a SERVER-generated id (uuid4) for every request. The audit
# dedupe key is derived from it, never from the client's X-Request-ID (which is only a
# correlation field): a client must not be able to choose a key that collides with another change.
server_request_key_var: ContextVar[str | None] = ContextVar("audit_server_key", default=None)


def short_id(value: str, size: int = 36) -> str:
    """Fit a free-form id into a varchar(size) column; long ids are replaced by their hash."""
    return value if len(value) <= size else hashlib.sha256(value.encode()).hexdigest()[:size]


@dataclass
class AuditContext:
    actor_id: str | None
    roles: tuple[str, ...]
    source: Source
    request_id: str
    key_base: str
    ip: str | None = None
    job: str | None = None
    _counts: dict[tuple[str, str], int] = field(default_factory=dict, repr=False, compare=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    @classmethod
    def for_principal(
        cls,
        actor_id: str,
        roles: list[str] | tuple[str, ...],
        *,
        request_id: str,
        ip: str | None = None,
    ) -> AuditContext:
        if not actor_id or not str(actor_id).strip():
            raise ValueError("an audit actor id is required")
        known = tuple(sorted({r for r in roles if r in ROLES}))
        source: Source = "admin" if set(known) & ADMIN_ROLES else "user"
        rid = request_id or "-"
        # outside a request (tests, scripts) the caller's id is the key; inside one it is the
        # server's id
        return cls(
            str(actor_id), known, source, short_id(rid), server_request_key_var.get() or rid, ip
        )

    @classmethod
    def system(cls, job: str, *, run_key: str, request_id: str | None = None) -> AuditContext:
        if not job or not job.strip():
            raise ValueError("a system audit context needs the job name")
        if not run_key:
            raise ValueError("a system audit context needs a run key")
        return cls(None, ("system",), "system", short_id(request_id or run_key), run_key, None, job)

    @classmethod
    def for_job(cls, job_ctx) -> AuditContext:
        """From the F04 JobContext: key = the chunk's effect key, so a retried chunk is deduped."""
        return cls.system(job_ctx.name, run_key=job_ctx.effect_key, request_id=job_ctx.run_id)

    @property
    def roles_text(self) -> str:
        return ",".join(self.roles)[:200]

    def count(self, entity: str, entity_id: str) -> int:
        with self._lock:
            return self._counts.get((entity, entity_id), 0)

    def advance_to(self, entity: str, entity_id: str, n: int) -> None:
        with self._lock:
            self._counts[(entity, entity_id)] = max(self._counts.get((entity, entity_id), 0), n)

    def key(self, entity: str, entity_id: str, n: int) -> str:
        return f"{self.key_base}:{entity}:{entity_id}:{n}"


@contextmanager
def audit_scope(ctx: AuditContext) -> Iterator[AuditContext]:
    """Make `ctx` the default context of AuditedRepository calls made inside the block."""
    previous = current_audit_context.set(ctx)
    try:
        yield ctx
    finally:
        current_audit_context.reset(previous)


@contextmanager
def job_scope(job_ctx) -> Iterator[AuditContext]:
    """Used by the F04 runner around every handler call: writes are logged as actor `system`."""
    with audit_scope(AuditContext.for_job(job_ctx)) as ctx:
        yield ctx
