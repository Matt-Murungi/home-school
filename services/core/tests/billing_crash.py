"""Crash-injection harness for the billing lifecycle (permanent test fixture).

`CrashClock` counts every write: each repository create/update/delete and each call to an
external adapter (ledger, audit, notifier, scheduler). After write number N it raises `Crash`
(a BaseException, like a killed process: production code's `except Exception` cannot swallow it).
`run_everywhere` runs one lifecycle operation once to count its writes, then once per write index
with a crash right AFTER that write; after every crash it runs the documented recovery (retry
the same operation, `redispatch_undelivered`, the roll job) and checks the invariants.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from billing_helpers import P2, P3, Rig
from hs_core.billing.errors import BillingError
from hs_core.billing.fakes import (
    FakeAuditSink,
    FakeLedger,
    FakeNotifier,
    FakePlans,
    FakeScheduler,
)
from hs_core.billing.service import BillingConfig, BillingService
from hs_core.billing.states import ForbiddenTransition
from hs_core.repository import InMemoryRepository


class Crash(BaseException):
    """Simulated process death."""


class CrashClock:
    def __init__(self, crash_after: int | None = None) -> None:
        self.count = 0
        self.armed = False
        self.crash_after = crash_after

    def tick(self, label: str) -> None:
        if not self.armed:
            return
        self.count += 1
        if self.crash_after is not None and self.count == self.crash_after:
            raise Crash(f"{label} (write #{self.count})")


class CrashRepository(InMemoryRepository):
    def __init__(self, clock: CrashClock) -> None:
        super().__init__()
        self.clock = clock

    def create(self, table, row_id, data, permissions=None):
        out = super().create(table, row_id, data, permissions)
        self.clock.tick(f"create {table}")
        return out

    def update(self, table, row_id, data, permissions=None):
        out = super().update(table, row_id, data, permissions)
        self.clock.tick(f"update {table}")
        return out

    def delete(self, table, row_id):
        super().delete(table, row_id)
        self.clock.tick(f"delete {table}")


def _wrap(obj, clock: CrashClock, names: tuple[str, ...]) -> None:
    for name in names:
        orig = getattr(obj, name)

        def call(*a, _orig=orig, _name=name, **k):
            out = _orig(*a, **k)
            clock.tick(_name)
            return out

        setattr(obj, name, call)


def make_crash_rig(clock: CrashClock, config: BillingConfig | None = None, discounts=None) -> Rig:
    repo = CrashRepository(clock)
    ledger, audit, notifier, scheduler = (
        FakeLedger(),
        FakeAuditSink(),
        FakeNotifier(),
        FakeScheduler(),
    )
    _wrap(ledger, clock, ("record_payment", "record_credit_applied", "flag_refund_due"))
    _wrap(audit, clock, ("record",))
    _wrap(notifier, clock, ("notify",))
    _wrap(scheduler, clock, ("cancel_future", "regenerate"))
    plans = FakePlans(P2, P3)
    svc = BillingService(
        repo,
        plans=plans,
        ledger=ledger,
        audit=audit,
        notifier=notifier,
        scheduler=scheduler,
        discounts=discounts,
        config=config or BillingConfig(renewal_lead_days=5),
    )
    return Rig(repo, ledger, audit, notifier, scheduler, plans, svc)


def recover(rig: Rig, retry, now: datetime) -> None:
    """The documented recovery: retry the same operation, the sweep, the roll job, the sweep."""
    later = now + timedelta(minutes=10)  # past every lease
    try:
        retry(rig, later)
    except (BillingError, ForbiddenTransition):
        pass  # the operation is not repeatable once it took effect: that is fine
    sweep(rig, later)
    cursor = None
    for _ in range(50):
        res = rig.svc.roll_due(now=later, cursor=cursor)
        cursor = res.next_cursor
        if cursor is None:
            break
    sweep(rig, later)


def sweep(rig: Rig, now: datetime) -> None:
    cursor = None
    for _ in range(50):
        res = rig.svc.redispatch_undelivered(now=now, older_than_s=0, cursor=cursor)
        cursor = res.next_cursor
        if cursor is None:
            break


def check_invariants(rig: Rig) -> None:
    """Facts that must hold after ANY crash + recovery, for every subscription."""
    repo = rig.repo
    invoices = [r for r in repo.list("invoice")]
    credits = {c["$id"]: c for c in repo.list("billing_credit")}
    seen: dict[str, str] = {}
    for inv in invoices:
        import json

        for cid in json.loads(inv.get("credit_ids") or "[]"):
            assert cid not in seen, f"credit {cid} is on two invoices"
            seen[cid] = inv["$id"]
            assert credits[cid]["applied_invoice_id"] == inv["$id"], "credit not finalised"
            assert f"credit:{cid}" in rig.ledger.credits, "credit not posted to the ledger"
    for cid, c in credits.items():
        if c.get("applied_invoice_id"):
            assert seen.get(cid) == c["applied_invoice_id"], "credit applied to a different invoice"
    assert len(rig.ledger.credits) == len(seen)
    keys = [(i["subscription_id"], i["period_start_date"]) for i in invoices]
    assert len(keys) == len(set(keys))
    for sub in repo.list("subscription"):
        claims = sorted(
            (c for c in repo.list("subscription_transition") if c["subscription_id"] == sub["$id"]),
            key=lambda c: c["to_version"],
        )
        assert sub["version"] == len(claims), (sub["version"], len(claims))
        for c in claims:
            assert c["status"] == "delivered", (c["trigger"], c["status"])
            for i, name in enumerate(json.loads(c["events"])):
                eid = f"{sub['$id']}:{c['to_version']}:{i}"
                if name == "sessions_cancel_requested":
                    assert eid in rig.scheduler.cancelled, eid
                elif name == "sessions_regenerate_requested":
                    assert eid in rig.scheduler.regenerated, eid
                else:
                    assert eid in rig.notifier.events, eid
        if sub["status"] == "cancelled":
            assert not [
                i
                for i in invoices
                if i["subscription_id"] == sub["$id"] and i["status"] in ("open", "past_due")
            ], "open invoice on a cancelled subscription"
        if sub["status"] == "active":
            from datetime import date

            from billing_helpers import T0  # noqa: F401
            from hs_core.billing.periods import period
            from hs_core.billing.store import rid

            p = period(date.fromisoformat(sub["anchor_date"]), sub["period_index"])
            cur = rid("inv", sub["$id"], p.start.isoformat())
            cur_inv = [i for i in invoices if i["$id"] == cur]
            assert cur_inv and cur_inv[0]["status"] != "void", "active subscription without invoice"


def run_everywhere(setup, op, expect, *, now: datetime) -> int:
    """setup(rig) -> ctx; op(rig, ctx, now); expect(rig, ctx). Returns the number of writes."""
    clock = CrashClock()
    rig = make_crash_rig(clock)
    ctx = setup(rig)
    clock.armed = True
    op(rig, ctx, now)
    total = clock.count
    assert total > 0
    for i in range(1, total + 1):
        clock = CrashClock(crash_after=i)
        rig = make_crash_rig(clock)
        ctx = setup(rig)
        clock.armed = True
        try:
            op(rig, ctx, now)
        except Crash:
            pass
        clock.armed = False
        recover(rig, lambda r, later, ctx=ctx: op(r, ctx, later), now)
        try:
            check_invariants(rig)
            expect(rig, ctx)
        except AssertionError as e:
            raise AssertionError(f"crash after write {i}/{total}: {e}") from e
    return total


UTC_ = UTC
