"""Shared fakes for the M06 audit tests: stepping clock, crash injection after every write."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from hs_core.audit import AuditContext, AuditedRepository, AuditService
from hs_core.repository import InMemoryRepository

T0 = datetime(2027, 3, 1, 8, 0, tzinfo=UTC)


class StepClock:
    """Every call returns a later instant, so occurred_at is strictly increasing."""

    def __init__(self, start: datetime = T0, step_ms: int = 10) -> None:
        self.now = start
        self.step = timedelta(milliseconds=step_ms)

    def __call__(self) -> datetime:
        self.now += self.step
        return self.now

    def advance(self, **kw) -> None:
        self.now += timedelta(**kw)


class Crash(BaseException):
    """Simulated process death: nothing in the code under test may catch it."""


class FaultyRepository(InMemoryRepository):
    """Raises `Crash` or an ordinary error around the n-th WRITE (1-based). `when` is
    'before' (the write never happens) or 'after' (it happened, the caller never learns)."""

    def __init__(self) -> None:
        super().__init__()
        self.writes: list[tuple[str, str, str]] = []
        self.fail_at: int | None = None
        self.when = "after"
        self.error: BaseException = Crash()
        self.fail_tables: set[str] | None = None  # None = any table
        self.plan: dict[int, BaseException] = {}  # write index -> error (always 'before')

    def arm(self, n: int, when: str = "after", error: BaseException | None = None) -> None:
        self.writes.clear()
        self.fail_at, self.when = n, when
        self.error = error or Crash()

    def disarm(self) -> None:
        self.fail_at = None
        self.plan = {}

    def _write(self, op, table, row_id, call):
        self.writes.append((op, table, row_id))
        if len(self.writes) in self.plan:
            raise self.plan[len(self.writes)]
        hit = self.fail_at == len(self.writes) and (
            self.fail_tables is None or table in self.fail_tables
        )
        if hit and self.when == "before":
            raise self.error
        out = call()
        if hit and self.when == "after":
            raise self.error
        return out

    def create(self, table, row_id, data, permissions=None):
        return self._write(
            "create",
            table,
            row_id,
            lambda: super(FaultyRepository, self).create(table, row_id, data, permissions),
        )

    def update(self, table, row_id, data, permissions=None):
        return self._write(
            "update",
            table,
            row_id,
            lambda: super(FaultyRepository, self).update(table, row_id, data, permissions),
        )

    def delete(self, table, row_id):
        return self._write(
            "delete", table, row_id, lambda: super(FaultyRepository, self).delete(table, row_id)
        )


def admin_ctx(request_id: str = "req-1", actor: str = "u_admin", ip: str = "203.0.113.9"):
    return AuditContext.for_principal(actor, ["opsadmin"], request_id=request_id, ip=ip)


def user_ctx(request_id: str = "req-u", actor: str = "u_parent"):
    return AuditContext.for_principal(actor, ["parent"], request_id=request_id, ip="198.51.100.4")


def build(repo=None, clock=None, **service_kw):
    repo = repo if repo is not None else InMemoryRepository()
    clock = clock or StepClock()
    service = AuditService(repo, clock=clock, **service_kw)
    return repo, service, AuditedRepository(repo, service), clock


def audit_rows(repo, **where):
    rows = repo.list("audit_log")
    for k, v in where.items():
        rows = [r for r in rows if r.get(k) == v]
    return sorted(rows, key=lambda r: (r["occurred_at"], r["$id"]))
