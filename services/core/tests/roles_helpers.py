"""Builders for the A03 role tests: a faked stack and crash/fault injection wrappers."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from hs_core.audit.context import AuditContext
from hs_core.audit.service import meta_of
from hs_core.auth_otp.accounts import FakeUserDirectory
from hs_core.repository import InMemoryRepository, RepositoryUnavailable
from hs_core.roles.service import Actor, RoleService

T0 = datetime(2026, 11, 25, 9, 0, 0, tzinfo=UTC)


class Clock:
    def __init__(self, now: datetime = T0) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


class Boom(BaseException):  # noqa: N818
    """Simulated process death. A BaseException so that no `except Exception` (e.g. the
    reconcile per-row isolation) can swallow it: a dead process runs no handlers."""


class Tick:
    """One write counter shared by the repository and the label directory, so a test can kill
    the 'process' before any single write of a multi-store operation."""

    def __init__(self) -> None:
        self.n, self.crash_at = 0, None

    def hit(self) -> None:
        self.n += 1
        if self.crash_at is not None and self.n == self.crash_at:
            raise Boom


class CountingRepo:
    def __init__(self, inner, tick: Tick) -> None:
        self.inner, self.tick = inner, tick
        self.fail_audit = False

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def create(self, *a, **k):
        if self.fail_audit and a and a[0] == "audit_log":
            raise RepositoryUnavailable("audit store down")
        self.tick.hit()
        return self.inner.create(*a, **k)

    def update(self, *a, **k):
        self.tick.hit()
        return self.inner.update(*a, **k)

    def delete(self, *a, **k):
        self.tick.hit()
        return self.inner.delete(*a, **k)


class CountingDirectory:
    def __init__(self, inner, tick: Tick) -> None:
        self.inner, self.tick = inner, tick
        self.jitter = 0.0  # seconds to sleep inside a label write: widens race windows

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def _pause(self):
        if self.jitter:
            import random
            import time

            time.sleep(random.random() * self.jitter)

    def end_sessions(self, *a, **k):
        self.tick.hit()
        return self.inner.end_sessions(*a, **k)

    def add_label(self, *a, **k):
        self.tick.hit()
        self._pause()
        return self.inner.add_label(*a, **k)

    def remove_label(self, *a, **k):
        self.tick.hit()
        self._pause()
        return self.inner.remove_label(*a, **k)


class CtxService:
    """Calls the role service the way the app does: one fresh AuditContext per call (a request
    has a server-generated key), the actor taken from the Actor (the verified principal)."""

    def __init__(self, real) -> None:
        object.__setattr__(self, "_real", real)

    def __getattr__(self, name):
        return getattr(self._real, name)

    def __setattr__(self, name, value):
        setattr(self._real, name, value)

    @staticmethod
    def ctx_for(actor) -> AuditContext:
        import uuid

        key = uuid.uuid4().hex
        if actor.kind == "system":
            return AuditContext.system(actor.id, run_key=key)
        return AuditContext.for_principal(actor.id, sorted(actor.roles), request_id=key)

    def grant_role(self, actor, user_id, role, *, reason="", ctx=None):
        return self._real.grant_role(
            actor, user_id, role, reason=reason, ctx=ctx or self.ctx_for(actor)
        )

    def revoke_role(self, actor, user_id, role, *, reason="", ctx=None):
        return self._real.revoke_role(
            actor, user_id, role, reason=reason, ctx=ctx or self.ctx_for(actor)
        )


class AuditView:
    """The audit_log rows the M06 service wrote, in a shape tests can assert on."""

    def __init__(self, raw) -> None:
        self._raw = raw

    def rows(self):
        return self._raw.list("audit_log")

    def intents(self):
        return [r for r in self.rows() if meta_of(r).get("status") in ("intent", "recorded")]

    def role_intents(self):
        return [r for r in self.intents() if r["entity"] == "role_assignment"]

    def outcomes(self):
        return [r for r in self.rows() if meta_of(r).get("status") in ("failed", "not_applied")]


class Env:
    def __init__(self) -> None:
        self.clock = Clock()
        self.raw_repo = InMemoryRepository()
        self.tick = Tick()
        self.repo = CountingRepo(self.raw_repo, self.tick)
        self.raw_users = FakeUserDirectory(clock=self.clock)
        self.users = CountingDirectory(self.raw_users, self.tick)
        self.real_service = RoleService(self.repo, self.users, clock=self.clock)
        self.service = CtxService(self.real_service)
        self.audit = AuditView(self.raw_repo)
        self.events: list = []

    def user(self, labels=(), active=True, profile_status="active", deleted=False) -> str:
        uid = self.raw_users.add(f"+2567{len(self.raw_users.users):08d}", list(labels), active)
        data = {"phone": "x", "status": profile_status}
        if deleted:
            data["deleted_at"] = self.clock().isoformat()
        self.raw_repo.create("user_profile", uid, data)
        return uid

    def superadmin(self) -> tuple[str, Actor]:
        uid = self.user(["superadmin"])
        self.raw_repo.create(
            "role_assignment",
            f"seed{uid}"[:36],
            {
                "user_id": uid,
                "role": "superadmin",
                "granted_at": "2026-01-01T00:00:00+00:00",
                "active_key": f"{uid}:superadmin",
            },
        )
        return uid, Actor.user(uid, ["superadmin"])

    def labels(self, uid: str) -> set[str]:
        return set(self.raw_users.users[uid].labels)

    def active_rows(self, uid: str, role: str | None = None) -> list[dict]:
        rows = self.raw_repo.list("role_assignment")
        return [
            r
            for r in rows
            if r["user_id"] == uid and r.get("active_key") and (role is None or r["role"] == role)
        ]

    def rows(self, uid: str, role: str) -> list[dict]:
        return [
            r
            for r in self.raw_repo.list("role_assignment")
            if r["user_id"] == uid and r["role"] == role
        ]


OPS = lambda uid="ops1": Actor.user(uid, ["opsadmin"])  # noqa: E731
