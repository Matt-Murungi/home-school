"""The Appwrite user side of sign-in: find or create the user for a phone, ensure the profile and
role label, issue a custom token (`users.createToken`) the app exchanges for a session.

Behind `UserDirectory` (fake for tests, `AppwriteUserDirectory` over the server SDK). The real
adapter has only been exercised with mocks: method names were read from the installed SDK.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from appwrite.permission import Permission
from appwrite.role import Role

from ..audit import bind_actor, client_ip_var
from ..jobs.idempotency import row_id
from ..logging_setup import request_id_var
from ..repository import Conflict, NotFound, Repository, RepositoryUnavailable
from .store import iso, parse

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


class DirectoryConflict(Exception):  # noqa: N818
    """A user with this phone (or ID) already exists."""


class DirectoryUnavailable(Exception):  # noqa: N818
    ...


@dataclass
class AuthUser:
    id: str
    phone: str
    labels: list[str] = field(default_factory=list)
    active: bool = True
    accessed_at: datetime | None = None


@dataclass(frozen=True)
class IssuedToken:
    secret: str
    expire: datetime


class UserDirectory(Protocol):
    def find_by_phone(self, phone: str) -> AuthUser | None: ...
    def create(self, user_id: str, phone: str) -> AuthUser: ...
    def add_label(self, user_id: str, label: str) -> AuthUser: ...
    def create_token(self, user_id: str, ttl_s: int) -> IssuedToken: ...


class FakeUserDirectory:
    def __init__(self, clock: Callable[[], datetime] | None = None) -> None:
        self._clock = clock or (lambda: datetime.now(UTC))
        self.users: dict[str, AuthUser] = {}
        self.tokens: list[tuple[str, str]] = []
        self.available = True
        self.race_on_create = False

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

    def get(self, user_id: str) -> AuthUser:
        return self.users[user_id]

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
    ) -> None:
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
            if not user.active:
                raise AccountBlocked
            dormant = self._dormant(user, is_new)
            grant = app not in user.labels
            if grant:
                user = self.users.add_label(user.id, app)
            self._ensure_profile(user, phone, app)
            if grant:
                self._record_grant(user.id, app)
            issued = self.users.create_token(user.id, self.token_ttl_s)
        except (DirectoryUnavailable, RepositoryUnavailable):
            raise AccountUnavailable from None
        if dormant:
            log.warning("sign-in to a dormant account", extra={"event": "otp_dormant_login"})
        return SignIn(user.id, issued, is_new, dormant)

    def _dormant(self, user: AuthUser, is_new: bool) -> bool:
        if is_new or user.accessed_at is None:
            return False
        return self.clock() - user.accessed_at > timedelta(days=self.dormant_days)

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

    def _record_grant(self, user_id: str, role: str) -> None:
        """M01 `role_assignment` is the grant history (`active_key`: one active grant per role)."""
        audited = self._audited(user_id, role)
        data = {
            "user_id": user_id,
            "role": role,
            "granted_at": iso(self.clock()),
            "active_key": f"{user_id}:{role}",
        }
        rid = row_id("role_assignment", user_id, role, "self")
        try:
            audited.create("role_assignment", rid, data)
        except Conflict:
            # Granted before. If that grant was revoked, this is a new grant: record it.
            if self.repo.get("role_assignment", rid).get("revoked_at"):
                try:
                    audited.create("role_assignment", uuid.uuid4().hex[:32], data)
                except Conflict:
                    pass
