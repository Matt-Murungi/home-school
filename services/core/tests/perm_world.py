"""Test world: the sample dataset plus a client view and fault injection."""

from __future__ import annotations

from hs_core.permissions.fake import PermissionAwareRepository, Principal
from hs_core.permissions.sample import H, SampleWorld, iso
from hs_core.repository import RepositoryUnavailable
from jobs_helpers import T0, Crash, FakeClock

__all__ = ["H", "T0", "FlakyRepository", "World", "iso"]


class FlakyRepository(PermissionAwareRepository):
    """Fails chosen writes: `fail(op, table, row_id) -> bool` (set by the test)."""

    fail = None
    crash = None  # kills the "process" (BaseException) like jobs_helpers.Crash

    def _maybe(self, op, table, row_id):
        if self.crash and self.crash(op, table, row_id):
            raise Crash()
        if self.fail and self.fail(op, table, row_id):
            raise RepositoryUnavailable(f"injected failure on {op} {table}/{row_id}")

    def create(self, table, row_id, data, permissions=None):
        self._maybe("create", table, row_id)
        return super().create(table, row_id, data, permissions)

    def update(self, table, row_id, data, permissions=None):
        self._maybe("update", table, row_id)
        return super().update(table, row_id, data, permissions)


class World(SampleWorld):
    def __init__(self, clock=None, repo_cls=FlakyRepository, **service_kw) -> None:
        super().__init__(clock or FakeClock(T0), repo_cls, **service_kw)

    def as_(self, uid: str | None, labels: list[str] | None = None):
        """Client view; labels default to the active role_assignment history of the user."""
        lab = self.labels(uid) if (labels is None and uid) else (labels or [])
        return self.repo.as_principal(Principal.of(uid, lab))
