"""The retention purger (C03 #141): applies ONE policy rule to ONE table, a chunk at a time.

This module (with deletion.py, which reuses its primitives) is the ONLY place that hard-deletes
rows of soft, immutable or append-only tables: the M01-M05 rule "the retention job is the only
allowed hard-deleter" (appwrite/README.md). `tests/test_retention_tripwire.py` scans src/hs_core
for any other `.delete(` with a non-literal table name.

Per row, in this order (every step idempotent, a crash after any write is resumed by the next run):
  1. due?  exact calendar rule (periods.is_due) on the rule's clock; the repository filter is only a
     looser prefilter, so a due row is never missed and a not-yet-due row is never deleted.
  2. hold? (holds.HoldChecker) re-checked immediately before the write; fail closed.
  3. restrict / cascade / anonymise-reference handling for rows that point at this one
     (children before parents).
  4. soft tables: QUARANTINE first (`soft_delete_patch`: deleted_at set, derived unique keys nulled,
     M02), hard delete only once the row has been soft-deleted for `quarantine_days`.
  5. files first, then the row. Dry run writes nothing and counts `would_*`.

Paging: `order_asc($id)` + `cursor_after`, at most 100 rows per list call, never offsets (Appwrite
caps offsets and totals at 5,000). Because purged rows disappear, the resume cursor is the LAST
ROW THAT STILL EXISTS; if the cursor row itself vanished the table restarts from its beginning
(safe: every step is idempotent).
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from appwrite.query import Query

from ..append_only import APPEND_ONLY_TABLES
from ..repository import NotFound, Repository, RepositoryError, count_rows
from .clocks import CLOCKS
from .files import RetentionFileStore
from .holds import HoldChecker
from .periods import is_due, prefilter_boundary
from .policy import (
    DERIVED_KEYS,
    OWN_SUBJECT,
    PSEUDO,
    SOFT_TABLES,
    Action,
    OnParentPurge,
    Rule,
    referencing,
)

log = logging.getLogger("hs_core.retention")

PAGE = 100
CASCADE_CAP = 5000
ID_SAMPLE = 50
APPEND_CLOCK = {"safeguarding_action": "acted_at", "audit_log": "occurred_at"}


class AppendOnlyPurgeRefused(Exception):
    """A purge of an append-only table that the exception path does not allow."""


class PurgeRefused(Exception):
    """purge_chunk was asked for a table the policy never purges by age."""


class Blocked(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def pseudonym(value: str) -> str:
    """Stable one-way stand-in for an id: the same subject keeps one pseudonym across rows (so a
    tax auditor can still group payments) but nothing links it back once the profile is gone."""
    return "anon-" + hashlib.sha256(f"hs-retention-v1:{value}".encode()).hexdigest()[:30]


@dataclass
class PurgeConfig:
    enforce: bool = False  # dry run unless the owner switches it on (RETENTION_ENFORCE)
    quarantine_days: int = 14
    max_delete_pct: int = 10
    blast_override: bool = False
    blast_min_rows: int = 50  # below this a table is too small for the percentage test
    allow_append_only: bool = False
    per_table_cap: int = 5000
    page: int = PAGE


def new_stats() -> dict[str, Any]:
    return {
        "scanned": 0, "not_due": 0, "held": 0, "blocked": 0, "deleted": 0, "anonymised": 0,
        "quarantined": 0, "waiting": 0, "files": 0, "errors": 0, "would_delete": 0,
        "would_anonymise": 0, "would_quarantine": 0, "children": 0, "ids": [],
    }  # fmt: skip


@dataclass
class Chunk:
    after: str | None
    done: bool
    writes: int = 0


@dataclass
class _Plan:
    steps: list[tuple[Rule, dict]] = field(default_factory=list)  # delete order
    anonymise_refs: list[tuple[Rule, dict, str, str]] = field(default_factory=list)


class RetentionPurger:
    def __init__(
        self,
        repo: Repository,
        files: RetentionFileStore,
        policy: dict[str, Rule],
        config: PurgeConfig,
        clock: Callable[[], datetime] | None = None,
        holds: HoldChecker | None = None,
    ) -> None:
        self.repo = repo
        self.files = files
        self.policy = policy
        self.config = config
        self.clock = clock or (lambda: datetime.now(UTC))
        self.holds = holds or HoldChecker(repo, policy)
        self._vanished: dict[str, bool] = {}  # row ids a concurrent run deleted first

    # ------------------------------------------------------------------ which tables
    def skip_reason(self, rule: Rule) -> str | None:
        reason = self.skip_reason_static(rule)
        if reason == "append_only" and self._append_only_allowed_table(rule):
            return None
        return reason

    @staticmethod
    def skip_reason_static(rule: Rule) -> str | None:
        """Why a table is not purged by age at all (independent of settings), or None."""
        if rule.never_purge:
            return "never_purge"
        if rule.action in (Action.KEEP, Action.CASCADE):
            return rule.action.value
        if rule.enforced_by != "retention-purge":
            return f"enforced_by:{rule.enforced_by}"
        if rule.period is None or rule.clock is None:
            return "no_period"
        if rule.append_only:
            return "append_only"
        return None

    def _append_only_allowed_table(self, rule: Rule) -> bool:
        return bool(self.config.allow_append_only and not rule.never_purge and rule.floor)

    # ------------------------------------------------------------------ due
    def _clock_value(self, rule: Rule, row: dict) -> object:
        fn = CLOCKS.get(rule.table)
        return fn(self.repo, row) if fn else row.get(rule.clock or "")

    def is_due(self, rule: Rule, row: dict, now: datetime) -> bool:
        assert rule.period is not None
        return is_due(self._clock_value(rule, row), rule.period, now)

    def _queries(self, rule: Rule, now: datetime) -> list[str]:
        assert rule.period is not None and rule.clock
        q = [Query.less_than(rule.clock, prefilter_boundary(rule.period, now))]
        for col, values in rule.where.items():
            q.append(Query.equal(col, list(values)))
        if rule.action is Action.ANONYMISE and rule.marker:
            q.append(Query.not_starts_with(rule.marker[0], rule.marker[1]))
        return q

    # ------------------------------------------------------------------ blast radius
    def blast_check(self, rule: Rule, now: datetime) -> str | None:
        """None = fine; otherwise the refusal reason. Counts the rows the prefilter selects (an
        over-estimate of the due rows: conservative) against the whole table."""
        if self.config.blast_override:
            return None
        total = count_rows(self.repo, rule.table, [])
        if total < self.config.blast_min_rows:
            return None
        due = count_rows(self.repo, rule.table, self._queries(rule, now))
        if due * 100 > self.config.max_delete_pct * total:
            return f"blast_limit:{due}/{total}>{self.config.max_delete_pct}%"
        return None

    # ------------------------------------------------------------------ one chunk of one table
    def purge_chunk(
        self,
        table: str,
        now: datetime,
        after: str | None,
        stats: dict[str, Any],
        max_writes: int = 40,
        stop: Callable[[], bool] = lambda: False,
    ) -> Chunk:
        rule = self.policy[table]
        why = self.skip_reason(rule)
        if why:  # defence in depth: the job never asks, but a direct caller must not either
            raise PurgeRefused(f"{table} is not purged by age: {why}")
        queries = self._queries(rule, now)
        last_exist = after
        writes = 0
        cursor = after
        restarted = False
        while True:
            q = [*queries, Query.order_asc("$id"), Query.limit(min(self.config.page, PAGE))]
            if cursor:
                q.append(Query.cursor_after(cursor))
            try:
                rows = self.repo.list(table, q)
            except (ValueError, RepositoryError):
                if not cursor or restarted:
                    raise
                restarted, cursor, last_exist = True, None, None  # the cursor row vanished
                continue
            if not rows:
                return Chunk(None, True, writes)
            due = []
            for r in rows:
                stats["scanned"] += 1
                if self.is_due(rule, r, now):
                    due.append(r)
                else:
                    stats["not_due"] += 1
            held = self.holds.held_ids(rule, due) if due else {}
            due_ids = {r["$id"] for r in due}
            for r in rows:
                if r["$id"] not in due_ids:
                    last_exist = r["$id"]
                    continue
                if stop() or writes >= max_writes:
                    return Chunk(last_exist, False, writes)
                if self._touched(stats) >= self.config.per_table_cap:
                    stats["capped"] = True
                    return Chunk(None, True, writes)
                if r["$id"] in held:
                    stats["held"] += 1
                    last_exist = r["$id"]
                    continue
                gone, wrote = self._act(rule, r, now, stats)
                writes += wrote
                if not gone:
                    last_exist = r["$id"]
            if len(rows) < min(self.config.page, PAGE):
                return Chunk(None, True, writes)
            cursor = last_exist  # purged rows are gone: continue after the last row that remains
            if stop():
                return Chunk(last_exist, False, writes)

    _TOUCH = (
        "deleted", "anonymised", "quarantined",
        "would_delete", "would_anonymise", "would_quarantine",
    )  # fmt: skip

    @classmethod
    def _touched(cls, stats: dict[str, Any]) -> int:
        return sum(stats[k] for k in cls._TOUCH)

    # ------------------------------------------------------------------ one row
    def _act(self, rule: Rule, row: dict, now: datetime, stats: dict[str, Any]) -> tuple[bool, int]:
        """(row is gone, writes made). Errors on one row never stop the table."""
        try:
            if self.holds.is_held(rule, row):  # final re-check right before the write
                stats["held"] += 1
                return False, 0
            if rule.action is Action.ANONYMISE:
                return False, self._anonymise_row(rule, row, None, stats)
            return self._expire_row(rule, row, now, stats)
        except Blocked as b:
            stats["blocked"] += 1
            stats.setdefault("blocked_reasons", {}).setdefault(b.reason, 0)
            stats["blocked_reasons"][b.reason] += 1
            return False, 0
        except (RepositoryError, OSError) as exc:
            stats["errors"] += 1
            log.warning(
                "retention row failed",
                extra={"table": rule.table, "row_id": row["$id"], "error_type": type(exc).__name__},
            )
            return False, 0

    def _expire_row(
        self, rule: Rule, row: dict, now: datetime, stats: dict[str, Any]
    ) -> tuple[bool, int]:
        soft = rule.table in SOFT_TABLES
        q_days = self.config.quarantine_days
        if soft and q_days > 0:
            deleted_at = row.get("deleted_at")
            if not deleted_at:
                if not self.config.enforce:
                    stats["would_quarantine"] += 1
                    return False, 0
                self.repo.update(rule.table, row["$id"], quarantine_patch(rule.table, now))
                stats["quarantined"] += 1
                return False, 1
            if self._soft_age_days(deleted_at, now) < q_days:
                stats["waiting"] += 1
                return False, 0
        plan = self.plan_delete(rule, row, now)
        if not self.config.enforce:
            stats["would_delete"] += 1
            stats["children"] += len(plan.steps) - 1
            return False, 0
        n = self.execute_plan(plan, now, stats)
        if self._vanished.pop(row["$id"], False):
            stats["vanished"] = stats.get("vanished", 0) + 1  # someone else deleted it first
            return True, n
        stats["deleted"] += 1
        if len(stats["ids"]) < ID_SAMPLE:
            stats["ids"].append(row["$id"])
        return True, n

    @staticmethod
    def _soft_age_days(deleted_at: str, now: datetime) -> float:
        try:
            dt = datetime.fromisoformat(deleted_at.replace("Z", "+00:00"))
        except ValueError:
            return 0.0
        return (now - dt).total_seconds() / 86400

    # ------------------------------------------------------------------ plans (reads only)
    def plan_delete(self, rule: Rule, row: dict, now: datetime, depth: int = 0) -> _Plan:
        """Everything that must go with `row`, children first. Raises Blocked when a RESTRICT
        reference exists, a child is under a hold, or an append-only row may not be purged."""
        if depth > 6:
            raise Blocked("depth")
        plan = _Plan()
        self._check_append_only(rule, row, now)
        for crule, link in referencing(rule.table, self.policy):
            if link.on_parent_purge is OnParentPurge.IGNORE:
                continue
            refs = self._referencing_rows(crule, link.column, row["$id"])
            if not refs:
                continue
            if link.on_parent_purge is OnParentPurge.RESTRICT:
                raise Blocked(f"restrict:{crule.table}")
            if link.on_parent_purge is OnParentPurge.ANONYMISE:
                plan.anonymise_refs += [(crule, r, link.column, link.parent) for r in refs]
                continue
            held = self.holds.held_ids(crule, refs)
            if held:
                raise Blocked(f"child_held:{crule.table}")
            for r in refs:
                sub = self.plan_delete(crule, r, now, depth + 1)
                plan.steps += sub.steps
                plan.anonymise_refs += sub.anonymise_refs
        plan.steps.append((rule, row))
        return plan

    def _referencing_rows(self, crule: Rule, column: str, parent_id: str) -> list[dict]:
        rows: list[dict] = []
        after = None
        while True:
            q = [Query.equal(column, [parent_id]), Query.order_asc("$id"), Query.limit(PAGE)]
            if after:
                q.append(Query.cursor_after(after))
            page = self.repo.list(crule.table, q)
            rows += page
            if len(page) < PAGE:
                return rows
            if len(rows) >= CASCADE_CAP:
                raise Blocked(f"too_many:{crule.table}")
            after = page[-1]["$id"]

    def _check_append_only(self, rule: Rule, row: dict, now: datetime) -> None:
        """The append-only exception path: a purge of an append-only table needs ALL of: the owner's
        switch (RETENTION_ALLOW_APPEND_ONLY_PURGE), a rule that is not `never_purge`, a legal floor,
        and THIS row older than that floor by its own timestamp (re-verified per row, per call)."""
        if rule.table not in APPEND_ONLY_TABLES:
            return
        clock = APPEND_CLOCK.get(rule.table)
        if (
            not self.config.allow_append_only
            or rule.never_purge
            or rule.floor is None
            or clock is None
            or not is_due(row.get(clock), rule.floor, now)
        ):
            raise Blocked("append_only")

    # ------------------------------------------------------------------ execution (writes)
    def execute_plan(self, plan: _Plan, now: datetime, stats: dict[str, Any]) -> int:
        n = 0
        for crule, r, column, parent in plan.anonymise_refs:
            n += self._pseudonymise_reference(crule, r, column, parent, stats)
        for rule, row in plan.steps:
            n += self.erase_row(rule, row, now, stats)
        return n

    def erase_row(self, rule: Rule, row: dict, now: datetime, stats: dict[str, Any]) -> int:
        """Files first, then the row. Idempotent: a file or row that is already gone is fine."""
        self._check_append_only(rule, row, now)  # defence in depth: also at the moment of the write
        n = 0
        for f in rule.files:
            fid = row.get(f.column)
            if isinstance(fid, str) and fid:
                if self.files.delete(f.bucket, fid):
                    stats["files"] += 1
                n += 1
        try:
            self.repo.delete(rule.table, row["$id"])
            n += 1
        except NotFound:
            self._vanished[row["$id"]] = True
        if rule.table in APPEND_ONLY_TABLES:
            stats["append_only_purged"] = stats.get("append_only_purged", 0) + 1
            log.warning(
                "append-only row purged through the retention exception path",
                extra={"table": rule.table, "row_id": row["$id"]},
            )
        return n

    def _pseudonymise_reference(
        self, crule: Rule, row: dict, column: str, parent: str, stats: dict[str, Any]
    ) -> int:
        """The parent is going away: the referencing row keeps existing but loses the link AND
        every personal field its rule minimises for that kind, in ONE update. (A partial update
        would leave a row whose subject column no longer matches, so no later run could find and
        minimise its other fields.)"""
        kind = OWN_SUBJECT.get(parent)
        patch = self.anonymise_patch(crule, row, {kind} if kind else set())
        v = row.get(column)
        if column not in patch and isinstance(v, str) and v and not v.startswith("anon-"):
            patch[column] = pseudonym(v)
        if not patch:
            return 0
        self.repo.update(crule.table, row["$id"], patch)
        stats["anonymised"] += 1
        return 1

    # ------------------------------------------------------------------ anonymise
    def anonymise_patch(self, rule: Rule, row: dict, kinds: set[str] | None) -> dict[str, Any]:
        patch: dict[str, Any] = {}
        for kind, cols in rule.anonymise.items():
            if kind != "*" and kinds is not None and kind not in kinds:
                continue
            for col, tomb in cols.items():
                cur = row.get(col)
                new = tomb
                if tomb == PSEUDO:
                    new = (
                        cur
                        if not isinstance(cur, str) or cur.startswith("anon-")
                        else pseudonym(cur)
                    )
                if cur != new:
                    patch[col] = new
        return patch

    def _anonymise_row(
        self, rule: Rule, row: dict, kinds: set[str] | None, stats: dict[str, Any]
    ) -> int:
        patch = self.anonymise_patch(rule, row, kinds)
        if not patch:
            return 0
        if not self.config.enforce:
            stats["would_anonymise"] += 1
            return 0
        self.repo.update(rule.table, row["$id"], patch)
        stats["anonymised"] += 1
        if len(stats["ids"]) < ID_SAMPLE:
            stats["ids"].append(row["$id"])
        return 1


def quarantine_patch(table: str, now: datetime) -> dict[str, Any]:
    """M02 `schema_build.soft_delete_patch`: deleted_at plus every derived unique key set to null.
    (`tests/test_retention_policy.py` compares it with schema_build's, so they cannot drift.)"""
    patch: dict[str, Any] = {"deleted_at": now.astimezone(UTC).isoformat()}
    patch.update({k: None for k in DERIVED_KEYS.get(table, ())})
    return patch
