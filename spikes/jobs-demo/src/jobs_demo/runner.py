"""Job runner for ADR 0002: Appwrite Functions tick, core service does the work.

Tables match the existing Appwrite schema in the Home School project:
- job_run:     name, dedupe_key, trigger, status, payload, attempts, max_attempts,
               run_at, scheduled_for, started_at, finished_at, locked_until,
               last_error, actor_id, reason
- job_attempt: run_id, attempt, started_at

Rules (each is covered by tests/test_runner.py):
1. Scheduled runs are materialised for *every* period that became due since the
   schedule started, so a missed tick is caught up, never lost.
2. A run's row ID is derived from its dedupe key, so the same (job, period) can
   only ever be created once, however many ticks overlap.
3. A run is claimed by creating a job_attempt row with a deterministic ID
   (run, chunk, attempt). Only one claimer can succeed; the others get Conflict.
4. A failed attempt is retried with exponential backoff until max_attempts,
   then marked failed and reported to the alert hook.
5. A run whose lease (locked_until) expires while "running" is treated as a
   failed attempt and becomes claimable again (worker crashed or timed out).
6. Long jobs return Continue(cursor): the run is re-queued immediately with the
   cursor in its payload, so each tick does one bounded chunk of work.
"""
from __future__ import annotations

import json
import time
import traceback
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Callable
from zoneinfo import ZoneInfo

from .store import Conflict, Store, row_id

KAMPALA = ZoneInfo("Africa/Kampala")
LEASE = timedelta(minutes=5)
TICK_BUDGET_SECONDS = 20.0


# ---------------------------------------------------------------- handlers
@dataclass(frozen=True)
class Done:
    detail: str = ""


@dataclass(frozen=True)
class Continue:
    """Chunk finished; more work remains. `cursor` is passed to the next chunk."""
    cursor: Any


@dataclass(frozen=True)
class JobContext:
    name: str
    run_id: str
    dedupe_key: str
    scheduled_for: datetime | None
    payload: dict[str, Any]
    cursor: Any
    chunk: int

    @property
    def effect_key(self) -> str:
        """Idempotency key for side effects of this chunk (e.g. a payout request)."""
        return f"{self.dedupe_key}#chunk{self.chunk}"


Handler = Callable[[JobContext], "Done | Continue"]


# ---------------------------------------------------------------- schedules
@dataclass(frozen=True)
class Schedule:
    """Every `every`, aligned to `anchor` (Kampala time). E.g. hourly, or daily at 02:00."""
    every: timedelta
    anchor: datetime = datetime(2026, 1, 1, tzinfo=KAMPALA)

    def due_periods(self, since: datetime, now: datetime) -> list[datetime]:
        """Period starts p with since <= p <= now."""
        step = self.every.total_seconds()
        first = self.anchor + self.every * max(0, -(-((since - self.anchor).total_seconds()) // step))
        out, p = [], first
        while p <= now:
            out.append(p.astimezone(UTC))
            p += self.every
        return out


@dataclass(frozen=True)
class JobSpec:
    name: str
    handler: Handler
    schedule: Schedule | None = None
    max_attempts: int = 5
    backoff_base: timedelta = timedelta(minutes=1)
    # How far back a freshly deployed schedule looks; bounds catch-up after long outages.
    catch_up: timedelta = timedelta(days=2)


# ---------------------------------------------------------------- runner
def _iso(dt: datetime | None) -> str | None:
    return dt.astimezone(UTC).isoformat() if dt else None


def _dt(s: str | None) -> datetime | None:
    return datetime.fromisoformat(s) if s else None


@dataclass
class TickResult:
    created: int = 0
    completed: list[str] = field(default_factory=list)
    continued: list[str] = field(default_factory=list)
    retried: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    lost_claims: int = 0


class Runner:
    def __init__(self, store: Store, jobs: list[JobSpec], alert: Callable[[dict], None] = lambda run: None):
        self.store = store
        self.jobs = {j.name: j for j in jobs}
        self.alert = alert

    # -- creating runs
    def enqueue(self, name: str, dedupe_key: str, *, run_at: datetime, payload: dict | None = None,
                trigger: str = "enqueue", scheduled_for: datetime | None = None,
                actor_id: str | None = None, reason: str | None = None) -> bool:
        """Create a run once per dedupe key. Returns False if it already existed."""
        spec = self.jobs[name]
        try:
            self.store.create_row("job_run", row_id("job_run", dedupe_key), {
                "name": name, "dedupe_key": dedupe_key, "trigger": trigger, "status": "queued",
                "payload": json.dumps(payload or {}), "attempts": 0, "max_attempts": spec.max_attempts,
                "run_at": _iso(run_at), "scheduled_for": _iso(scheduled_for), "started_at": None,
                "finished_at": None, "locked_until": None, "last_error": None,
                "actor_id": actor_id, "reason": reason,
            })
            return True
        except Conflict:
            return False

    def materialise(self, now: datetime) -> int:
        created = 0
        for spec in self.jobs.values():
            if not spec.schedule:
                continue
            # Look back at least one interval so a late tick never skips the current period.
            window = max(spec.catch_up, spec.schedule.every)
            for period in spec.schedule.due_periods(now - window, now):
                key = f"{spec.name}:{period.isoformat()}"
                created += self.enqueue(spec.name, key, run_at=period, trigger="schedule", scheduled_for=period)
        return created

    # -- claiming and running
    def _claimable(self, now: datetime) -> list[dict]:
        rows = []
        for r in self.store.list_rows("job_run"):
            if r["status"] == "queued" and _dt(r["run_at"]) <= now:
                rows.append(r)
            elif r["status"] == "running" and _dt(r["locked_until"]) and _dt(r["locked_until"]) < now:
                rows.append(r)  # lease expired: crashed or timed-out worker
        return sorted(rows, key=lambda r: r["run_at"])

    def _claim(self, run: dict, now: datetime) -> dict | None:
        payload = json.loads(run["payload"] or "{}")
        chunk = payload.get("_chunk", 0)
        attempt = run["attempts"] + 1
        try:
            self.store.create_row("job_attempt", row_id("job_attempt", run["$id"], chunk, attempt), {
                "run_id": run["$id"], "attempt": attempt, "started_at": _iso(now),
            })
        except Conflict:
            return None  # another tick claimed this exact attempt first
        expired = run["status"] == "running"
        return self.store.update_row("job_run", run["$id"], {
            "status": "running", "attempts": attempt, "locked_until": _iso(now + LEASE),
            "started_at": run["started_at"] or _iso(now),
            "last_error": "lease expired before the previous attempt finished" if expired else run["last_error"],
        })

    def _fail(self, run: dict, now: datetime, error: str, result: TickResult) -> None:
        spec = self.jobs[run["name"]]
        if run["attempts"] >= run["max_attempts"]:
            run = self.store.update_row("job_run", run["$id"], {
                "status": "failed", "finished_at": _iso(now), "locked_until": None, "last_error": error[:500]})
            result.failed.append(run["dedupe_key"])
            self.alert(run)
        else:
            delay = spec.backoff_base * (2 ** (run["attempts"] - 1))
            self.store.update_row("job_run", run["$id"], {
                "status": "queued", "run_at": _iso(now + delay), "locked_until": None, "last_error": error[:500]})
            result.retried.append(run["dedupe_key"])

    def tick(self, now: datetime | None = None, budget_s: float = TICK_BUDGET_SECONDS) -> TickResult:
        now = now or datetime.now(UTC)
        deadline = time.monotonic() + budget_s
        result = TickResult(created=self.materialise(now))
        for candidate in self._claimable(now):
            if time.monotonic() > deadline:
                break  # leave the rest for the next tick
            if candidate["status"] == "running":
                # Expired lease counts as a failed attempt before re-claiming.
                if candidate["attempts"] >= candidate["max_attempts"]:
                    self._fail(candidate, now, "lease expired on final attempt", result)
                    continue
            run = self._claim(candidate, now)
            if run is None:
                result.lost_claims += 1
                continue
            payload = json.loads(run["payload"] or "{}")
            ctx = JobContext(run["name"], run["$id"], run["dedupe_key"], _dt(run["scheduled_for"]),
                             {k: v for k, v in payload.items() if not k.startswith("_")},
                             payload.get("_cursor"), payload.get("_chunk", 0))
            try:
                outcome = self.jobs[run["name"]].handler(ctx)
            except Exception as exc:  # noqa: BLE001 — any handler error is a failed attempt
                self._fail(run, now, f"{type(exc).__name__}: {exc}\n{traceback.format_exc(limit=3)}", result)
                continue
            if isinstance(outcome, Continue):
                payload.update({"_cursor": outcome.cursor, "_chunk": ctx.chunk + 1})
                self.store.update_row("job_run", run["$id"], {
                    "status": "queued", "run_at": _iso(now), "payload": json.dumps(payload),
                    "attempts": 0, "locked_until": None})
                result.continued.append(run["dedupe_key"])
            else:
                self.store.update_row("job_run", run["$id"], {
                    "status": "completed", "finished_at": _iso(now), "locked_until": None})
                result.completed.append(run["dedupe_key"])
        return result
