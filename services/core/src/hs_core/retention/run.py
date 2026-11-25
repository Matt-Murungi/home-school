"""One run of the `retention-purge` job (C03 #141): guard, lease, stages, summary.

A run spans several F04 chunks (each at most ~6 s). State between chunks is the `Continue` cursor:
{seq, now, mode, i, after, stats}. The run's clock `now` is fixed in the first chunk, so a run that
straddles Kampala midnight judges every row by the same date.

Safety, in the order a run meets it:
1. PURGE GUARD (first chunk): refuses when the clock looks wrong (earlier than the last run's start,
   or a year outside 2026-2100). A refusal is recorded (`retention_run` status refused) and alerted;
   nothing is read or deleted.
2. LEASE: one run at a time. The lease is a `retention_run` row with a deterministic id per
   generation (`lease-<n>`), so exactly one of any number of concurrent starters creates
   generation n; a crashed holder is replaced after `lease_s`. The holder renews it every chunk.
3. DRY RUN unless RETENTION_ENFORCE: the same traversal, nothing written, `would_*` counts.
4. BLAST RADIUS per table (purger.blast_check) unless RETENTION_BLAST_OVERRIDE.
5. PER-TABLE ISOLATION: one table's failure is counted and alerted; the next table still runs.
6. SUMMARY: one `retention_run` row and ONE audit event per run with counts per table (never one
   audit row per purged row: millions of rows would bury the log; ids are listed only for tables
   where at most 50 rows were purged, e.g. safeguarding cases and documents).
Logs carry table names, counts and row ids only, never field values.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from datetime import time as dtime
from typing import Any, Protocol

from appwrite.query import Query

from ..jobs.alerts import Alert, AlertSink, LogAlertSink
from ..jobs.model import Continue, Done, JobContext
from ..notifications.retention import expire_chunk
from ..repository import Conflict, NotFound, Repository, RepositoryError
from .files import RetentionFileStore, sweep_orphans
from .holds import HoldLookupFailed
from .periods import KAMPALA, Period, kampala_date
from .policy import Rule, effective_policy, parse_overrides, purge_order
from .purger import PurgeConfig, RetentionPurger, new_stats

log = logging.getLogger("hs_core.retention")
JOB = "retention-purge"
TABLE = "retention_run"
MIN_YEAR, MAX_YEAR = 2026, 2100
CLOCK_SKEW = timedelta(minutes=5)
TIME_BUDGET_S = 6.0
WRITES_PER_CALL = 40  # writes per purge_chunk call
ROWS_PER_CHUNK = 200  # writes per F04 chunk (then Continue with a cursor)
PAYLOAD_STAGE = "payload:notification"
ORPHAN_STAGE = "files:orphans"
_DISPLAY = (
    "deleted anonymised quarantined waiting held blocked files errors would_delete "
    "would_anonymise would_quarantine scanned"
).split()


class RetentionAudit(Protocol):
    def summary(self, *, key: str, action: str, entity_id: str, detail: dict[str, Any]) -> None: ...


class NullAudit:
    def summary(self, **_: Any) -> None:
        return None


class AuditServiceSink:
    """M06: ONE event per run via `record_external` under the key `retention:<seq>`."""

    def __init__(self, service: Any) -> None:
        self._svc = service

    def summary(self, *, key: str, action: str, entity_id: str, detail: dict[str, Any]) -> None:
        from ..audit import payload

        self._svc.record_external(
            key, content_hash=payload.stable_hash({"action": action, "detail": detail}),
            action=action, entity=TABLE, entity_id=entity_id, actor_id=None,
            roles=("system",), job=JOB, after=detail,
        )  # fmt: skip


def iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat()


# ---------------------------------------------------------------------- guard
class GuardRefused(Exception):
    pass


def purge_guard(repo: Repository, now: datetime) -> None:
    """Raise GuardRefused when `now` cannot be right. Cheap: one indexed read."""
    if not MIN_YEAR <= now.year <= MAX_YEAR:
        raise GuardRefused(f"clock_year:{now.year}")
    last = repo.list(
        TABLE,
        [Query.equal("kind", ["run"]), Query.order_desc("started_at"), Query.limit(1)],
    )
    if last:
        started = datetime.fromisoformat(last[0]["started_at"])
        if now < started - CLOCK_SKEW:
            raise GuardRefused("clock_before_last_run")


# ---------------------------------------------------------------------- lease
def _lease_id(seq: int) -> str:
    return f"lease-{seq:010d}"


def acquire_lease(
    repo: Repository, owner: str, now: datetime, lease_s: int, mode: str
) -> int | None:
    """Generation number held by `owner`, or None when another run holds a live lease."""
    latest = repo.list(
        TABLE, [Query.equal("kind", ["lease"]), Query.order_desc("seq"), Query.limit(1)]
    )
    seq = 1
    if latest:
        row = latest[0]
        live = (
            row.get("status") == "running"
            and row.get("lease_until_at")
            and datetime.fromisoformat(row["lease_until_at"]) > now
        )
        if live:
            if row.get("owner") != owner:
                return None
            repo.update(
                TABLE, row["$id"], {"lease_until_at": iso(now + timedelta(seconds=lease_s))}
            )
            return int(row["seq"])
        seq = int(row["seq"]) + 1
    try:
        repo.create(
            TABLE,
            _lease_id(seq),
            {
                "kind": "lease", "seq": seq, "owner": owner, "mode": mode, "status": "running",
                "started_at": iso(now), "lease_until_at": iso(now + timedelta(seconds=lease_s)),
            },
        )  # fmt: skip
    except Conflict:
        return None  # another starter created this generation first
    return seq


def release_lease(repo: Repository, seq: int, now: datetime, status: str = "completed") -> None:
    try:
        repo.update(TABLE, _lease_id(seq), {"status": status, "finished_at": iso(now)})
    except NotFound:
        pass


# ---------------------------------------------------------------------- stages
@dataclass
class Deps:
    repo: Repository
    files: RetentionFileStore
    config: PurgeConfig
    overrides_text: str = ""
    lease_s: int = 600
    clock: Callable[[], datetime] = lambda: datetime.now(UTC)  # noqa: E731
    alert: AlertSink | None = None
    audit: RetentionAudit | None = None
    policy: dict[str, Rule] | None = None
    payload_page: int = 40


def stages(policy: dict[str, Rule]) -> list[str]:
    order = [
        t for t in purge_order(policy) if RetentionPurger.skip_reason_static(policy[t]) is None
    ]
    return [PAYLOAD_STAGE, *order, ORPHAN_STAGE]


def _compact(stats: dict[str, dict]) -> dict[str, dict]:
    out = {}
    for t, s in stats.items():
        c = {k: v for k, v in s.items() if v not in (0, [], {}, None, False)}
        if c:
            out[t] = c
    return out


def _payload_boundary(now: datetime, days: int) -> datetime:
    start: date = kampala_date(now) - timedelta(days=days)
    return datetime.combine(start, dtime(0, 0), KAMPALA)


class RetentionJob:
    def __init__(self, deps: Deps) -> None:
        self.d = deps
        self.policy = deps.policy or effective_policy(parse_overrides(deps.overrides_text))
        self.alert = deps.alert or LogAlertSink()
        self.audit = deps.audit or NullAudit()
        self.purger = RetentionPurger(deps.repo, deps.files, self.policy, deps.config, deps.clock)
        self.stages = stages(self.policy)

    @property
    def mode(self) -> str:
        return "enforce" if self.d.config.enforce else "dry_run"

    def _alert(self, ctx: JobContext, kind: str, error: str) -> None:
        self.alert.send(Alert(kind=kind, job=JOB, dedupe_key=ctx.dedupe_key, error=error[:300]))

    # ------------------------------------------------------------------ the handler
    def __call__(self, ctx: JobContext) -> Done | Continue:
        cur = ctx.cursor or {}
        now_real = self.d.clock()
        if "seq" not in cur:
            started = self._start(ctx, now_real)
            if isinstance(started, Done):
                return started
            cur = started
        now = datetime.fromisoformat(cur["now"])
        seq = cur["seq"]
        held = acquire_lease(self.d.repo, ctx.run_id, now_real, self.d.lease_s, self.mode)
        if held != seq:
            return Done(detail="lease lost or held by another run: standing down", items=0)
        stats: dict[str, dict] = cur["stats"]
        deadline = time.monotonic() + TIME_BUDGET_S

        def cancelled() -> bool:
            return bool(ctx.cancelled and ctx.cancelled.is_set())

        i, after = cur["i"], cur.get("after")
        items = 0
        progressed = False

        def stop() -> bool:
            return cancelled() or time.monotonic() > deadline or items >= ROWS_PER_CHUNK

        while i < len(self.stages):
            if cancelled() or (progressed and stop()):
                break
            progressed = True
            stage = self.stages[i]
            st = stats[stage] = {**new_stats(), **stats.get(stage, {})}
            try:
                done, after, n = self._stage(
                    stage,
                    now,
                    after,
                    st,
                    stop,
                    max(1, min(WRITES_PER_CALL, ROWS_PER_CHUNK - items)),
                )
            except HoldLookupFailed as exc:
                st["errors"] += 1
                st["refused"] = f"hold_lookup_failed:{exc}"
                self._alert(ctx, "retention_hold_lookup_failed", f"{stage}: {exc}")
                done, after, n = True, None, 0
            except (RepositoryError, OSError, ValueError) as exc:  # per-table isolation
                st["errors"] += 1
                st["failed"] = type(exc).__name__
                log.warning(
                    "retention table failed",
                    extra={"table": stage, "error_type": type(exc).__name__},
                )
                self._alert(ctx, "retention_table_failed", f"{stage}: {type(exc).__name__}")
                done, after, n = True, None, 0
            items += n
            if done:
                i, after = i + 1, None
        if i < len(self.stages):
            return Continue(
                {"seq": seq, "now": cur["now"], "mode": cur["mode"], "i": i, "after": after,
                 "stats": _compact_keep(stats)},
                items=items,
            )  # fmt: skip
        return self._finish(ctx, seq, now, now_real, stats, items)

    # ------------------------------------------------------------------ first chunk
    def _start(self, ctx: JobContext, now: datetime) -> dict | Done:
        repo = self.d.repo
        try:
            purge_guard(repo, now)
        except GuardRefused as g:
            seq = acquire_lease(repo, ctx.run_id, now, self.d.lease_s, self.mode)
            if seq is not None:
                self._write_run(seq, now, "refused", {}, note=str(g))
                release_lease(repo, seq, now, "refused")
            self._alert(ctx, "retention_guard_refused", str(g))
            log.error("retention purge refused by the purge guard", extra={"reason": str(g)})
            return Done(detail=f"refused: {g}", items=0)
        seq = acquire_lease(repo, ctx.run_id, now, self.d.lease_s, self.mode)
        if seq is None:
            return Done(detail="another purge run holds the lease", items=0)
        self._write_run(seq, now, "running", {})
        return {"seq": seq, "now": iso(now), "mode": self.mode, "i": 0, "after": None, "stats": {}}

    def _write_run(
        self, seq: int, now: datetime, status: str, counts: dict, note: str = ""
    ) -> None:
        row = {
            "kind": "run", "seq": seq, "mode": self.mode, "status": status,
            "started_at": iso(now), "counts": json.dumps(counts)[:60000], "note": note[:200],
        }  # fmt: skip
        rid = f"run-{seq:010d}"
        try:
            self.d.repo.create(TABLE, rid, row)
        except Conflict:
            self.d.repo.update(TABLE, rid, {k: v for k, v in row.items() if k != "started_at"})

    # ------------------------------------------------------------------ one stage chunk
    def _stage(self, stage, now, after, st, stop, budget) -> tuple[bool, str | None, int]:
        if stage == PAYLOAD_STAGE:
            return self._payload(now, after, st)
        if stage == ORPHAN_STAGE:
            res = sweep_orphans(
                self.d.repo, self.d.files, self.policy, now, enforce=self.d.config.enforce
            )
            st["scanned"] += res["scanned"]
            st["files"] += res["deleted"]
            st["would_delete"] += 0 if self.d.config.enforce else res["orphans"]
            return True, None, res["deleted"]
        if after is None and not st.get("checked"):
            st["checked"] = True
            reason = self.purger.blast_check(self.policy[stage], now)
            if reason:
                st["refused"] = reason
                self.alert.send(
                    Alert(kind="retention_blast_limit", job=JOB, dedupe_key=stage, error=reason)
                )
                return True, None, 0
        chunk = self.purger.purge_chunk(stage, now, after, st, budget, stop)
        return chunk.done, chunk.after, chunk.writes

    def _payload(self, now, after, st) -> tuple[bool, str | None, int]:
        """N01 `expire_chunk`: strip template variables of notifications older than the payload
        period. Resumable by cursor; a dry run changes nothing."""
        days = (self.policy["notification"].payload_expiry or Period(days=90)).days
        if not self.d.config.enforce:
            st["skipped"] = "dry_run"
            return True, None, 0
        res = expire_chunk(
            self.d.repo,
            _payload_boundary(now, days),
            now,
            page=self.d.payload_page,
            start_after=after,
        )
        st["anonymised"] += res.changed
        return res.done, res.next_cursor, res.changed

    # ------------------------------------------------------------------ last chunk
    def _finish(self, ctx, seq, now, now_real, stats, items) -> Done:
        compact = _compact(stats)
        totals = {
            k: sum(s.get(k, 0) for s in compact.values() if isinstance(s.get(k, 0), int))
            for k in _DISPLAY
        }
        detail = {
            "mode": self.mode,
            "totals": totals,
            "tables": {t: _brief(s) for t, s in compact.items()},
        }
        self._write_run(seq, now, "completed", compact)
        self.d.repo.update(
            TABLE, f"run-{seq:010d}", {"finished_at": iso(now_real), "status": "completed"}
        )
        release_lease(self.d.repo, seq, now_real)
        try:
            self.audit.summary(
                key=f"retention:{seq}",
                action="retention.purge" if self.mode == "enforce" else "retention.dry_run",
                entity_id=f"run-{seq:010d}",
                detail=detail,
            )
        except Exception as exc:  # noqa: BLE001 - the summary must not undo a completed purge
            self._alert(ctx, "retention_audit_failed", type(exc).__name__)
        for t, s in compact.items():
            log.info("retention table", extra={"table": t, **_brief(s, ids=False)})
        return Done(detail=f"{self.mode}: " + json.dumps(totals), items=items)


def _brief(s: dict, ids: bool = True) -> dict:
    out = {k: v for k, v in s.items() if k not in ("ids", "checked")}
    if ids and s.get("ids") and s.get("deleted", 0) + s.get("would_delete", 0) <= 50:
        out["ids"] = s["ids"]
    return out


def _compact_keep(stats: dict[str, dict]) -> dict[str, dict]:
    """The cursor keeps `checked` flags and counts (ids are bounded: 50 per table)."""
    return {
        t: {k: v for k, v in s.items() if v not in (0, [], {}, None, False) or k == "checked"}
        for t, s in stats.items()
        if any(v not in (0, [], {}, None, False) for v in s.values())
    }
