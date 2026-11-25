"""Business facts behind the permission rules, as one read-only object.

`PermissionContext` answers the questions the SR 6.2 matrix asks ("which teachers may read this
child now?", "which leads are assigned to an open case about this family?") from a `FactSource`.
All instants are timezone-aware UTC; Africa/Kampala is for display only. A `FactSource` is either
`StaticFacts` (dicts, used by pure tests and the drift script) or `RepoFacts` (the repository,
used by the service); the rules never touch the repository themselves.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from .policy import (
    CANCELLED_SESSION_STATUSES,
    DEFAULT_POLICY,
    GUARDIAN_SCOPES,
    HELD_SESSION_STATUSES,
    IMMEDIATE_ADDRESS_REVOKE_REASONS,
    Policy,
    clean_labels,
)
from .queries import rows_where


def parse_dt(value: Any) -> datetime | None:
    """ISO text -> aware UTC datetime. Naive input is read as UTC (documented, M02 follow-up)."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


class FactSource(Protocol):
    def row(self, table: str, row_id: str) -> dict | None: ...
    def rows(self, table: str, column: str, value: str) -> list[dict]: ...
    def labels(self, user_id: str) -> frozenset[str]: ...


class StaticFacts:
    """In-memory facts: `tables` maps table -> rows (with `$id`); `label_map` user -> labels."""

    def __init__(
        self, tables: dict[str, list[dict]], label_map: dict[str, list[str]] | None = None
    ):
        self._t = {t: {r["$id"]: r for r in rows} for t, rows in tables.items()}
        self._labels = label_map or {}

    def row(self, table, row_id):
        return self._t.get(table, {}).get(row_id)

    def rows(self, table, column, value):
        return [r for r in self._t.get(table, {}).values() if r.get(column) == value]

    def labels(self, user_id):
        return clean_labels(self._labels.get(user_id, []))


class LabelDirectory(Protocol):
    def labels_of(self, user_id: str) -> frozenset[str]: ...


class RoleAssignmentLabels:
    """Labels derived from the active `role_assignment` history rows (active_key set). Stand-in
    until the Appwrite Users adapter (labels are authoritative, M01) is wired; same answer when
    the core service keeps both in step."""

    def __init__(self, repo) -> None:
        self._repo = repo

    def labels_of(self, user_id: str) -> frozenset[str]:
        rows = rows_where(self._repo, "role_assignment", "user_id", [user_id])
        return clean_labels([r.get("role") for r in rows if r.get("active_key")])


class RepoFacts:
    """Facts read from the repository, memoised for the life of one object (one recompute)."""

    def __init__(self, repo, labels: LabelDirectory | None = None) -> None:
        from .repo_errors import NotFound

        self._repo = repo
        self._dir = labels or RoleAssignmentLabels(repo)
        self._rows: dict[tuple, dict | None] = {}
        self._lists: dict[tuple, list[dict]] = {}
        self._lab: dict[str, frozenset[str]] = {}
        self._nf = NotFound

    def row(self, table, row_id):
        key = (table, row_id)
        if key not in self._rows:
            try:
                self._rows[key] = self._repo.get(table, row_id)
            except self._nf:
                self._rows[key] = None
        return self._rows[key]

    def rows(self, table, column, value):
        key = (table, column, value)
        if key not in self._lists:
            self._lists[key] = rows_where(self._repo, table, column, [value])
        return self._lists[key]

    def labels(self, user_id):
        if user_id not in self._lab:
            self._lab[user_id] = clean_labels(self._dir.labels_of(user_id))
        return self._lab[user_id]


@dataclass(frozen=True)
class Access:
    child: bool = False  # child record, logs, handover, history of the child
    address: bool = False  # home address
    lapses_at: datetime | None = None  # earliest future instant at which `child` or `address` ends


NO_ACCESS = Access()


def _live(row: dict | None) -> bool:
    return row is not None and not row.get("deleted_at")


def parse_grants(text: Any) -> frozenset[str]:
    """guardian_link.grants: comma separated scope names, `all` = every scope. Unknown or empty
    = nothing (fail safe)."""
    if not isinstance(text, str):
        return frozenset()
    out: set[str] = set()
    for tok in text.split(","):
        tok = tok.strip()
        if tok == "all":
            out.update(GUARDIAN_SCOPES)
        elif tok in GUARDIAN_SCOPES:
            out.add(tok)
    return frozenset(out)


class PermissionContext:
    def __init__(self, source: FactSource, now: datetime, policy: Policy = DEFAULT_POLICY) -> None:
        if now.tzinfo is None:
            raise ValueError("now must be timezone-aware (UTC)")
        self.src = source
        self.now = now.astimezone(UTC)
        self.policy = policy
        self._memo: dict[tuple, Any] = {}

    def _m(self, key: tuple, fn):
        if key not in self._memo:
            self._memo[key] = fn()
        return self._memo[key]

    # ------------------------------------------------------------------ users
    def user_row(self, uid: str) -> dict | None:
        return self.src.row("user_profile", uid)

    def user_exists(self, uid: str | None) -> bool:
        """Account not deleted (own-account rows stay readable by the owner while suspended)."""
        return bool(uid) and _live(self.user_row(uid))  # type: ignore[arg-type]

    def user_active(self, uid: str | None) -> bool:
        if not self.user_exists(uid):
            return False
        return (self.user_row(uid) or {}).get("status", "active") == "active"  # type: ignore[arg-type]

    def entitled(self, uid: str | None, label: str) -> bool:
        """Active account that still holds the role's label (role withdrawn = no access)."""
        return self.user_active(uid) and label in self.src.labels(uid)  # type: ignore[arg-type]

    # ------------------------------------------------------------------ families
    def parent_row(self, parent_id: str) -> dict | None:
        r = self.src.row("parent_profile", parent_id)
        return r if _live(r) else None

    def parent_user(self, parent_id: str | None) -> str | None:
        r = self.parent_row(parent_id) if parent_id else None
        return r.get("user_id") if r else None

    def child_row(self, child_id: str) -> dict | None:
        r = self.src.row("child", child_id)
        return r if _live(r) else None

    def child_parent(self, child_id: str | None) -> str | None:
        r = self.child_row(child_id) if child_id else None
        return r.get("parent_id") if r else None

    def children_of(self, parent_id: str) -> list[dict]:
        return [r for r in self.src.rows("child", "parent_id", parent_id) if _live(r)]

    def family_users(self, parent_id: str | None, scope: str) -> frozenset[str]:
        """The parent plus every active guardian whose grants include `scope` (a guardian never
        gets more than the grants on the link). Role-based: needs the right label and an active
        account."""
        if not parent_id:
            return frozenset()

        def build() -> frozenset[str]:
            out: set[str] = set()
            owner = self.parent_user(parent_id)
            if owner and self.entitled(owner, "parent"):
                out.add(owner)
            for g in self.src.rows("guardian_link", "parent_id", parent_id):
                if not _live(g) or g.get("status") != "active" or g.get("revoked_at"):
                    continue
                if scope in parse_grants(g.get("grants")) and self.entitled(
                    g.get("user_id"), "guardian"
                ):
                    out.add(g["user_id"])
            return frozenset(out)

        return self._m(("family", parent_id, scope), build)

    def child_family_users(self, child_id: str | None, scope: str) -> frozenset[str]:
        return self.family_users(self.child_parent(child_id), scope)

    # ------------------------------------------------------------------ teachers
    def teacher_row(self, teacher_id: str) -> dict | None:
        r = self.src.row("teacher_profile", teacher_id)
        return r if _live(r) else None

    def teacher_user_raw(self, teacher_id: str | None) -> str | None:
        """The teacher's user id for own-account rows (earnings, documents): non-deleted account."""
        r = self.teacher_row(teacher_id) if teacher_id else None
        uid = r.get("user_id") if r else None
        return uid if uid and self.user_exists(uid) else None

    def teacher_user(self, teacher_id: str | None) -> str | None:
        """The teacher's user id only while they may act as a teacher: profile active, account
        active, `teacher` label held. Suspended or deactivated teachers lose business access."""
        r = self.teacher_row(teacher_id) if teacher_id else None
        if not r or r.get("status") != "active":
            return None
        uid = r.get("user_id")
        return uid if self.entitled(uid, "teacher") else None

    def assignments(self, child_id: str) -> list[dict]:
        return [a for a in self.src.rows("assignment", "child_id", child_id) if _live(a)]

    def sessions_of_child(self, child_id: str) -> list[dict]:
        return [s for s in self.src.rows("session", "child_id", child_id) if _live(s)]

    def access(self, teacher_id: str, child_id: str) -> Access:
        """What teacher `teacher_id` may read of child `child_id` at `now` (ADR 0012 rows for
        child, home_address, session_log; substitute window; 24 h address rule)."""
        key = ("access", teacher_id, child_id)
        if key in self._memo:
            return self._memo[key]
        res = self._access(teacher_id, child_id)
        self._memo[key] = res
        return res

    def _access(self, teacher_id: str, child_id: str) -> Access:
        if self.teacher_user(teacher_id) is None or self.child_row(child_id) is None:
            return NO_ACCESS
        now, pol = self.now, self.policy
        child = addr = False
        lapses: list[datetime] = []
        sessions = self.sessions_of_child(child_id)
        for a in self.assignments(child_id):
            if a.get("teacher_id") != teacher_id:
                continue
            end = parse_dt(a.get("end_at"))
            if end is None or end > now:
                child = addr = True
                if end is not None:
                    lapses.append(end)
                continue
            if a.get("end_reason") in IMMEDIATE_ADDRESS_REVOKE_REASONS:
                continue
            held = [
                parse_dt(s.get("scheduled_end_at"))
                for s in sessions
                if s.get("assignment_id") == a["$id"] and s.get("status") in HELD_SESSION_STATUSES
            ]
            held = [h for h in held if h is not None]
            if held:
                until = max(held) + pol.address_grace
                if now < until:
                    addr = True
                    lapses.append(until)
        for s in sessions:
            if (
                s.get("teacher_id") == teacher_id
                and s.get("is_substitute")
                and s.get("status") not in CANCELLED_SESSION_STATUSES
            ):
                end = parse_dt(s.get("scheduled_end_at"))
                if end is None:
                    continue
                until = end + pol.substitute_grace
                if now < until:
                    child = addr = True
                    lapses.append(until)
        return Access(child, addr, min((x for x in lapses if x > now), default=None))

    def candidate_teachers(self, child_id: str) -> frozenset[str]:
        ids = {a.get("teacher_id") for a in self.assignments(child_id)}
        ids |= {
            s.get("teacher_id") for s in self.sessions_of_child(child_id) if s.get("is_substitute")
        }
        return frozenset(i for i in ids if i)

    def teachers_for_child(self, child_id: str | None) -> frozenset[str]:
        """Teacher USER ids that may read the child record now."""
        if not child_id:
            return frozenset()
        return frozenset(
            u
            for t in self.candidate_teachers(child_id)
            if self.access(t, child_id).child and (u := self.teacher_user(t))
        )

    def teachers_for_address(self, parent_id: str | None) -> frozenset[str]:
        """Teacher USER ids that may read this family's home address now."""
        if not parent_id:
            return frozenset()
        out: set[str] = set()
        for c in self.children_of(parent_id):
            for t in self.candidate_teachers(c["$id"]):
                if self.access(t, c["$id"]).address and (u := self.teacher_user(t)):
                    out.add(u)
        return frozenset(out)

    def next_lapse(self, teacher_id: str, child_id: str) -> datetime | None:
        return self.access(teacher_id, child_id).lapses_at

    # ------------------------------------------------------------------ safeguarding
    @staticmethod
    def case_open(case: dict) -> bool:
        return case.get("status") != "closed"

    def _lead_of(self, case: dict) -> str | None:
        uid = case.get("assigned_to_id")
        return uid if uid and self.entitled(uid, "safeguardinglead") else None

    def case_lead(self, case: dict, *, closed_ok: bool = False) -> str | None:
        if not _live(case):
            return None
        if not closed_ok and not self.case_open(case):
            return None
        return self._lead_of(case)

    def case_leads(
        self,
        *,
        child_id: str | None = None,
        parent_id: str | None = None,
        teacher_id: str | None = None,
    ) -> frozenset[str]:
        """Assigned leads of OPEN cases that concern the child / family / teacher (ADR 0012
        scope rule S). Closed cases withdraw access to the related rows."""
        cases: list[dict] = []
        if child_id:
            cases += self.src.rows("safeguarding_case", "child_id", child_id)
        if parent_id:
            for c in self.children_of(parent_id):
                cases += self.src.rows("safeguarding_case", "child_id", c["$id"])
        if teacher_id:
            cases += self.src.rows("safeguarding_case", "teacher_id", teacher_id)
        return frozenset(u for c in cases if (u := self.case_lead(c)))

    def case_leads_for_user(self, user_id: str) -> frozenset[str]:
        """Leads of open cases in which this user is the reporter, the child's parent or the
        teacher concerned (the account rows a lead may read while the case is open)."""
        out: set[str] = set()
        for c in self.src.rows("safeguarding_case", "raised_by_id", user_id):
            if u := self.case_lead(c):
                out.add(u)
        for p in self.src.rows("parent_profile", "user_id", user_id):
            if _live(p):
                out |= self.case_leads(parent_id=p["$id"])
        for t in self.src.rows("teacher_profile", "user_id", user_id):
            if _live(t):
                out |= self.case_leads(teacher_id=t["$id"])
        return frozenset(out)

    # ------------------------------------------------------------------ lookups for derived tables
    def session_row(self, sid: str | None) -> dict | None:
        r = self.src.row("session", sid) if sid else None
        return r if _live(r) else None

    def session_child(self, sid: str | None) -> str | None:
        r = self.session_row(sid)
        return r.get("child_id") if r else None

    def log_child(self, log_id: str | None) -> str | None:
        r = self.src.row("session_log", log_id) if log_id else None
        return r.get("child_id") if _live(r) else None

    def assessment_child(self, aid: str | None) -> str | None:
        r = self.src.row("assessment", aid) if aid else None
        return r.get("child_id") if _live(r) else None

    def subscription_row(self, sid: str | None) -> dict | None:
        r = self.src.row("subscription", sid) if sid else None
        return r if _live(r) else None

    def catalogue_status(self, version_id: str | None) -> str | None:
        r = self.src.row("catalogue_version", version_id) if version_id else None
        return r.get("status") if _live(r) else None


__all__ = [
    "Access",
    "FactSource",
    "LabelDirectory",
    "PermissionContext",
    "RepoFacts",
    "RoleAssignmentLabels",
    "StaticFacts",
    "parse_dt",
    "parse_grants",
]
