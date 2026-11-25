"""Shared fixtures for the billing tests."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from hs_core.audit import AuditContext, AuditedRepository, AuditService
from hs_core.billing.fakes import (
    FakeLedger,
    FakeNotifier,
    FakePlans,
    FakeScheduler,
)
from hs_core.billing.models import PlanInfo
from hs_core.billing.service import BillingConfig, BillingService
from hs_core.repository import InMemoryRepository

# 2027-01-15 10:00 Kampala
T0 = datetime(2027, 1, 15, 7, 0, tzinfo=UTC)
P2 = PlanInfo("plan2", price_ugx=240_000, sessions_per_week=2)
P3 = PlanInfo("plan3", price_ugx=330_000, sessions_per_week=3)


@dataclass
class Rig:
    repo: InMemoryRepository  # the RAW repository (tests read and simulate crashes on it)
    ledger: FakeLedger
    audit: AuditService  # M06; rows are in repo "audit_log"
    notifier: FakeNotifier
    scheduler: FakeScheduler
    plans: FakePlans
    svc: BillingService
    audited: AuditedRepository | None = None
    parent: AuditContext | None = None  # a verified parent (source user)
    admin: AuditContext | None = None  # a verified opsadmin (source admin)

    def audit_rows(self, **where) -> list[dict]:
        rows = self.repo.list("audit_log")
        for k, v in where.items():
            rows = [r for r in rows if r.get(k) == v]
        return sorted(rows, key=lambda r: (r["occurred_at"], r["$id"]))

    def actions(self) -> list[str]:
        return [r["action"] for r in self.audit_rows()]


def contexts() -> tuple[AuditContext, AuditContext]:
    return (
        AuditContext.for_principal(
            "u_parent", ["parent"], request_id="req-parent", ip="198.51.100.4"
        ),
        AuditContext.for_principal(
            "u_admin", ["opsadmin"], request_id="req-admin", ip="203.0.113.9"
        ),
    )


def make_rig(config: BillingConfig | None = None, *, discounts=None, repo=None) -> Rig:
    repo = repo or InMemoryRepository()
    ledger, notifier, scheduler = FakeLedger(), FakeNotifier(), FakeScheduler()
    audit = AuditService(repo)
    audited = AuditedRepository(repo, audit)
    plans = FakePlans(P2, P3)
    svc = BillingService(
        audited,
        plans=plans,
        ledger=ledger,
        notifier=notifier,
        scheduler=scheduler,
        discounts=discounts,
        config=config or BillingConfig(),
    )
    parent, admin = contexts()
    return Rig(repo, ledger, audit, notifier, scheduler, plans, svc, audited, parent, admin)


def new_sub(rig: Rig, *, child="child1", parent="parent1", plan="plan2", now=T0):
    return rig.svc.create_subscription(
        parent_id=parent, child_id=child, plan_id=plan, now=now, ctx=rig.parent
    )


def activate(rig: Rig, now=T0, **kw):
    """Create, pay the first invoice and assign a teacher."""
    res = new_sub(rig, now=now, **kw)
    rig.svc.on_payment_confirmed(res.invoice.id, f"pay-{res.subscription.id}", now=now)
    rig.svc.on_teacher_assigned(res.subscription.id, assignment_id="asg1", is_trial=True, now=now)
    return res.subscription.id, res.invoice
