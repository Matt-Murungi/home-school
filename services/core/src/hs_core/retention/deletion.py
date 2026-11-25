"""Data-subject deletion (C03 #141, SR DP-5): the lifecycle and the executor.

C05 (#143) owns the user-facing flow (export, confirmation screens, notices). This module gives it
two things: `DeletionService` (the `deletion_request` lifecycle) and `execute_deletion` (what a
deletion DOES). Nothing else in the core may purge a person's data.

Lifecycle:  requested -> verified -> scheduled (grace period) -> executed -> confirmed
            cancelled (any time before executed)   blocked (a hold applies; the request is closed)

What `execute_deletion(kind, id)` does, from the `dsr` decision of every policy rule:
- DELETE:    the subject's rows go (files first; children before parents).
- ANONYMISE: rows are kept but the subject's references and personal fields become tombstones
             (payments, invoices, earnings, payouts, sessions: tax law, the other party).
- RETAIN:    kept untouched (consent, ledger, audit log, safeguarding cases) and COUNTED so the
             confirmation can say what was kept and why (`result.retained`).
Parent: the family's children are deleted first (cascade), then the parent's rows, then the profile.
Teacher: documents (Storage objects included) and profile go; earnings and payouts keep a pseudonym.
User: resolves to the parent and teacher profiles of that user, then the user's own rows.

HOLDS: if ANY row of the subject (or of the children it cascades to) is under a hold, NOTHING is
changed: `DeletionBlocked`, the request becomes `blocked`. The check runs when scheduling and
again at execution. Re-running a finished deletion is a no-op (every step is idempotent).
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from appwrite.query import Query

from ..repository import Conflict, NotFound, Repository
from .files import RetentionFileStore
from .holds import HoldChecker
from .policy import POLICY, Dsr, Rule, purge_order
from .purger import PurgeConfig, RetentionPurger, new_stats

log = logging.getLogger("hs_core.retention.deletion")
TABLE = "deletion_request"
KINDS = ("parent", "child", "teacher", "user")
PAGE = 100
OPEN = ("requested", "verified", "scheduled")
ALLOWED = {
    "requested": {"verified", "cancelled"},
    "verified": {"scheduled", "cancelled"},
    "scheduled": {"executed", "cancelled", "blocked"},
    "executed": {"confirmed"},
}
PROFILE_TABLE = {
    "parent": "parent_profile",
    "child": "child",
    "teacher": "teacher_profile",
    "user": "user_profile",
}


class DeletionError(Exception):
    pass


class DeletionBlocked(DeletionError):
    def __init__(self, reason: str, table: str = "", count: int = 0) -> None:
        super().__init__(reason)
        self.reason, self.table, self.count = reason, table, count


class BadTransition(DeletionError):
    pass


@dataclass
class DeletionResult:
    kind: str
    subject_id: str
    deleted: dict[str, int] = field(default_factory=dict)
    anonymised: dict[str, int] = field(default_factory=dict)
    retained: dict[str, int] = field(default_factory=dict)
    files: int = 0
    children: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "deleted": self.deleted,
            "anonymised": self.anonymised,
            "retained": self.retained,
            "files": self.files,
            "children": len(self.children),
        }


def _rows(repo: Repository, table: str, column: str, value: str) -> list[dict]:
    out, after = [], None
    while True:
        q = [Query.equal(column, [value]), Query.order_asc("$id"), Query.limit(PAGE)]
        if after:
            q.append(Query.cursor_after(after))
        page = repo.list(table, q)
        out += page
        if len(page) < PAGE:
            return out
        after = page[-1]["$id"]


def _subject_rows(repo: Repository, rule: Rule, kind: str, sid: str) -> list[dict]:
    """Rows of `rule.table` that belong to subject (kind, sid), including its own profile row."""
    rows: list[dict] = []
    if rule.own_subject == kind:
        try:
            rows.append(repo.get(rule.table, sid))
        except NotFound:
            pass
    for col, k in rule.subjects.items():
        if k == kind:
            rows += _rows(repo, rule.table, col, sid)
    seen, out = set(), []
    for r in rows:
        if r["$id"] not in seen:
            seen.add(r["$id"])
            out.append(r)
    return out


def _expand(repo: Repository, kind: str, sid: str) -> list[tuple[str, str]]:
    """Subjects deleted WITH this one, children first: a parent takes its children, a user takes
    the parent and teacher profiles that belong to it. The subject itself comes last."""
    out: list[tuple[str, str]] = []
    if kind == "parent":
        out += [("child", c["$id"]) for c in _rows(repo, "child", "parent_id", sid)]
    elif kind == "user":
        for p in _rows(repo, "parent_profile", "user_id", sid):
            out += _expand(repo, "parent", p["$id"])
        out += [("teacher", t["$id"]) for t in _rows(repo, "teacher_profile", "user_id", sid)]
    return [*out, (kind, sid)]


def execute_deletion(
    repo: Repository,
    files: RetentionFileStore,
    kind: str,
    subject_id: str,
    *,
    policy: dict[str, Rule] | None = None,
    clock: Callable[[], datetime] | None = None,
    holds: HoldChecker | None = None,
) -> DeletionResult:
    """Delete or anonymise everything of one data subject (see the module docstring). Raises
    DeletionBlocked (nothing changed) when a hold applies. Idempotent."""
    if kind not in KINDS:
        raise DeletionError(f"unknown subject kind {kind!r}")
    pol = policy or POLICY
    now = (clock or (lambda: datetime.now(UTC)))()
    hold = holds or HoldChecker(repo, pol)
    purger = RetentionPurger(
        repo, files, pol, PurgeConfig(enforce=True, quarantine_days=0), clock, hold
    )
    subjects = _expand(repo, kind, subject_id)
    order = purge_order(pol)
    work: list[tuple[str, str, Rule, list[dict]]] = []
    result = DeletionResult(
        kind, subject_id, children=[s for k, s in subjects if k == "child" and kind != "child"]
    )
    # 1. read everything and refuse as a whole if any of it is held
    seen: set[tuple[str, str, str]] = set()  # (table, row id, kind or "-"): a row is handled once
    for k, sid in subjects:
        for table in order:
            rule = pol[table]
            if rule.dsr is Dsr.NONE:
                continue
            rows = []
            for r in _subject_rows(repo, rule, k, sid):
                key = (table, r["$id"], k if rule.dsr is Dsr.ANONYMISE else "-")
                if key not in seen:
                    seen.add(key)
                    rows.append(r)
            if not rows:
                continue
            if rule.dsr is Dsr.RETAIN:
                result.retained[table] = result.retained.get(table, 0) + len(rows)
                continue
            held = hold.held_ids(rule, rows)
            if held:
                raise DeletionBlocked("hold", table, len(held))
            work.append((k, sid, rule, rows))
    # 2. act, children before parents (`order`), rows of one subject before the next subject
    stats = new_stats()
    for k, _sid, rule, rows in work:
        for row in rows:
            try:
                if rule.dsr is Dsr.ANONYMISE:
                    n = purger._anonymise_row(rule, row, {k}, stats)  # noqa: SLF001
                    if n:
                        result.anonymised[rule.table] = result.anonymised.get(rule.table, 0) + 1
                else:
                    plan = purger.plan_delete(rule, row, now)
                    purger.execute_plan(plan, now, stats)
                    for r_rule, _r in plan.steps:
                        result.deleted[r_rule.table] = result.deleted.get(r_rule.table, 0) + 1
            except NotFound:
                continue
    result.files = stats["files"]
    return result


# ---------------------------------------------------------------------- lifecycle
def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat()


class DeletionService:
    def __init__(
        self,
        repo: Repository,
        files: RetentionFileStore,
        *,
        grace_days: int = 30,
        clock: Callable[[], datetime] | None = None,
        policy: dict[str, Rule] | None = None,
        audit: Any | None = None,
    ) -> None:
        self.repo, self.files, self.grace = repo, files, timedelta(days=grace_days)
        self.clock = clock or (lambda: datetime.now(UTC))
        self.policy = policy or POLICY
        self.audit = audit
        self._lock = threading.Lock()

    # -- creation
    def request(self, kind: str, subject_id: str, requested_by_id: str) -> dict:
        """One open request per subject: asking again returns the open one (idempotent)."""
        if kind not in KINDS:
            raise DeletionError(f"unknown subject kind {kind!r}")
        key = f"{kind}:{subject_id}"
        rid = f"dr-{kind[:1]}-{subject_id}"[:36] + ""
        now = self.clock()
        row = {
            "subject_kind": kind,
            "subject_id": subject_id,
            "requested_by_id": requested_by_id,
            "status": "requested",
            "requested_at": _iso(now),
            "open_key": key,
        }
        for attempt in range(2):
            existing = self.repo.list(TABLE, [Query.equal("open_key", [key]), Query.limit(1)])
            if existing:
                return existing[0]
            try:
                return self.repo.create(
                    TABLE, f"{rid[:30]}-{attempt}{now.strftime('%y%m%d%H%M%S')}"[:36], row
                )
            except Conflict:
                continue
        raise DeletionError("could not create the request")

    def get(self, request_id: str) -> dict:
        return self.repo.get(TABLE, request_id)

    def _move(self, request_id: str, to: str, patch: dict[str, Any]) -> dict:
        with self._lock:
            row = self.repo.get(TABLE, request_id)
            if to not in ALLOWED.get(row["status"], set()):
                raise BadTransition(f"{row['status']} -> {to}")
            return self.repo.update(TABLE, request_id, {"status": to, **patch})

    def verify(self, request_id: str, method: str = "otp") -> dict:
        return self._move(
            request_id, "verified", {"verified_at": _iso(self.clock()), "verification": method[:24]}
        )

    def schedule(self, request_id: str) -> dict:
        """verified -> scheduled, `grace_days` from now. A hold blocks right away."""
        row = self.repo.get(TABLE, request_id)
        try:
            self._check_holds(row)
        except DeletionBlocked as b:
            return self._block(row, b)
        return self._move(
            request_id, "scheduled", {"scheduled_for_at": _iso(self.clock() + self.grace)}
        )

    def cancel(self, request_id: str) -> dict:
        return self._move(
            request_id, "cancelled", {"cancelled_at": _iso(self.clock()), "open_key": None}
        )

    def confirm(self, request_id: str) -> dict:
        return self._move(request_id, "confirmed", {"confirmed_at": _iso(self.clock())})

    def _block(self, row: dict, b: DeletionBlocked) -> dict:
        patch = {"status": "blocked", "blocked_reason": b.reason[:40], "open_key": None}
        return self.repo.update(TABLE, row["$id"], patch)

    def _check_holds(self, row: dict) -> None:
        hold = HoldChecker(self.repo, self.policy)
        for k, sid in _expand(self.repo, row["subject_kind"], row["subject_id"]):
            for table, rule in self.policy.items():
                if rule.dsr in (Dsr.NONE, Dsr.RETAIN):
                    continue
                rows = _subject_rows(self.repo, rule, k, sid)
                held = hold.held_ids(rule, rows) if rows else {}
                if held:
                    raise DeletionBlocked("hold", table, len(held))

    # -- execution
    def execute(self, request_id: str) -> dict:
        """scheduled -> executed (only once the grace period is over). Holds are re-checked by
        `execute_deletion` itself: blocked requests are closed and nothing is changed."""
        row = self.repo.get(TABLE, request_id)
        if row["status"] in ("executed", "confirmed"):
            return row  # already done: a repeat is a no-op
        if row["status"] != "scheduled":
            raise BadTransition(f"{row['status']} -> executed")
        due = datetime.fromisoformat(row["scheduled_for_at"])
        if self.clock() < due:
            raise DeletionError("grace period is not over")
        try:
            res = execute_deletion(
                self.repo,
                self.files,
                row["subject_kind"],
                row["subject_id"],
                policy=self.policy,
                clock=self.clock,
            )
        except DeletionBlocked as b:
            return self._block(row, b)
        out = self._move(
            request_id,
            "executed",
            {
                "executed_at": _iso(self.clock()),
                "result": json.dumps(res.as_dict()),
                "open_key": None,
            },
        )
        if self.audit is not None:
            self.audit.summary(
                key=f"deletion:{request_id}",
                action="deletion.execute",
                entity_id=request_id,
                detail=res.as_dict(),
            )
        log.info(
            "deletion executed",
            extra={
                "request_id": request_id,
                "kind": row["subject_kind"],
                "deleted": sum(res.deleted.values()),
                "anonymised": sum(res.anonymised.values()),
            },
        )
        return out

    def run_due(self, limit: int = 20) -> list[str]:
        """Execute every scheduled request whose grace period is over (cursor-paged)."""
        done, after = [], None
        now = self.clock()
        while len(done) < limit:
            q = [
                Query.equal("status", ["scheduled"]),
                Query.less_than_equal("scheduled_for_at", _iso(now)),
                Query.order_asc("$id"),
                Query.limit(PAGE),
            ]
            if after:
                q.append(Query.cursor_after(after))
            page = self.repo.list(TABLE, q)
            for r in page:
                if len(done) >= limit:
                    break
                self.execute(r["$id"])
                done.append(r["$id"])
            if len(page) < PAGE:
                break
            after = page[-1]["$id"]
        return done


def count_subject_rows(
    repo: Repository, kind: str, subject_id: str, policy: dict[str, Rule] | None = None
) -> dict[str, int]:
    """Rows of a subject per table (for C05's 'what we store' screen and for tests)."""
    pol = policy or POLICY
    out = {}
    for table in purge_order(pol):
        n = len(_subject_rows(repo, pol[table], kind, subject_id))
        if n:
            out[table] = n
    return out
