"""The Appwrite user side of sign-in: find or create the user for a phone, ensure the profile and
role label, issue a custom token (`users.createToken`) the app exchanges for a session.

Behind `UserDirectory` (fake for tests, `AppwriteUserDirectory` over the server SDK). The real
adapter has only been exercised with mocks: method names were read from the installed SDK.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from appwrite.permission import Permission
from appwrite.role import Role

from ..audit import AuditContext, bind_actor, client_ip_var
from ..logging_setup import request_id_var
from ..repository import Conflict, NotFound, Repository, RepositoryUnavailable
from ..roles.directory import (
    AuthUser,
    DirectoryConflict,
    DirectoryUnavailable,
    UserDirectory,
)
from ..roles.model import STAFF_ROLES, clean_labels
from ..roles.service import SIGNUP, RevokedByStaff, RoleError, RoleService, TargetNotActive
from .store import parse

log = logging.getLogger("hs_core.auth_otp.accounts")

# Roles a phone sign-in may add by itself, chosen by which app called. They only open the app's
# own screens: access to children, addresses and other people's data is granted per user ID by the
# core service later (ADR 0012), and a teacher is not approved until verification (VER-1).
SELF_SERVICE_ROLES = ("parent", "teacher")


class AccountError(Exception): ...


class AccountUnavailable(AccountError):  # noqa: N818
    """Appwrite or the repository is down; the caller may retry."""


class AccountBlocked(AccountError):  # noqa: N818
    """The user is blocked or suspended."""


class AccountConflict(AccountError):  # noqa: N818
    """Data disagree (e.g. the phone is on another profile): needs a human."""


class AccountSuspended(AccountError):  # noqa: N818
    """The user (or, for the teacher app, the teacher profile) is suspended: the app shows a
    suspended state, not a sign-in error (A03)."""


class AccountStaff(AccountError):  # noqa: N818
    """Q27 default: a user holding any staff label signs in to the admin dashboard (MFA, SEC-4),
    never through the phone flow. No token, no label or profile side effects."""


class AccountRoleRevoked(AccountError):  # noqa: N818
    """Staff withdrew this app's role from the user; signing in does not give it back."""


@dataclass(frozen=True)
class IssuedToken:
    secret: str
    expire: datetime


class FakeUserDirectory:
    def __init__(self, clock: Callable[[], datetime] | None = None) -> None:
        self._clock = clock or (lambda: datetime.now(UTC))
        self.users: dict[str, AuthUser] = {}
        self.tokens: list[tuple[str, str]] = []
        self.available = True
        self.race_on_create = False
        self.sessions_ended: list[str] = []

    def _up(self) -> None:
        if not self.available:
            raise DirectoryUnavailable

    def add(
        self,
        phone: str,
        labels: list[str] | None = None,
        active: bool = True,
        accessed_days_ago: float | None = None,
    ) -> str:
        uid = uuid.uuid4().hex[:32]
        accessed = (
            self._clock() - timedelta(days=accessed_days_ago)
            if accessed_days_ago is not None
            else self._clock()
        )
        self.users[uid] = AuthUser(uid, phone, list(labels or []), active, accessed)
        return uid

    def get(self, user_id: str) -> AuthUser | None:
        self._up()
        u = self.users.get(user_id)
        return None if u is None else AuthUser(**{**u.__dict__, "labels": list(u.labels)})

    def change_phone(self, user_id: str, new_phone: str) -> None:
        self.users[user_id].phone = new_phone

    def find_by_phone(self, phone: str) -> AuthUser | None:
        self._up()
        return next((u for u in self.users.values() if u.phone == phone), None)

    def create(self, user_id: str, phone: str) -> AuthUser:
        self._up()
        if self.race_on_create:
            self.race_on_create = False
            self.add(phone)  # the competing request won
        if any(u.phone == phone for u in self.users.values()) or user_id in self.users:
            raise DirectoryConflict
        self.users[user_id] = AuthUser(user_id, phone, [], True, self._clock())
        return self.users[user_id]

    def add_label(self, user_id: str, label: str) -> AuthUser:
        self._up()
        u = self.users[user_id]
        if label not in u.labels:
            u.labels.append(label)
        return u

    def remove_label(self, user_id: str, label: str) -> AuthUser:
        self._up()
        u = self.users[user_id]
        u.labels[:] = [x for x in u.labels if x != label]
        return u

    def end_sessions(self, user_id: str) -> None:
        self._up()
        self.sessions_ended.append(user_id)

    def labelled(self, label: str, after: str | None, limit: int) -> list[AuthUser]:
        self._up()
        ids = sorted(i for i, u in self.users.items() if label in u.labels)
        ids = [i for i in ids if after is None or i > after][:limit]
        return [self.users[i] for i in ids]

    def create_token(self, user_id: str, ttl_s: int) -> IssuedToken:
        self._up()
        secret = uuid.uuid4().hex + uuid.uuid4().hex
        self.tokens.append((user_id, secret))
        return IssuedToken(secret, self._clock() + timedelta(seconds=ttl_s))


def _get(obj: Any, name: str, alt: str | None = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(alt or name, obj.get(name))
    return getattr(obj, name, None)


class AppwriteUserDirectory:
    """Appwrite server SDK `Users` service (API key). Mock-tested only."""

    def __init__(self, users: Any) -> None:
        self._users = users

    @classmethod
    def from_client(cls, client: Any) -> AppwriteUserDirectory:
        from appwrite.services.users import Users

        return cls(Users(client))

    def _wrap(self, fn, *a, **kw):
        from appwrite.exception import AppwriteException

        try:
            return fn(*a, **kw)
        except AppwriteException as e:
            if e.code == 409:
                raise DirectoryConflict from None
            raise DirectoryUnavailable from None
        except OSError:
            raise DirectoryUnavailable from None

    @staticmethod
    def _user(u: Any) -> AuthUser:
        accessed = _get(u, "accessed_at", "accessedAt")
        try:
            accessed_dt = parse(accessed) if accessed else None
        except ValueError:
            accessed_dt = None
        return AuthUser(
            id=_get(u, "id", "$id"),
            phone=_get(u, "phone") or "",
            labels=list(_get(u, "labels") or []),
            active=_get(u, "status") is not False,
            accessed_at=accessed_dt,
        )

    def find_by_phone(self, phone: str) -> AuthUser | None:
        # `search` covers name, email and phone; the exact comparison below keeps it safe.
        res = self._wrap(self._users.list, search=phone, total=False)
        rows = _get(res, "users") or []
        for r in rows:
            if _get(r, "phone") == phone:
                return self._user(r)
        return None

    def create(self, user_id: str, phone: str) -> AuthUser:
        u = self._wrap(self._users.create, user_id=user_id, phone=phone)
        # The number was just proven by SMS: mark it verified (best effort, not needed to sign in).
        try:
            self._wrap(
                self._users.update_phone_verification, user_id=user_id, phone_verification=True
            )
        except DirectoryUnavailable:
            log.warning("could not mark the phone as verified in Appwrite")
        return self._user(u)

    def add_label(self, user_id: str, label: str) -> AuthUser:
        u = self._wrap(self._users.get, user_id=user_id)
        labels = list(_get(u, "labels") or [])
        if label not in labels:
            u = self._wrap(self._users.update_labels, user_id=user_id, labels=[*labels, label])
        return self._user(u)

    def get(self, user_id: str) -> AuthUser | None:
        from appwrite.exception import AppwriteException

        try:
            return self._user(self._users.get(user_id=user_id))
        except AppwriteException as e:
            if e.code == 404:
                return None
            raise DirectoryUnavailable from None
        except OSError:
            raise DirectoryUnavailable from None

    def remove_label(self, user_id: str, label: str) -> AuthUser:
        u = self._wrap(self._users.get, user_id=user_id)
        labels = list(_get(u, "labels") or [])
        if label in labels:
            u = self._wrap(
                self._users.update_labels, user_id=user_id, labels=[x for x in labels if x != label]
            )
        return self._user(u)

    def end_sessions(self, user_id: str) -> None:
        """Deletes every session of the user (`users.delete_sessions`): a revoked staff user
        loses even a live admin session. Mock-tested only (owner-blocked, docs/ops/roles.md)."""
        self._wrap(self._users.delete_sessions, user_id=user_id)

    def labelled(self, label: str, after: str | None, limit: int) -> list[AuthUser]:
        """Users holding `label`, by ID, after the cursor. The `labels` filter on `users.list`
        is unverified against a real project (owner step, docs/ops/roles.md)."""
        from appwrite.query import Query

        q = [Query.equal("labels", [label]), Query.order_asc("$id"), Query.limit(limit)]
        if after:
            q.append(Query.cursor_after(after))
        res = self._wrap(self._users.list, queries=q, total=False)
        return [self._user(r) for r in (_get(res, "users") or [])]

    def create_token(self, user_id: str, ttl_s: int) -> IssuedToken:
        t = self._wrap(self._users.create_token, user_id=user_id, expire=ttl_s)
        secret = _get(t, "secret")
        expire = _get(t, "expire")
        if not secret or not expire:
            raise DirectoryUnavailable
        return IssuedToken(secret, parse(expire))


@dataclass(frozen=True)
class SignIn:
    user_id: str
    token: IssuedToken
    is_new_user: bool
    dormant: bool


class AccountService:
    def __init__(
        self,
        users: UserDirectory,
        repo: Repository,
        clock: Callable[[], datetime],
        dormant_days: int,
        token_ttl_s: int,
        roles: RoleService | None = None,
    ) -> None:
        self.roles = roles or RoleService(repo, users, clock)
        self.users, self.repo, self.clock = users, repo, clock
        self.dormant_days, self.token_ttl_s = dormant_days, token_ttl_s

    def sign_in(self, phone: str, app: str) -> SignIn:
        """Phone was proven by the caller. `app` is validated to a self-service role."""
        assert app in SELF_SERVICE_ROLES
        try:
            user = self.users.find_by_phone(phone)
            is_new = user is None
            if user is None:
                try:
                    user = self.users.create(uuid.uuid4().hex[:32], phone)
                except DirectoryConflict:  # lost a race to create the same number
                    user = self.users.find_by_phone(phone)
                    is_new = False
                    if user is None:
                        raise AccountUnavailable from None
            if clean_labels(user.labels) & set(STAFF_ROLES):
                raise AccountStaff  # Q27: before anything is written
            if not user.active:
                raise AccountBlocked
            self._check_status(user.id, app)
            dormant = self._dormant(user, is_new)
            self._ensure_profile(user, phone, app)
            try:
                # Idempotent: a no-op when the role is already held, repairs a half-done grant.
                # Audited as the user signing up (M06), through the role service.
                self.roles.grant_role(SIGNUP, user.id, app, ctx=self._ctx(user.id, app))
            except RevokedByStaff:
                raise AccountRoleRevoked from None
            except TargetNotActive:
                raise AccountBlocked from None
            except RoleError:
                raise AccountUnavailable from None
            issued = self.users.create_token(user.id, self.token_ttl_s)
        except (DirectoryUnavailable, RepositoryUnavailable):
            raise AccountUnavailable from None
        if dormant:
            log.warning("sign-in to a dormant account", extra={"event": "otp_dormant_login"})
        return SignIn(user.id, issued, is_new, dormant)

    def _check_status(self, user_id: str, app: str) -> None:
        """Suspended accounts get a suspended state in the app, not a login error (A03)."""
        try:
            profile = self.repo.get("user_profile", user_id)
        except NotFound:
            profile = None
        if profile is not None:
            if profile.get("deleted_at") or profile.get("status") == "deactivated":
                raise AccountBlocked
            if profile.get("status") == "suspended":
                raise AccountSuspended
        if app == "teacher":
            from appwrite.query import Query

            rows = self.repo.list(
                "teacher_profile", [Query.equal("user_id", [user_id]), Query.limit(5)]
            )
            if any(r.get("status") == "suspended" and not r.get("deleted_at") for r in rows):
                raise AccountSuspended

    def _dormant(self, user: AuthUser, is_new: bool) -> bool:
        if is_new or user.accessed_at is None:
            return False
        return self.clock() - user.accessed_at > timedelta(days=self.dormant_days)

    @staticmethod
    def _ctx(user_id: str, role: str) -> AuditContext:
        return AuditContext.for_principal(
            user_id, [role], request_id=request_id_var.get(), ip=client_ip_var.get()
        )

    def _audited(self, user_id: str, role: str):
        """M06: the writes below change audited tables; the actor is the user signing up."""
        return bind_actor(
            self.repo, user_id, [role], request_id=request_id_var.get(), ip=client_ip_var.get()
        )

    def _ensure_profile(self, user: AuthUser, phone: str, role: str) -> None:
        try:
            self.repo.get("user_profile", user.id)
            return
        except NotFound:
            pass
        audited = self._audited(user.id, role)
        try:
            audited.create(
                "user_profile",
                user.id,
                {"phone": phone, "status": "active"},
                [Permission.read(Role.user(user.id))],
            )
        except Conflict:
            # Either a concurrent sign-in made it, or another profile owns this phone.
            try:
                self.repo.get("user_profile", user.id)
            except NotFound:
                raise AccountConflict from None
