"""Named audit errors. The HTTP-facing ones are AppErrors so endpoints map them without glue."""

from __future__ import annotations

from ..errors import AppError


class AuditError(Exception):
    """Base class for audit problems."""


class AuditContextMissing(AuditError):
    """An audited table was written with no audit context (actor, request, job)."""


class AuditUnavailable(AuditError):
    """The audit row could not be written, so the change (or read) was not allowed to proceed."""


class UnauditedRead(AuditError):
    """Case rows were read without the read_audited helper."""


class ReasonRequired(AuditError, AppError):
    def __init__(self, action: str) -> None:
        AppError.__init__(self, 422, "reason_required", f"A reason is required for {action}")
        self.action = action


class AuditForbidden(AuditError, AppError):
    def __init__(self) -> None:
        AppError.__init__(self, 403, "forbidden", "Insufficient role")


class AuditQueryError(AuditError, AppError):
    def __init__(self, message: str) -> None:
        AppError.__init__(self, 400, "bad_audit_query", message)
