"""Legal holds (C03 #141): nothing under a hold is purged or anonymised.

A hold is a `retention_hold` row (subject kind + id, active while `revoked_at` is null), or one of
two IMPLICIT holds that need no row: (a) an OPEN safeguarding case holds its child and its teacher;
(b) a row inherits the holds of the parent rows it references (`Link.inherit_hold`), so a hold on a
session log also holds its topics, and a hold on a child holds everything of the child.

Fail closed: if a hold lookup cannot be answered, `HoldLookupFailed` propagates and the caller
deletes NOTHING in that chunk. Residual race: a hold placed in the milliseconds between the final
re-check and the delete is not seen (documented in docs/ops/retention.md; the quarantine window is
the second line of defence for soft tables).

Safeguarding content is not read here: only ids and the status of case rows (`Query.select`), the
reason being that retention must know a case is open; the case text is never loaded.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from appwrite.query import Query

from ..repository import NotFound, Repository
from .policy import OWN_SUBJECT, POLICY, Rule

HOLD_TABLE = "retention_hold"
CASE_TABLE = "safeguarding_case"  # not a literal at the call sites: see module docstring
OPEN_CASE = ("open", "acknowledged", "investigating", "escalated")
BATCH = 50  # ids per `equal` query (Appwrite allows 100 values; stay well inside)
MAX_DEPTH = 4


class HoldLookupFailed(Exception):
    """A hold could not be checked; nothing may be deleted on the strength of that."""


def row_ref(table: str, row_id: str) -> str:
    return f"{table}:{row_id}"


class HoldChecker:
    def __init__(self, repo: Repository, policy: dict[str, Rule] | None = None) -> None:
        self._repo = repo
        self._policy = policy or POLICY

    # ------------------------------------------------------------------ subjects of a row
    def subjects_of(self, rule: Rule, row: dict, depth: int = 0) -> set[tuple[str, str]]:
        out: set[tuple[str, str]] = {("row", row_ref(rule.table, row["$id"]))}
        kind = OWN_SUBJECT.get(rule.table)
        if kind:
            out.add((kind, row["$id"]))
        for col, k in rule.subjects.items():
            v = row.get(col)
            if isinstance(v, str) and v:
                out.add((k, v))
        if depth < MAX_DEPTH:
            for link in rule.links:
                v = row.get(link.column)
                parent_rule = self._policy.get(link.parent)
                if not (link.inherit_hold and isinstance(v, str) and v and parent_rule):
                    continue
                if v.startswith("anon-"):  # a tombstone references nothing
                    continue
                own = OWN_SUBJECT.get(link.parent)
                out.add((own, v) if own else ("row", row_ref(link.parent, v)))
                if parent_rule.links or parent_rule.subjects:  # the parent's own holds
                    out |= self._parent_subjects(parent_rule, v, depth + 1)
        return out

    def _parent_subjects(self, rule: Rule, row_id: str, depth: int) -> set[tuple[str, str]]:
        try:
            parent = self._repo.get(rule.table, row_id)
        except NotFound:
            return set()  # a dangling reference inherits nothing
        except Exception as exc:  # noqa: BLE001 - fail closed
            raise HoldLookupFailed(type(exc).__name__) from None
        return self.subjects_of(rule, parent, depth)

    # ------------------------------------------------------------------ the check
    def held_ids(self, rule: Rule, rows: Iterable[dict]) -> dict[str, str]:
        """row id -> reason code for every row of `rows` that is under a hold."""
        rows = list(rows)
        if not rows:
            return {}
        per_row = {r["$id"]: self.subjects_of(rule, r) for r in rows}
        wanted = {s for subs in per_row.values() for s in subs}
        try:
            explicit = self._explicit(wanted)
            implicit = self._open_cases(wanted)
        except HoldLookupFailed:
            raise
        except Exception as exc:  # noqa: BLE001 - fail closed on ANY lookup problem
            raise HoldLookupFailed(type(exc).__name__) from None
        active = explicit | implicit
        out = {}
        for rid, subs in per_row.items():
            hit = sorted(subs & active)
            if hit:
                out[rid] = f"hold:{hit[0][0]}"
        return out

    def _explicit(self, wanted: set[tuple[str, str]]) -> set[tuple[str, str]]:
        ids = sorted({sid for _, sid in wanted})
        found: set[tuple[str, str]] = set()
        for i in range(0, len(ids), BATCH):
            chunk = ids[i : i + BATCH]
            after = None
            while True:
                q = [
                    Query.equal("subject_id", chunk),
                    Query.is_null("revoked_at"),
                    Query.order_asc("$id"),
                    Query.limit(100),
                ] + ([Query.cursor_after(after)] if after else [])
                page = self._repo.list(HOLD_TABLE, q)
                for h in page:
                    if not h.get("revoked_at"):
                        found.add((h["subject_kind"], h["subject_id"]))
                if len(page) < 100:
                    break
                after = page[-1]["$id"]
        return found & wanted

    def _open_cases(self, wanted: set[tuple[str, str]]) -> set[tuple[str, str]]:
        """child and teacher subjects of an open safeguarding case."""
        found: set[tuple[str, str]] = set()
        for kind, col in (("child", "child_id"), ("teacher", "teacher_id")):
            ids = sorted({sid for k, sid in wanted if k == kind})
            for i in range(0, len(ids), BATCH):
                chunk = ids[i : i + BATCH]
                after = None
                while True:
                    q = [
                        Query.equal(col, chunk),
                        Query.equal("status", list(OPEN_CASE)),
                        Query.select(["$id", "child_id", "teacher_id", "status"]),
                        Query.order_asc("$id"),
                        Query.limit(100),
                    ] + ([Query.cursor_after(after)] if after else [])
                    page = self._repo.list(CASE_TABLE, q)
                    for c in page:
                        if c.get(col):
                            found.add((kind, c[col]))
                    if len(page) < 100:
                        break
                    after = page[-1]["$id"]
        return found

    def is_held(self, rule: Rule, row: dict[str, Any]) -> bool:
        return row["$id"] in self.held_ids(rule, [row])
