"""AuditedRepository: the choke point for writes to audited tables (M06 #41).

It implements the `Repository` protocol, so it can stand wherever a repository is passed. For
tables outside AUDITED_TABLES it is a plain pass-through. For an audited table every create,
update and delete needs an AuditContext (explicit `ctx=`, `bind(ctx)`, or the context a job or
`audit_scope` set) and goes through AuditService.run_change (audit-first). `audit_log` itself is
refused: only AuditService writes it, and only through AppendOnlyRepository.

Limit (docs/ops/audit.md "Tripwires"): code that holds the raw repository can still write; the
app wires only this wrapper into app.state.repository, and an AST tripwire flags literal writes
elsewhere. That is protection by wiring and convention, not enforcement.
"""

from __future__ import annotations

from typing import Any

from ..append_only import APPEND_ONLY_TABLES, AppendOnlyViolation
from ..repository import Conflict, NotFound, Repository
from . import payload, registry
from .context import AuditContext, current_audit_context
from .errors import AuditContextMissing, UnauditedRead
from .service import TABLE, AuditService


class AuditedRepository:
    def __init__(
        self,
        inner: Repository,
        service: AuditService,
        *,
        ctx: AuditContext | None = None,
    ) -> None:
        self._inner = inner
        self._svc = service
        self._ctx = ctx

    @property
    def service(self) -> AuditService:
        return self._svc

    def effective_context(self) -> AuditContext | None:
        """The context an audited write made now would use (bound, else the ambient scope)."""
        return self._ctx or current_audit_context.get()

    def bind(self, ctx: AuditContext) -> AuditedRepository:
        """A view of the same repository whose audited writes use `ctx`."""
        return AuditedRepository(self._inner, self._svc, ctx=ctx)

    # -------------------------------------------------------------- pass-through
    def ping(self) -> None:
        self._inner.ping()

    def get(self, table: str, row_id: str) -> dict[str, Any]:
        if table in registry.CASE_TABLES:
            raise UnauditedRead(f"{table} rows are read with read_audited()")
        return self._inner.get(table, row_id)

    def list(self, table: str, queries: list[str] | None = None) -> list[dict[str, Any]]:
        if table in registry.CASE_TABLES:
            raise UnauditedRead(f"{table} rows are listed with list_audited()")
        return self._inner.list(table, queries)

    # -------------------------------------------------------------- audited writes
    def _audited(self, table: str, op: str, ctx: AuditContext | None) -> AuditContext | None:
        """None = not an audited table (pass through); otherwise the context to use."""
        if table == TABLE:
            raise AppendOnlyViolation("audit_log is written by AuditService only")
        if table not in registry.AUDITED_TABLES:
            return None
        if op in ("update", "delete") and table in APPEND_ONLY_TABLES:
            raise AppendOnlyViolation(f"{table} is append-only: {op} is not allowed")
        found = ctx or self._ctx or current_audit_context.get()
        if found is None:
            raise AuditContextMissing(f"{op} on {table} needs an audit context")
        return found

    def create(
        self,
        table: str,
        row_id: str,
        data: dict,
        permissions: list[str] | None = None,
        *,
        ctx: AuditContext | None = None,
        action: str | None = None,
        reason: str | None = None,
    ) -> dict[str, Any]:
        use = self._audited(table, "create", ctx)
        if use is None:
            return self._inner.create(table, row_id, data, permissions)

        def replay(exc: Exception) -> dict[str, Any]:
            if isinstance(exc, Conflict):  # an earlier attempt of this request already created it
                return self._inner.get(table, row_id)
            raise exc

        after = {**data, "$permissions": permissions or []}
        return self._svc.run_change(
            use, action=action or f"{table}.create", entity=table, entity_id=row_id, op="create",
            before=None, after=after, reason=reason,
            apply=lambda: self._inner.create(table, row_id, data, permissions), on_replay=replay,
        )  # fmt: skip

    def update(
        self,
        table: str,
        row_id: str,
        data: dict,
        permissions: list[str] | None = None,
        *,
        ctx: AuditContext | None = None,
        action: str | None = None,
        reason: str | None = None,
    ) -> dict[str, Any]:
        use = self._audited(table, "update", ctx)
        if use is None:
            return self._inner.update(table, row_id, data, permissions)
        cache: dict[str, dict] = {}

        def before() -> dict:
            if "b" not in cache:
                cache["b"] = self._inner.get(table, row_id)  # NotFound: nothing to change
            return cache["b"]

        def after() -> dict:
            merged = {**before(), **data}
            if permissions is not None:
                merged["$permissions"] = permissions
            return merged

        return self._svc.run_change(
            use, action=action or f"{table}.update", entity=table, entity_id=row_id, op="update",
            before=before, after=after, reason=reason,
            apply=lambda: self._inner.update(table, row_id, data, permissions),
        )  # fmt: skip

    def delete(
        self,
        table: str,
        row_id: str,
        *,
        ctx: AuditContext | None = None,
        action: str | None = None,
        reason: str | None = None,
    ) -> None:
        use = self._audited(table, "delete", ctx)
        if use is None:
            return self._inner.delete(table, row_id)

        def replay(exc: Exception) -> None:
            if isinstance(exc, NotFound):  # an earlier attempt of this request already deleted it
                return None
            raise exc

        self._svc.run_change(
            use, action=action or f"{table}.delete", entity=table, entity_id=row_id, op="delete",
            before=lambda: self._inner.get(table, row_id), after=None, reason=reason,
            apply=lambda: self._inner.delete(table, row_id), on_replay=replay,
        )  # fmt: skip

    # -------------------------------------------------------------- used by reads.py only
    def _raw_get(self, table: str, row_id: str) -> dict[str, Any]:
        return self._inner.get(table, row_id)

    def _raw_list(self, table: str, queries: list[str] | None = None) -> list[dict[str, Any]]:
        return self._inner.list(table, queries)


__all__ = ["AuditedRepository", "payload"]
