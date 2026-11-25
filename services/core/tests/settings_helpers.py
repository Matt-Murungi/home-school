"""Shared fakes for the H05 settings tests. No test reads the wall clock: every service gets the
ManualClock (CONTRIBUTING.md "Tests must not read the wall clock")."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from audit_helpers import FaultyRepository, StepClock, build
from hs_core.app_settings import REGISTRY, AppSettings
from hs_core.audit import AuditContext
from hs_core.audit.recover import recover_audit  # noqa: F401  (re-exported for tests)

# 08:00 UTC = 11:00 in Kampala; the next Kampala midnight is 2027-03-01 21:00 UTC.
T0 = datetime(2027, 3, 1, 8, 0, tzinfo=UTC)
NEXT_MIDNIGHT = datetime(2027, 3, 1, 21, 0, tzinfo=UTC)

LATE = "session.late_cancellation_notice_hours"  # next_day, default 24
AUTO = "session.auto_confirm_after_hours"  # immediate, default 48
GRACE = "billing.grace_days"  # next_day, default 3
CANCEL = "billing.past_due_cancel_days"  # next_day, default 14
WEIGHTS = "matching.weights"  # immediate map, sum 100
QUIET = "notify.quiet_hours"
RATE = "notify.rate_limit"
REASON = "Owner decision recorded in the ops meeting"


class ManualClock:
    """Stands still until `advance`."""

    def __init__(self, start: datetime = T0) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kw) -> None:
        self.now += timedelta(**kw)

    def set(self, t: datetime) -> None:
        self.now = t


def superadmin(request_id: str = "req-1", actor: str = "u_super") -> AuditContext:
    return AuditContext.for_principal(
        actor, ["superadmin"], request_id=request_id, ip="203.0.113.9"
    )


def stack(repo=None, clock=None, defaults=None, ttl_s=30.0, spec=REGISTRY, events=None):
    """-> (raw repo, AppSettings, audited repo, clock). The audit clock steps; the settings clock
    is manual."""
    clock = clock or ManualClock()
    raw, _service, audited, _ = build(repo if repo is not None else FaultyRepository(), StepClock())
    svc = AppSettings(
        audited, spec=spec, defaults=defaults, clock=clock, ttl_s=ttl_s,
        on_event=(lambda n, i: events.append((n, i))) if events is not None else None,
    )  # fmt: skip
    return raw, svc, audited, clock


_n = [0]


def ctx(name: str = "u_super") -> AuditContext:
    """A fresh request key per call (one context = one request)."""
    _n[0] += 1
    return superadmin(f"req-{_n[0]}", name)


def edit(svc, key, value, rev, **kw):
    kw.setdefault("reason", REASON)
    kw.setdefault("ctx", ctx())
    return svc.change(key, value, expected_revision=rev, **kw)


def audit_actions(raw):
    out = []
    for r in sorted(raw.list("audit_log"), key=lambda r: (r["occurred_at"], r["$id"])):
        meta = json.loads(r["after"])["meta"]
        out.append((r["entity"], r["action"], meta["status"], meta.get("op")))
    return out


def history(raw, key):
    return sorted(
        (r for r in raw.list("app_setting_change") if r["setting_key"] == key),
        key=lambda r: r["revision"],
    )


def value_row(raw, key):
    rows = [r for r in raw.list("app_setting") if r["setting_key"] == key]
    return rows[0] if rows else None
