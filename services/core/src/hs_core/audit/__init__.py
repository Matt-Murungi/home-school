"""Audit logging (M06 #41, SEC-6, ADM-6). Read docs/ops/audit.md first.

Public surface:
- `AuditedRepository` (repository.py): the choke point; wrap the repository, pass `ctx=`.
- `AuditService.run_change` / `record`: audit-first writes and single rows for non-table actions.
- `read_audited` / `list_audited`: how case endpoints must read safeguarding rows (a
  convention checked by a tripwire, see tripwire.py).
- `list_audit`: cursor-paginated, role-checked read side (for H08).
- `recover.audit_recover`: the F04 job that settles crashed changes.
`hs_core.audit.api.audit_context` (FastAPI dependency) is imported explicitly, not from here.
"""

from __future__ import annotations

from typing import Any

from .context import (
    AuditContext,
    audit_scope,
    client_ip_var,
    current_audit_context,
    job_scope,
)
from .errors import (
    AuditContextMissing,
    AuditError,
    AuditForbidden,
    AuditQueryError,
    AuditUnavailable,
    NotWritten,
    ReasonRequired,
    UnauditedRead,
)
from .query import AuditPage, can_read_audit, list_audit
from .reads import list_audited, read_audited
from .repository import AuditedRepository
from .service import AuditService

__all__ = [
    "AuditContext", "AuditContextMissing", "AuditError", "AuditForbidden", "AuditPage",
    "AuditQueryError", "AuditService", "AuditUnavailable", "AuditedRepository", "NotWritten",
    "ReasonRequired",
    "UnauditedRead", "audit_scope", "bind_actor", "can_read_audit", "client_ip_var",
    "current_audit_context", "job_scope", "list_audit", "list_audited", "read_audited",
]  # fmt: skip


def bind_actor(
    repo: Any, actor_id: str, roles: list[str], *, request_id: str, ip: str | None = None
):
    """A repository whose audited writes are attributed to `actor_id` (e.g. a user signing up).
    Returns `repo` unchanged when it is not an AuditedRepository (unit tests with a bare fake)."""
    if isinstance(repo, AuditedRepository):
        return repo.bind(AuditContext.for_principal(actor_id, roles, request_id=request_id, ip=ip))
    return repo
