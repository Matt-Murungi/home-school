"""Job runner for ADR 0002. Storage is the F03 repository (Appwrite TablesDB or the fake).

Tables (job_run, job_attempt exist in the live project; F02 owns the schema build, re-verify):
- job_run:     name, dedupe_key, trigger, status, payload, attempts, max_attempts, run_at,
               scheduled_for, started_at, finished_at, locked_until, last_error, actor_id, reason
               index `by_status_run_at` (status, run_at).
- job_attempt: run_id, chunk, attempt, generation, started_at, outcome, finished_at, error,
               items, result   (the last six are the ADR 0002 follow-up columns)

Rules, each covered by tests/test_jobs_runner.py:
1. Due scheduled periods are materialised from a persisted watermark, one run per (job, period).
2. A run's row ID is derived from its dedupe key (Appwrite refuses duplicate IDs).
3. A worker claims an attempt by creating the job_attempt row with a deterministic ID; only one
   creator wins (Conflict for the rest). An orphaned claim older than the lease is taken over with
   the next generation ID (again an atomic create).
4. A claimer verifies it still holds the claim before and after the job_run update (its lease has
   not run out and no later generation exists). A claimer that stalled past the lease stands down.
5. The attempt row is the source of truth for the outcome. It is written before job_run is
   updated, so a crash in between is reconciled from it and the handler is not re-run.
6. Failed attempts retry with capped exponential backoff, then fail and alert. A handler that
   exceeds its timeout is a failed attempt. Consecutive failed attempts alert once per streak.
7. Expired lease: a failed attempt (final attempt: failed + alert, handler not re-run).
8. Long jobs return Continue(cursor); the run is re-queued with the cursor in its payload.
"""

from __future__ import annotations

import contextvars
import json
import logging
import threading
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from appwrite.query import Query

from ..repository import Conflict, NotFound, Repository
from .alerts import Alert, AlertSink, LogAlertSink
from .idempotency import row_id, run_row_id
from .model import Continue, Done, JobContext, JobSpec

log = logging.getLogger("hs_core.jobs")

LEASE = timedelta(minutes=5)
LEASE_SAFETY = timedelta(seconds=30)  # a claimer stands down this long before its lease ends
TICK_BUDGET_SECONDS = 15.0  # no new run starts after this; + job timeout stays under 25 s
MAX_BACKOFF = timedelta(hours=1)
_TERMINAL = ("completed", "failed")

__all__ = ["LEASE", "Runner", "TickResult", "row_id"]


def _iso(dt: datetime | None) -> str | None:
    return dt.astimezone(UTC).isoformat() if dt else None


def _dt(s: str | None) -> datetime | None:
    return datetime.fromisoformat(s) if s else None


def _now() -> datetime:
    return datetime.now(UTC)


class JobTimeout(Exception):
    pass


@dataclass
class TickResult:
    created: int = 0
    completed: list[str] = field(default_factory=list)
    continued: list[str] = field(default_factory=list)
    retried: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    reconciled: list[str] = field(default_factory=list)
    lost_claims: int = 0
    unknown_jobs: int = 0


@dataclass
class _Claim:
    run: dict
    attempt: int
    generation: int
    chunk: int
    started: datetime
    attempt_id: str


class Runner:
    def __init__(
        self,
        repo: Repository,
        jobs: list[JobSpec],
        alert: AlertSink | None = None,
        clock: Callable[[], datetime] = _now,
        failure_alert_after: int = 3,
        claim_batch: int = 50,
        lease: timedelta = LEASE,
    ) -> None:
        self.repo = repo
        self.jobs = {j.name: j for j in jobs}
        self.alert = alert or LogAlertSink()
        self.clock = clock
        self.failure_alert_after = failure_alert_after
        self.claim_batch = claim_batch
        self.lease = lease

    # ------------------------------------------------------------ creating runs
    def enqueue(
        self,
        name: str,
        dedupe_key: str,
        *,
        run_at: datetime,
        payload: dict | None = None,
        trigger: str = "enqueue",
        scheduled_for: datetime | None = None,
        actor_id: str | None = None,
        reason: str | None = None,
    ) -> bool:
        """Create a run once per dedupe key. Returns False if it already existed."""
        spec = self.jobs[name]
        try:
            self.repo.create(
                "job_run",
                run_row_id(dedupe_key),
                {
                    "name": name,
                    "dedupe_key": dedupe_key,
                    "trigger": trigger,
                    "status": "queued",
                    "payload": json.dumps(payload or {}),
                    "attempts": 0,
                    "max_attempts": spec.max_attempts,
                    "run_at": _iso(run_at),
                    "scheduled_for": _iso(scheduled_for),
                    "started_at": None,
                    "finished_at": None,
                    "locked_until": None,
                    "last_error": None,
                    "actor_id": actor_id,
                    "reason": reason,
                },
            )
            return True
        except Conflict:
            return False

    # Per-job state (watermark, failure streak) lives in one non-claimable job_run row per job.
    def _state(self, name: str) -> tuple[dict, dict]:
        rid = row_id("job_state", name)
        try:
            row = self.repo.get("job_run", rid)
        except NotFound:
            data = {
                "name": name,
                "dedupe_key": f"state:{name}",
                "trigger": "schedule",
                "status": "skipped",  # never claimable
                "payload": "{}",
                "attempts": 0,
                "max_attempts": 0,
                "run_at": _iso(self.clock()),
                "reason": "per-job state: materialisation watermark and failure streak",
            }
            try:
                row = self.repo.create("job_run", rid, data)
            except Conflict:
                row = self.repo.get("job_run", rid)
        return row, json.loads(row.get("payload") or "{}")

    def _save_state(self, row: dict, state: dict) -> None:
        self.repo.update("job_run", row["$id"], {"payload": json.dumps(state)})

    def materialise(self, now: datetime) -> int:
        """Create runs for periods that became due since the watermark (bounded by catch_up)."""
        created = 0
        for spec in self.jobs.values():
            if not spec.schedule:
                continue
            row, state = self._state(spec.name)
            window = max(spec.catch_up, spec.schedule.every)  # never skip the current period
            since = now - window
            through = _dt(state.get("through"))
            if through:
                since = max(since, through + timedelta(microseconds=1))
            periods = spec.schedule.due_periods(since, now)
            for period in periods:
                key = f"{spec.name}:{period.isoformat()}"
                created += self.enqueue(
                    spec.name, key, run_at=period, trigger="schedule", scheduled_for=period
                )
            if periods:
                state["through"] = _iso(periods[-1])
                self._save_state(row, state)
        return created

    # ------------------------------------------------------------ claiming
    def _claimable(self, now: datetime) -> list[dict]:
        """status + run_at on the by_status_run_at index, ordered, limited."""
        base = [Query.less_than_equal("run_at", _iso(now)), Query.order_asc("run_at")]
        limit = Query.limit(self.claim_batch)
        queued = self.repo.list("job_run", [Query.equal("status", ["queued"]), *base, limit])
        running = self.repo.list("job_run", [Query.equal("status", ["running"]), *base, limit])
        expired = [r for r in running if (_dt(r.get("locked_until")) or now) < now]
        return sorted(queued + expired, key=lambda r: r["run_at"])[: self.claim_batch]

    def _attempt_id(self, run_id: str, chunk: int, attempt: int, generation: int) -> str:
        parts = ("job_attempt", run_id, chunk, attempt) + ((generation,) if generation else ())
        return row_id(*parts)

    def _attempt_exists(self, attempt_id: str) -> dict | None:
        try:
            return self.repo.get("job_attempt", attempt_id)
        except NotFound:
            return None

    def _latest_attempt(self, run_id: str, chunk: int, attempt: int) -> dict | None:
        latest, gen = None, 0
        while row := self._attempt_exists(self._attempt_id(run_id, chunk, attempt, gen)):
            latest, gen = row, gen + 1
        return latest

    def _holds(self, claim: _Claim) -> bool:
        """Still the owner: lease not (nearly) over and no later generation has taken over."""
        if self.clock() - claim.started >= self.lease - LEASE_SAFETY:
            return False
        nxt = self._attempt_id(claim.run["$id"], claim.chunk, claim.attempt, claim.generation + 1)
        return self._attempt_exists(nxt) is None

    def _claim(self, run: dict) -> _Claim | None:
        payload = json.loads(run["payload"] or "{}")
        chunk, attempt, gen = payload.get("_chunk", 0), run["attempts"] + 1, 0
        while True:
            aid = self._attempt_id(run["$id"], chunk, attempt, gen)
            started = self.clock()
            try:
                self.repo.create(
                    "job_attempt",
                    aid,
                    {
                        "run_id": run["$id"],
                        "chunk": chunk,
                        "attempt": attempt,
                        "generation": gen,
                        "started_at": _iso(started),
                        "outcome": None,
                        "finished_at": None,
                        "error": None,
                        "items": 0,
                        "result": None,
                    },
                )
                break
            except Conflict:
                existing = self._attempt_exists(aid)
                if existing is None:
                    return None
                if existing.get("outcome"):  # finished earlier; run row just never caught up
                    self._reconcile(run["$id"], existing, TickResult())
                    return None
                begun = _dt(existing.get("started_at"))
                if begun is None or self.clock() - begun < self.lease:
                    return None  # a live claimer holds it
                gen += 1  # orphaned claim: take over with the next generation
        claim = _Claim(run, attempt, gen, chunk, started, aid)
        if not self._holds(claim):
            return None
        expired = run["status"] == "running"
        updated = self.repo.update(
            "job_run",
            run["$id"],
            {
                "status": "running",
                "attempts": attempt,
                "locked_until": _iso(self.clock() + self.lease),
                "started_at": run.get("started_at") or _iso(started),
                "last_error": "lease expired before the previous attempt finished"
                if expired
                else run.get("last_error"),
            },
        )
        # The update may have stalled past our lease. Verify the claim again; if a taker won,
        # stand down (its outcome, once recorded, is reconciled into the run row).
        if not self._holds(claim):
            latest = self._latest_attempt(run["$id"], chunk, attempt)
            if latest and latest.get("outcome"):
                self._reconcile(run["$id"], latest, TickResult())
            return None
        claim.run = updated
        return claim

    # ------------------------------------------------------------ outcomes
    def _send_alert(self, alert: Alert) -> None:
        try:
            self.alert.send(alert)
        except Exception:  # noqa: BLE001 - alerting must never break the runner
            log.error("alert sink failed", exc_info=True, extra={"job": alert.job})

    def _note_attempt(self, run: dict, ok: bool, error: str = "") -> None:
        """Count consecutive failed attempts per job; alert once when the streak reaches N."""
        try:
            row, state = self._state(run["name"])
            streak = 0 if ok else state.get("consecutive_failures", 0) + 1
            state["consecutive_failures"] = streak
            self._save_state(row, state)
            if not ok and streak == self.failure_alert_after:
                self._send_alert(
                    Alert("consecutive_failures", run["name"], run["dedupe_key"], error, streak)
                )
        except Exception:  # noqa: BLE001 - bookkeeping must not fail the run
            log.warning("failure streak bookkeeping failed", exc_info=True)

    def _apply(self, run: dict, att: dict, now: datetime, result: TickResult) -> None:
        """Write an attempt's outcome onto job_run (also used to reconcile after a crash)."""
        spec = self.jobs[run["name"]]
        outcome, attempt = att["outcome"], att["attempt"]
        payload = json.loads(run["payload"] or "{}")
        payload["_items"] = payload.get("_items", 0) + (att.get("items") or 0)
        key = run["dedupe_key"]
        extra = {
            "job": run["name"],
            "run_id": run["$id"],
            "dedupe_key": key,
            "period": run.get("scheduled_for"),
            "outcome": outcome,
            "items": att.get("items") or 0,
            "attempt": attempt,
        }
        if outcome == "completed":
            self.repo.update(
                "job_run",
                run["$id"],
                {
                    "status": "completed",
                    "finished_at": _iso(now),
                    "locked_until": None,
                    "payload": json.dumps(payload),
                },
            )
            self._note_attempt(run, True)
            result.completed.append(key)
        elif outcome == "continue":
            payload.update(
                {"_cursor": json.loads(att["result"])["cursor"], "_chunk": att["chunk"] + 1}
            )
            self.repo.update(
                "job_run",
                run["$id"],
                {
                    "status": "queued",
                    "run_at": _iso(now),
                    "payload": json.dumps(payload),
                    "attempts": 0,
                    "locked_until": None,
                },
            )
            self._note_attempt(run, True)
            result.continued.append(key)
        else:
            error = att.get("error") or "unknown error"
            self._note_attempt(run, False, error)
            if attempt >= run["max_attempts"]:
                self.repo.update(
                    "job_run",
                    run["$id"],
                    {
                        "status": "failed",
                        "attempts": attempt,
                        "finished_at": _iso(now),
                        "locked_until": None,
                        "last_error": error[:500],
                    },
                )
                result.failed.append(key)
                self._send_alert(Alert("final_failure", run["name"], key, error))
            else:
                delay = min(spec.backoff_base * (2 ** (attempt - 1)), MAX_BACKOFF)
                self.repo.update(
                    "job_run",
                    run["$id"],
                    {
                        "status": "queued",
                        "attempts": attempt,
                        "run_at": _iso(now + delay),
                        "locked_until": None,
                        "last_error": error[:500],
                    },
                )
                result.retried.append(key)
        level = logging.INFO if outcome != "failed" else logging.WARNING
        log.log(level, "job run finished", extra=extra)

    def _record(
        self,
        claim: _Claim,
        outcome: str,
        result: TickResult,
        *,
        error: str | None = None,
        items: int = 0,
        cursor: Any = None,
    ) -> None:
        """Attempt row first (source of truth), then job_run."""
        now = self.clock()
        att = self.repo.update(
            "job_attempt",
            claim.attempt_id,
            {
                "outcome": outcome,
                "finished_at": _iso(now),
                "error": error,
                "items": items,
                "result": json.dumps({"cursor": cursor}) if outcome == "continue" else None,
            },
        )
        self._apply(claim.run, att, now, result)

    def _reconcile(self, run_id: str, att: dict, result: TickResult) -> bool:
        """The worker recorded an outcome but died (or was superseded) before updating job_run."""
        run = self.repo.get("job_run", run_id)
        payload = json.loads(run["payload"] or "{}")
        if run["status"] in _TERMINAL or att.get("chunk", 0) != payload.get("_chunk", 0):
            return False
        if run["status"] == "queued" and run["attempts"] >= att["attempt"]:
            return False  # already applied
        self._apply(run, att, self.clock(), result)
        result.reconciled.append(run["dedupe_key"])
        return True

    # ------------------------------------------------------------ running
    def _invoke(self, spec: JobSpec, ctx: JobContext) -> Done | Continue:
        box: dict[str, Any] = {}
        cv = contextvars.copy_context()

        def target() -> None:
            try:
                box["value"] = cv.run(spec.handler, ctx)
            except BaseException as exc:  # noqa: BLE001 - re-raised in the caller
                box["error"] = exc

        t = threading.Thread(target=target, daemon=True, name=f"job-{spec.name}")
        t.start()
        t.join(spec.timeout.total_seconds())
        if t.is_alive():
            assert ctx.cancelled is not None
            ctx.cancelled.set()
            raise JobTimeout(f"timed out after {spec.timeout.total_seconds():g}s")
        if "error" in box:
            raise box["error"]
        return box["value"]

    def _process(self, cand: dict, result: TickResult) -> None:
        spec = self.jobs.get(cand["name"])
        if spec is None:
            log.warning("run for unknown job skipped", extra={"job": cand["name"]})
            result.unknown_jobs += 1
            return
        expired = cand["status"] == "running"
        if expired:
            chunk = json.loads(cand["payload"] or "{}").get("_chunk", 0)
            latest = self._latest_attempt(cand["$id"], chunk, cand["attempts"])
            if latest and latest.get("outcome"):
                self._reconcile(cand["$id"], latest, result)
                return
        claim = self._claim(cand)
        if claim is None:
            result.lost_claims += 1
            return
        run = claim.run
        if expired:
            self._note_attempt(run, False, "lease expired")
            if claim.attempt > run["max_attempts"]:
                self._record(claim, "failed", result, error="lease expired on final attempt")
                return
        payload = json.loads(run["payload"] or "{}")
        ctx = JobContext(
            run["name"],
            run["$id"],
            run["dedupe_key"],
            _dt(run.get("scheduled_for")),
            {k: v for k, v in payload.items() if not k.startswith("_")},
            payload.get("_cursor"),
            claim.chunk,
            threading.Event(),
        )
        try:
            out = self._invoke(spec, ctx)
        except Exception as exc:  # noqa: BLE001 - any handler error is a failed attempt
            detail = (
                str(exc)
                if isinstance(exc, JobTimeout)
                else (f"{type(exc).__name__}: {exc}\n{traceback.format_exc(limit=3)}")
            )
            self._record(claim, "failed", result, error=detail)
            return
        if isinstance(out, Continue):
            self._record(claim, "continue", result, items=out.items, cursor=out.cursor)
        else:
            self._record(claim, "completed", result, items=getattr(out, "items", 0))

    def tick(self, budget_s: float = TICK_BUDGET_SECONDS) -> TickResult:
        deadline = time.monotonic() + budget_s
        now = self.clock()
        result = TickResult(created=self.materialise(now))
        for cand in self._claimable(now):
            if time.monotonic() > deadline:
                break  # the rest waits for the next tick
            self._process(cand, result)
        return result
