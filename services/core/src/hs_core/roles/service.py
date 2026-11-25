"""Role changes: the ONLY code that sets or removes a role label (A03, #45, ADR 0012).

A role exists in two places: the Appwrite user label (authoritative for guards and for M05's
`label:<role>` permissions) and a `role_assignment` row (the grant history, unique active
(user, role) via `active_key`). Neither store has transactions, so the order is fixed and the
failure modes are known:

grant   1. audit intent + history row (one audited write, M06)  2. label  3. `role_changed` event
revoke  1. audit intent  2. close the history row  3. label removed  4. event   (non-staff)
        1. audit intent  2. label removed, sessions ended  3. close the row  4. event   (staff)

Every `role_assignment` write goes through the M06 `AuditedRepository` (audit-first, one audit row
per change; the staff label step runs inside that audited change via `before_apply`). The actor
comes from the verified principal or the job context, never from a body.

Crash after step 2 of a grant: row without label (the user holds less than recorded: safe). A
retry of the same call finishes it; `role-reconcile` completes non-staff grants and closes staff
ones (a grant that never returned success must not leave a staff role behind). Crash after step 2
of a revoke: row closed, label still set (over-grant until repaired): the caller got an error and
retries; `role-reconcile` removes the label. Label without any row: a staff label is only reported
(an owner-set bootstrap label must not vanish), a non-staff label gets its history row back.

Appwrite has no conditional update, so uniqueness of the active grant is a claim row whose id is
derived from (user, role, generation): a 409 means another writer took that generation. One
process serialises changes per user with a lock; across processes the claim rows and the
reconcile job keep the stores consistent.
"""

from __future__ import annotations

import hashlib
import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from appwrite.query import Query

from ..audit import AuditedRepository, AuditService, current_audit_context
from ..audit.context import AuditContext, short_id
from ..audit.errors import AuditContextMissing, AuditUnavailable, NotWritten, ReasonRequired
from ..audit.registry import REASON_REQUIRED
from ..audit.service import reason_is_enough
from ..jobs.idempotency import row_id
from ..repository import Conflict, NotFound, RepositoryError
from .directory import AuthUser, DirectoryUnavailable, UserDirectory
from .model import ROLES, STAFF_ROLES, clean_labels

log = logging.getLogger("hs_core.roles")
TABLE = "role_assignment"
MAX_ROWS = 500
SIGNUP_ROLES = ("parent", "teacher")  # what a phone sign-in may add by itself (A01)


class RoleError(Exception):
    code = "role_error"
    status = 400
    message = "Role change refused."

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or self.message)
        if message:
            self.message = message


class UnknownRole(RoleError):
    code, status, message = "unknown_role", 422, "Unknown role."


class Forbidden(RoleError):
    code, status, message = "forbidden", 403, "You may not make this role change."


class SelfChange(RoleError):
    code, status, message = "self_change", 403, "You cannot change your own roles."


class UnknownUser(RoleError):
    code, status, message = "unknown_user", 404, "No such user."


class TargetNotActive(RoleError):
    code, status, message = "target_not_active", 409, "That account is suspended or deleted."


class LastSuperadmin(RoleError):
    code, status, message = "last_superadmin", 409, "There must always be one super admin."


class RevokedByStaff(RoleError):
    code, status, message = "revoked_by_staff", 403, "This role was withdrawn by staff."


class Unavailable(RoleError):
    code, status, message = "unavailable", 503, "Role changes are unavailable. Try again."


class IncompleteChange(RoleError, NotWritten):
    """Recorded but not finished (e.g. the label call failed). Safe to repeat the same call.
    Also a `NotWritten`: raised inside an audited change before the row write it proves nothing
    was written, so the audit row is marked failed at once."""

    code, status = "role_change_incomplete", 503
    message = "The role change was only partly applied. Repeat the request."


@dataclass(frozen=True)
class Actor:
    """Who asks. A `user` actor's roles MUST come from a verified JWT (see `api.py`); a `system`
    actor is code inside the core service (sign-up, guardian link, reconcile job)."""

    kind: str
    id: str
    roles: frozenset[str] = field(default_factory=frozenset)

    @staticmethod
    def user(user_id: str, roles: Any) -> Actor:
        return Actor("user", user_id, clean_labels(list(roles or [])))

    @staticmethod
    def system(name: str) -> Actor:
        return Actor("system", name, frozenset())


SIGNUP = Actor.system("signup")


@dataclass(frozen=True)
class RoleChange:
    user_id: str
    role: str
    action: str  # grant | revoke
    changed: bool  # False = it already was so (idempotent no-op)
    repaired: bool = False  # a half-finished earlier change was completed
    roles: tuple[str, ...] = ()  # the user's roles afterwards


_STRIPES = 64


def _gen_id(user_id: str, role: str, n: int) -> str:
    return row_id(TABLE, user_id, role, "g", n)


class RoleService:
    def __init__(
        self,
        repo: Any,
        users: UserDirectory,
        clock: Callable[[], datetime] | None = None,
        permissions: Any | None = None,
    ) -> None:
        self.clock = clock or (lambda: datetime.now(UTC))
        if not isinstance(repo, AuditedRepository):  # unit tests with a bare repository
            repo = AuditedRepository(repo, AuditService(repo, clock=self.clock))
        self.repo, self.users = repo, users
        self.permissions = permissions  # PermissionService (M05): `.run(event, business_write)`
        self._locks = [threading.RLock() for _ in range(_STRIPES)]

    # ------------------------------------------------------------------ policy
    @staticmethod
    def check_grant(actor: Actor, user_id: str, role: str) -> None:
        """Who may grant what. Staff roles: a super admin only. guardian/parent/teacher: ops admin
        or super admin by hand, or the core service itself (sign-up grants parent/teacher; the
        guardian link grants guardian). Nobody grants themselves anything. System actors never
        grant staff roles (a bootstrap super admin is an owner action, docs/ops/roles.md)."""
        if actor.kind == "user" and actor.id == user_id:
            raise SelfChange
        if role in STAFF_ROLES:
            if actor.kind == "user" and "superadmin" in actor.roles:
                return
            raise Forbidden
        if actor.kind == "system":
            if actor.id == "signup" and role not in SIGNUP_ROLES:
                raise Forbidden
            return
        if actor.roles & {"opsadmin", "superadmin"}:
            return
        raise Forbidden

    @staticmethod
    def check_revoke(actor: Actor, user_id: str, role: str) -> None:
        if actor.kind == "system":
            return
        if role in STAFF_ROLES:
            if actor.id == user_id:
                raise SelfChange("Ask another super admin to change your staff roles.")
            if "superadmin" not in actor.roles:
                raise Forbidden
            return
        if actor.id == user_id:
            return  # leaving a parent/teacher/guardian role of your own is allowed
        if not actor.roles & {"opsadmin", "superadmin"}:
            raise Forbidden

    # ------------------------------------------------------------------ helpers
    def _lock(self, user_id: str) -> threading.RLock:
        h = int.from_bytes(hashlib.sha256(user_id.encode()).digest()[:4], "big")
        return self._locks[h % _STRIPES]

    @staticmethod
    def _known(role: Any) -> str:
        if not isinstance(role, str) or role not in ROLES:
            raise UnknownRole
        return role

    @staticmethod
    def _id(user_id: Any) -> str:
        if not isinstance(user_id, str) or not user_id or len(user_id) > 36:
            raise UnknownUser
        return user_id

    def _rows(self, user_id: str, role: str) -> list[dict]:
        rows = self.repo.list(
            TABLE,
            [
                Query.equal("user_id", [user_id]),
                Query.equal("role", [role]),
                Query.limit(MAX_ROWS),
            ],
        )
        return sorted(rows, key=lambda r: (str(r.get("granted_at") or ""), r["$id"]))

    def _active_rows(self, role: str) -> list[dict]:
        rows = self.repo.list(TABLE, [Query.equal("role", [role]), Query.limit(MAX_ROWS)])
        return [r for r in rows if r.get("active_key")]

    def roles_of(self, user_id: str) -> tuple[str, ...]:
        """Roles per the Appwrite label (authoritative), for the result of a change."""
        u = self.users.get(user_id)
        return tuple(sorted(clean_labels(u.labels))) if u else ()

    def _target(self, user_id: str) -> AuthUser | None:
        return self.users.get(user_id)

    def _profile_state(self, user_id: str) -> str:
        """active | suspended | deleted | none, from the `user_profile` row (id = user id)."""
        try:
            p = self.repo.get("user_profile", user_id)
        except NotFound:
            return "none"
        if p.get("deleted_at"):
            return "deleted"
        return "active" if p.get("status", "active") == "active" else str(p.get("status"))

    @staticmethod
    def _action(kind: str, role: str) -> str:
        """`role.grant` / `role.revoke`; staff roles use `_staff` (a reason is required)."""
        return f"role.{kind}_staff" if role in STAFF_ROLES else f"role.{kind}"

    @staticmethod
    def _need_reason(kind: str, role: str, reason: str) -> None:
        action = RoleService._action(kind, role)
        if action in REASON_REQUIRED and not reason_is_enough(reason):
            raise ReasonRequired(action)

    @staticmethod
    def _ctx(ctx: AuditContext | None) -> AuditContext:
        found = ctx or current_audit_context.get()
        if found is None:
            raise AuditContextMissing("a role change needs an audit context")
        return found

    def _refuse(self, ctx, user_id, role: str, kind: str, err: RoleError):
        """A refusal is audited best effort (one `role.<kind>_refused` row) and re-raised."""
        try:
            self.repo.service.record(
                ctx,
                action=f"role.{kind}_refused",
                entity=TABLE,
                entity_id=short_id(f"{user_id}:{role}"[:100]),
                op="refused",
                after={"code": err.code, "role": str(role)[:40]},
            )
        except Exception:  # noqa: BLE001
            log.warning("could not audit a refused role change")
        log.info(
            "role change refused", extra={"code": err.code, "kind": kind, "role": str(role)[:40]}
        )
        raise err

    def _label(self, fn: Callable[[], Any], what: str) -> None:
        try:
            fn()
        except DirectoryUnavailable:
            log.warning("role label call failed", extra={"step": what})
            raise IncompleteChange from None

    def _event(self, event_id: str, user_id: str, write: Callable[[], Any]) -> None:
        """Label write inside the M05 order (enqueue retry, write, recompute permissions)."""
        if self.permissions is None:
            write()
            return
        from ..permissions.service import role_changed

        self.permissions.run(role_changed(event_id, user_id), write)

    # ------------------------------------------------------------------ grant
    def grant_role(
        self,
        actor: Actor,
        user_id: str,
        role: str,
        *,
        reason: str = "",
        ctx: AuditContext | None = None,
    ) -> RoleChange:
        ctx = self._ctx(ctx)
        try:
            self._id(user_id)
            self._known(role)
            self.check_grant(actor, user_id, role)
        except RoleError as e:
            self._refuse(ctx, user_id, role, "grant", e)
        self._need_reason("grant", role, reason)
        try:
            with self._lock(user_id):
                return self._grant(actor, user_id, role, reason, ctx)
        except RoleError:
            raise
        except (RepositoryError, AuditUnavailable, DirectoryUnavailable):
            raise Unavailable from None

    def _grant(self, actor, user_id: str, role: str, reason: str, ctx) -> RoleChange:
        target = self._target(user_id)
        if target is None:
            self._refuse(ctx, user_id, role, "grant", UnknownUser())
        state = self._profile_state(user_id)
        if not target.active or state in ("suspended", "deleted", "deactivated"):
            self._refuse(ctx, user_id, role, "grant", TargetNotActive())
        rows = self._rows(user_id, role)
        active = next((r for r in rows if r.get("active_key")), None)
        action = self._action("grant", role)
        if active is not None:
            n = len(rows)
            if role in target.labels:
                return RoleChange(user_id, role, "grant", False, roles=self._now_roles(target))
            # The row exists, the label does not: finish the grant. One audit row for the repair.
            self.repo.service.record(
                ctx,
                action=action,
                entity=TABLE,
                entity_id=active["$id"],
                op="repair",
                after={"user_id": user_id, "role": role, "repair": "label"},
                reason=reason,
            )
            self._finish_grant(user_id, role, n)
            return RoleChange(user_id, role, "grant", True, True, self.roles_of(user_id))
        if actor.kind == "system" and actor.id == "signup" and rows:
            last = rows[-1]
            if str(last.get("revoke_reason") or "").startswith("staff:"):
                self._refuse(ctx, user_id, role, "grant", RevokedByStaff())
        n = len(rows) + 1
        if self._claim_row(actor, user_id, role, n, reason, ctx) is None:
            return self._grant(actor, user_id, role, reason, ctx)  # another writer took it
        self._finish_grant(user_id, role, n)
        return RoleChange(user_id, role, "grant", True, False, self.roles_of(user_id))

    @staticmethod
    def _now_roles(target: AuthUser) -> tuple[str, ...]:
        return tuple(sorted(clean_labels(target.labels)))

    def _claim_row(self, actor, user_id: str, role: str, n: int, reason: str, ctx):
        data = {
            "user_id": user_id,
            "role": role,
            "granted_by_id": actor.id if actor.kind == "user" else None,
            "granted_at": self.clock().isoformat(),
            "active_key": f"{user_id}:{role}",
        }
        rid = _gen_id(user_id, role, n)
        audited = self.repo.bind(ctx)
        try:
            audited.create(
                TABLE, rid, data, ctx=ctx, action=self._action("grant", role), reason=reason
            )
        except Conflict:
            return None
        return rid

    def _finish_grant(self, user_id: str, role: str, n: int) -> None:
        key = f"role:{user_id}:{role}:{n}:grant"
        self._event(
            key, user_id, lambda: self._label(lambda: self.users.add_label(user_id, role), "add")
        )

    def _end_sessions(self, user_id: str) -> None:
        """Defence in depth for a revoked staff user (the label is already gone): end their
        Appwrite sessions. A failure is logged, not fatal: access has already ended."""
        try:
            self.users.end_sessions(user_id)
        except DirectoryUnavailable:
            log.warning("could not end the sessions of a revoked staff user")

    # ------------------------------------------------------------------ revoke
    def revoke_role(
        self,
        actor: Actor,
        user_id: str,
        role: str,
        *,
        reason: str = "",
        ctx: AuditContext | None = None,
    ) -> RoleChange:
        ctx = self._ctx(ctx)
        try:
            self._id(user_id)
            self._known(role)
            self.check_revoke(actor, user_id, role)
        except RoleError as e:
            self._refuse(ctx, user_id, role, "revoke", e)
        self._need_reason("revoke", role, reason)
        try:
            with self._lock(user_id):
                return self._revoke(actor, user_id, role, reason, ctx)
        except RoleError:
            raise
        except (RepositoryError, AuditUnavailable, DirectoryUnavailable):
            raise Unavailable from None

    def _revoke(self, actor, user_id: str, role: str, reason: str, ctx) -> RoleChange:
        rows = self._rows(user_id, role)
        active = next((r for r in rows if r.get("active_key")), None)
        target = self._target(user_id)
        label_set = bool(target and role in clean_labels(target.labels))
        n = len(rows)
        if active is None and not label_set:
            return RoleChange(user_id, role, "revoke", False, roles=self.roles_of(user_id))
        if role == "superadmin" and self._is_last_superadmin(user_id, active, label_set):
            self._refuse(ctx, user_id, role, "revoke", LastSuperadmin())
        key = f"role:{user_id}:{role}:{max(n, 1)}:revoke"
        action = self._action("revoke", role)
        staff = role in STAFF_ROLES

        def staff_access_ends_first() -> None:
            # Core guards read the label live, so removing it is what ends admin access. A crash
            # after this leaves an open row with no label, which a repeat of the call (or
            # role-reconcile) closes; never a closed row with the label still set.
            if target is not None:
                self._label(lambda: self.users.remove_label(user_id, role), "remove")
                self._end_sessions(user_id)

        audited = self.repo.bind(ctx)
        if active is not None:
            prefix = (
                "staff"
                if actor.kind == "user" and actor.id != user_id
                else ("self" if actor.kind == "user" else "system")
            )
            audited.update(
                TABLE,
                active["$id"],
                {
                    "revoked_at": self.clock().isoformat(),
                    "active_key": None,
                    "revoke_reason": f"{prefix}:{actor.id}:{reason}"[:200],
                },
                ctx=ctx,
                action=action,
                reason=reason,
                before_apply=staff_access_ends_first if staff else None,
            )
        else:  # a label with no row: nothing to close; one audit row, then the label goes
            self.repo.service.record(
                ctx,
                action=action,
                entity=TABLE,
                entity_id=short_id(f"{user_id}:{role}"),
                op="action",
                after={"user_id": user_id, "role": role, "label_only": True},
                reason=reason,
            )
            if staff:
                staff_access_ends_first()
        if not staff and active is not None and target is not None:
            # Non-staff: row first (label-first would let reconcile re-grant the label).
            self._event(
                key,
                user_id,
                lambda: self._label(lambda: self.users.remove_label(user_id, role), "remove"),
            )
        else:
            if not staff and target is not None:  # a non-staff label with no row
                self._label(lambda: self.users.remove_label(user_id, role), "remove")
            self._event(key, user_id, lambda: None)  # recompute after row AND label are gone
        if role == "superadmin" and not self._active_rows("superadmin"):
            # Two super admins revoked each other at once: put this one back.
            self._restore_superadmin(user_id, ctx)
            raise LastSuperadmin
        return RoleChange(user_id, role, "revoke", True, active is None, self.roles_of(user_id))

    def _is_last_superadmin(self, user_id: str, active: dict | None, label_set: bool) -> bool:
        others = [r for r in self._active_rows("superadmin") if r.get("user_id") != user_id]
        return not others

    def _restore_superadmin(self, user_id: str, ctx) -> None:
        rows = self._rows(user_id, "superadmin")
        rid = _gen_id(user_id, "superadmin", len(rows) + 1)
        audited = self.repo.bind(ctx)
        try:
            audited.create(
                TABLE,
                rid,
                {
                    "user_id": user_id,
                    "role": "superadmin",
                    "granted_at": self.clock().isoformat(),
                    "active_key": f"{user_id}:superadmin",
                },
                ctx=ctx,
                action="role.grant_staff",
                reason="restored: the last super admin cannot be revoked",
            )
        except Conflict:
            pass
        self.users.add_label(user_id, "superadmin")

    # ------------------------------------------------------------------ backfill used by reconcile
    def backfill_row(
        self, user_id: str, role: str, reason: str, ctx: AuditContext | None = None
    ) -> bool:
        """A label that has no history row: write the row (non-staff roles only)."""
        ctx = self._ctx(ctx)
        with self._lock(user_id):
            rows = self._rows(user_id, role)
            if any(r.get("active_key") for r in rows):
                return False
            n = len(rows) + 1
            audited = self.repo.bind(ctx)
            try:
                audited.create(
                    TABLE,
                    _gen_id(user_id, role, n),
                    {
                        "user_id": user_id,
                        "role": role,
                        "granted_at": self.clock().isoformat(),
                        "revoke_reason": None,
                        "active_key": f"{user_id}:{role}",
                    },
                    ctx=ctx,
                    action=self._action("grant", role),
                    reason=reason,
                )
            except Conflict:
                return False
            return True


__all__ = [
    "Actor",
    "Forbidden",
    "IncompleteChange",
    "LastSuperadmin",
    "RevokedByStaff",
    "RoleChange",
    "RoleError",
    "RoleService",
    "SIGNUP",
    "SelfChange",
    "TargetNotActive",
    "Unavailable",
    "UnknownRole",
    "UnknownUser",
]
