"""S01 test helpers: a repository with Appwrite-style unique indexes (read from the real schema), a
deterministic
world builder (teacher, availability, plan, subscription, assignment, home) and a session-set
snapshot."""

from __future__ import annotations

import itertools
import json
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

from hs_core.repository import Conflict, InMemoryRepository
from hs_core.scheduling.rules import SLOT_RELEASED, overlaps
from jobs_helpers import FakeClock, HookRepository

SCHEMA_DIR = Path(__file__).resolve().parents[3] / "appwrite" / "schema"


def unique_indexes() -> dict[str, list[list[str]]]:
    """{table: [[col, ...], ...]} for every unique index in the schema domain files (single source
    of
    truth)."""
    out: dict[str, list[list[str]]] = {}
    for f in sorted(SCHEMA_DIR.glob("*.json")):
        for t in json.loads(f.read_text())["tables"]:
            for i in t["indexes"]:
                if i["type"] == "unique":
                    out.setdefault(t["$id"], []).append(i["columns"])
    return out


UNIQUE = unique_indexes()


class UniqueRepo(HookRepository):
    """InMemoryRepository plus what Appwrite enforces: unique indexes (NULLs never collide, 409 ->
    Conflict) and
    `$createdAt` (monotonic, so the 'later-created' tie-break is deterministic)."""

    def __init__(self) -> None:
        super().__init__()
        self._seq = itertools.count(1)
        self._ulock = threading.RLock()

    def _check(self, table, row_id, data):
        for cols in UNIQUE.get(table, []):
            vals = [data.get(c) for c in cols]
            if any(v is None for v in vals):
                continue
            for other in self._t.get(table, {}).values():
                if other["$id"] != row_id and [other.get(c) for c in cols] == vals:
                    raise Conflict(f"unique {table}.{cols}")

    def create(self, table, row_id, data, permissions=None):
        self._hook("create", table, row_id, data)
        with self._ulock, self._lock:
            self._check(table, row_id, data)
            data = {**data, "$createdAt": f"{next(self._seq):012d}"}
            return InMemoryRepository.create(self, table, row_id, data, permissions)

    def update(self, table, row_id, data, permissions=None):
        self._hook("update", table, row_id, data)
        with self._ulock, self._lock:
            row = self._t.get(table, {}).get(row_id)
            if row is not None:
                self._check(table, row_id, {**row, **data})
            return InMemoryRepository.update(self, table, row_id, data, permissions)


def iso(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.000+00:00")


# 2026-12-01 is a Tuesday. NOW is Monday 2026-11-30 09:00 EAT = 06:00 UTC.
NOW = datetime(2026, 11, 30, 6, 0, tzinfo=UTC)


class World:
    """One teacher, one child, one plan; `assignment()` adds pairings. All data lives in `repo`."""

    def __init__(self, repo=None, now: datetime = NOW) -> None:
        self.repo = repo or UniqueRepo()
        self.clock = FakeClock(now)
        self._n = itertools.count(1)
        self.plans: dict[int, str] = {}
        self.teachers: dict[str, str] = {}

    def _id(self, prefix: str) -> str:
        return f"{prefix}{next(self._n)}"

    def teacher(self, tid: str | None = None, status: str = "active", availability=None) -> str:
        tid = tid or self._id("t")
        self.repo.create("teacher_profile", tid, {"user_id": f"u-{tid}", "status": status})
        # default availability: every day 06:00-22:00 Kampala time
        for wd, s, e in (
            availability if availability is not None else [(d, 360, 1320) for d in range(1, 8)]
        ):
            self.repo.create(
                "availability_slot",
                self._id("av"),
                {"teacher_id": tid, "weekday": wd, "start_minute": s, "end_minute": e},
            )
        return tid

    def leave(self, tid: str, starts: datetime, ends: datetime) -> str:
        rid = self._id("lv")
        self.repo.create(
            "availability_exception",
            rid,
            {"teacher_id": tid, "starts_at": iso(starts), "ends_at": iso(ends)},
        )
        return rid

    def plan(self, per_week: int) -> str:
        if per_week not in self.plans:
            pid = self._id("p")
            self.repo.create(
                "plan",
                pid,
                {"curriculum_id": "cur", "sessions_per_week": per_week, "price_ugx": 100000},
            )
            self.plans[per_week] = pid
        return self.plans[per_week]

    def home(self, parent: str, default: bool = True) -> str:
        hid = self._id("h")
        self.repo.create(
            "home_location",
            hid,
            {
                "parent_id": parent,
                "is_default": default,
                "default_key": parent if default else None,
            },
        )
        return hid

    def assignment(
        self,
        teacher: str,
        slots,
        per_week: int | None = None,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        trial: bool = False,
        status: str = "active",
        parent: str | None = None,
        home: bool = True,
    ) -> str:
        parent = parent or self._id("par")
        child = self._id("c")
        sub = self._id("s")
        aid = self._id("a")
        if home:
            self.home(parent)
        self.repo.create(
            "subscription",
            sub,
            {
                "child_id": child,
                "parent_id": parent,
                "plan_id": self.plan(per_week or len(slots)),
                "status": status,
                "preferred_slots": json.dumps(
                    [{"weekday": w, "start_minute": s, "end_minute": e} for w, s, e in slots]
                ),
            },
        )
        self.repo.create(
            "assignment",
            aid,
            {
                "child_id": child,
                "teacher_id": teacher,
                "subscription_id": sub,
                "start_at": iso(start or NOW - timedelta(days=7)),
                "end_at": iso(end) if end else None,
                "is_trial": trial,
                "active_key": f"{child}:{sub}",
            },
        )
        return aid

    def sessions(self, **where):
        rows = self.repo.list("session")
        return [r for r in rows if all(r.get(k) == v for k, v in where.items())]

    def issues(self, **where):
        rows = self.repo.list("scheduling_issue")
        return [r for r in rows if all(r.get(k) == v for k, v in where.items())]


def snapshot(
    repo,
    table="session",
    fields=(
        "assignment_id",
        "slot_ref",
        "scheduled_start_at",
        "scheduled_end_at",
        "status",
        "is_trial",
        "teacher_id",
    ),
):
    """Order-independent comparable view of live rows."""
    rows = [r for r in repo.list(table) if not r.get("deleted_at")]
    return sorted(tuple(str(r.get(f)) for f in fields) for r in rows)


def live_held(repo, teacher_id):
    return [
        r
        for r in repo.list("session")
        if r["teacher_id"] == teacher_id
        and not r.get("deleted_at")
        and r["status"] not in SLOT_RELEASED
    ]


def overlapping_pairs(repo, teacher_id):
    rows = live_held(repo, teacher_id)
    return [
        (a["$id"], b["$id"])
        for i, a in enumerate(rows)
        for b in rows[i + 1 :]
        if overlaps(
            a["scheduled_start_at"],
            a["scheduled_end_at"],
            b["scheduled_start_at"],
            b["scheduled_end_at"],
        )
    ]
