"""Test helpers for the jobs package: fake clock, crash/stall injection, handler ledger."""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta

from hs_core.jobs.alerts import FakeAlertSink
from hs_core.jobs.model import Done, JobSpec
from hs_core.jobs.runner import Runner
from hs_core.repository import InMemoryRepository

T0 = datetime(2027, 1, 18, 9, 0, tzinfo=UTC)


class FakeClock:
    def __init__(self, now: datetime = T0) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kw) -> None:
        self.now += timedelta(**kw)


class Crash(BaseException):
    """Simulated worker death: propagates out of tick() like a killed process."""


class HookRepository(InMemoryRepository):
    """InMemoryRepository with a hook before every write and a log of every call."""

    def __init__(self) -> None:
        super().__init__()
        self.before = None  # callable(op, table, row_id, data) -> None
        self.calls: list[tuple] = []
        self._tl = threading.local()

    def _hook(self, op, table, row_id, data):
        self.calls.append((op, table, row_id, data))
        if self.before and not getattr(self._tl, "busy", False):
            self._tl.busy = True
            try:
                self.before(op, table, row_id, data)
            finally:
                self._tl.busy = False

    def create(self, table, row_id, data, permissions=None):
        self._hook("create", table, row_id, data)
        return super().create(table, row_id, data, permissions)

    def update(self, table, row_id, data, permissions=None):
        self._hook("update", table, row_id, data)
        return super().update(table, row_id, data, permissions)

    def list(self, table, queries=None):
        self.calls.append(("list", table, None, queries))
        return super().list(table, queries)

    def count(self, op, table=None):
        return sum(1 for c in self.calls if c[0] == op and (table is None or c[1] == table))


class Effects:
    """Models an idempotent side effect (ADR 0002 rule 7): applied once per effect key."""

    def __init__(self) -> None:
        self.invocations: list[str] = []
        self.applied: dict[str, int] = {}
        self._lock = threading.Lock()

    def handler(self, ctx):
        with self._lock:
            self.invocations.append(ctx.effect_key)
            self.applied.setdefault(ctx.effect_key, 0)
            if self.applied[ctx.effect_key] == 0:
                self.applied[ctx.effect_key] = 1
        return Done(items=1)


def make(handler=None, repo=None, clock=None, alert=None, **spec_kw):
    repo = repo if repo is not None else HookRepository()
    clock = clock or FakeClock()
    alert = alert or FakeAlertSink()
    spec = JobSpec("demo", handler or (lambda ctx: Done()), **spec_kw)
    runner = Runner(repo, [spec], alert=alert, clock=clock, failure_alert_after=3)
    return repo, runner, clock, alert


def runs(repo):
    rows = [r for r in repo.list("job_run") if not r["dedupe_key"].startswith("state:")]
    return sorted(rows, key=lambda r: (r["run_at"], r["dedupe_key"]))
