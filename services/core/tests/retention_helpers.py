"""Test helpers for the retention package (C03): injected clocks, a recording repository with a
crash hook on EVERY write (create, update, delete), and small world builders.

Clocks are always injected: retention is about dates, so no test here reads the wall clock."""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta

from hs_core.repository import InMemoryRepository
from hs_core.retention.files import FakeRetentionFileStore
from hs_core.retention.policy import POLICY, effective_policy
from hs_core.retention.purger import PurgeConfig, RetentionPurger, new_stats

# 12:00 Kampala on a Monday (09:00 UTC): far from every date boundary
NOW = datetime(2027, 1, 18, 9, 0, tzinfo=UTC)


class Clock:
    def __init__(self, now: datetime = NOW) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kw) -> None:
        self.now += timedelta(**kw)


class Crash(BaseException):
    """A dead process: nothing after it runs (BaseException, so no `except Exception` hides it)."""


class RecRepo(InMemoryRepository):
    """InMemoryRepository that records every call and can crash before the N-th write."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[tuple] = []
        self.writes = 0
        self.crash_at: int | None = None
        self.on_write = None
        self._gate = threading.Lock()

    def _w(self, op, table, row_id):
        with self._gate:
            self.writes += 1
            n = self.writes
            self.calls.append((op, table, row_id))
        if self.on_write:
            self.on_write(op, table, row_id)
        if self.crash_at is not None and n == self.crash_at:
            raise Crash(f"crash before write {n}: {op} {table}/{row_id}")

    def create(self, table, row_id, data, permissions=None):
        self._w("create", table, row_id)
        return super().create(table, row_id, data, permissions)

    def update(self, table, row_id, data, permissions=None):
        self._w("update", table, row_id)
        return super().update(table, row_id, data, permissions)

    def delete(self, table, row_id):
        self._w("delete", table, row_id)
        return super().delete(table, row_id)

    def list(self, table, queries=None):
        self.calls.append(("list", table, queries))
        return super().list(table, queries)

    def deletes(self) -> list[tuple[str, str]]:
        return [(t, i) for op, t, i in self.calls if op == "delete"]

    def rows(self, table) -> dict[str, dict]:
        return {r["$id"]: r for r in InMemoryRepository.list(self, table)}


def iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat()


def ago(days: int, now: datetime = NOW) -> str:
    return iso(now - timedelta(days=days))


def make_purger(
    repo, *, enforce=True, quarantine_days=0, policy=None, files=None, clock=None, **cfg
):
    cfg.setdefault("blast_override", True)
    files = files if files is not None else FakeRetentionFileStore()
    return RetentionPurger(
        repo,
        files,
        policy or dict(POLICY),
        PurgeConfig(enforce=enforce, quarantine_days=quarantine_days, **cfg),
        clock or Clock(),
    )


def purge_table(purger, table, now=NOW, stats=None, max_writes=10_000):
    """Run one table to completion (loops over chunks like the job does)."""
    st = stats if stats is not None else new_stats()
    after = None
    for _ in range(100_000):
        chunk = purger.purge_chunk(table, now, after, st, max_writes)
        if chunk.done:
            return st
        after = chunk.after
    raise AssertionError("purge did not finish")


def policy_with(**periods):
    from hs_core.retention.periods import Period

    return effective_policy({t: Period.parse(p) for t, p in periods.items()})


def add_child(repo, cid="c1", parent="p1", **kw):
    repo.create(
        "child", cid, {"parent_id": parent, "name": "Ada", "date_of_birth": "2018-01-01", **kw}
    )


def add_log(repo, lid, child="c1", teacher="t1", submitted_at=None, **kw):
    row = {
        "session_id": f"s-{lid}",
        "child_id": child,
        "teacher_id": teacher,
        "submitted_at": submitted_at or ago(2000),
        "note": "n",
        "session_key": f"k-{lid}",
    }
    repo.create("session_log", lid, {**row, **kw})
