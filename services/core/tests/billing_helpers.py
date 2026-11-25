"""Shared fixtures for the billing tests."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from hs_core.billing.fakes import (
    FakeAuditSink,
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
    repo: InMemoryRepository
    ledger: FakeLedger
    audit: FakeAuditSink
    notifier: FakeNotifier
    scheduler: FakeScheduler
    plans: FakePlans
    svc: BillingService


def make_rig(config: BillingConfig | None = None, *, discounts=None, repo=None) -> Rig:
    repo = repo or InMemoryRepository()
    ledger, audit, notifier, scheduler = (
        FakeLedger(),
        FakeAuditSink(),
        FakeNotifier(),
        FakeScheduler(),
    )
    plans = FakePlans(P2, P3)
    svc = BillingService(
        repo,
        plans=plans,
        ledger=ledger,
        audit=audit,
        notifier=notifier,
        scheduler=scheduler,
        discounts=discounts,
        config=config or BillingConfig(),
    )
    return Rig(repo, ledger, audit, notifier, scheduler, plans, svc)


def new_sub(rig: Rig, *, child="child1", parent="parent1", plan="plan2", now=T0):
    return rig.svc.create_subscription(parent_id=parent, child_id=child, plan_id=plan, now=now)


def activate(rig: Rig, now=T0, **kw):
    """Create, pay the first invoice and assign a teacher."""
    res = new_sub(rig, now=now, **kw)
    rig.svc.on_payment_confirmed(res.invoice.id, f"pay-{res.subscription.id}", now=now)
    rig.svc.on_teacher_assigned(res.subscription.id, assignment_id="asg1", is_trial=True, now=now)
    return res.subscription.id, res.invoice
