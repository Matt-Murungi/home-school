"""B03 round 3: every lifecycle operation, crashed after EVERY write, then recovered.

For each operation: a crash after write i (i = 1..N), then the documented recovery (retry the
same operation, `redispatch_undelivered`, the roll job) and the invariants of `billing_crash`:
no credit on two invoices, every credit posted, every transition delivered, no open invoice on
a cancelled subscription, an invoice for the current period of every active subscription, one
refund per paid-but-undelivered cancellation."""

from __future__ import annotations

from datetime import timedelta

import pytest

from billing_crash import make_crash_rig, run_everywhere
from billing_helpers import T0, Rig
from hs_core.billing.fakes import FixedDiscount
from hs_core.billing.periods import period
from hs_core.billing.states import Status

DAY = timedelta(days=1)


def _activate(rig: Rig, child="c1"):
    res = rig.svc.create_subscription(parent_id="p1", child_id=child, plan_id="plan2", now=T0)
    rig.svc.on_payment_confirmed(res.invoice.id, f"pay-{child}", now=T0)
    rig.svc.on_teacher_assigned(res.subscription.id, assignment_id="a1", is_trial=False, now=T0)
    return res.subscription.id


NXT = period(__import__("datetime").date(2027, 1, 15), 1).start_at  # renewal of the T0 anchor


def _next_start(rig, sid):
    return period(rig.svc.get(sid).anchor_date, 1).start_at


def test_cancel_in_matching_with_a_paid_first_period_always_reaches_the_refund_queue():
    def setup(rig):
        res = rig.svc.create_subscription(parent_id="p", child_id="c", plan_id="plan2", now=T0)
        rig.svc.on_payment_confirmed(res.invoice.id, "pay-1", now=T0)
        return {"sid": res.subscription.id}

    def op(rig, ctx, now):
        rig.svc.cancel(ctx["sid"], now=now, actor="u_parent")

    def expect(rig, ctx):
        assert rig.svc.get(ctx["sid"]).status == Status.cancelled
        (r,) = rig.ledger.refunds_due.values()
        assert r["reason"] == "cancelled_before_activation" and r["amount_ugx"] == 240_000

    assert run_everywhere(setup, op, expect, now=T0 + DAY) >= 6


def test_immediate_cancel_of_an_active_subscription_voids_invoices_and_cancels_sessions():
    def setup(rig):
        sid = _activate(rig)
        rig.svc.generate_invoice(sid, 1, now=_next_start(rig, sid) - 3 * DAY)
        return {"sid": sid}

    def op(rig, ctx, now):
        rig.svc.cancel(ctx["sid"], now=now, actor="u_admin", immediate=True, reason="x")

    def expect(rig, ctx):
        assert rig.svc.get(ctx["sid"]).status == Status.cancelled
        assert rig.ledger.refunds_due == {}  # it had a teacher: no first-period refund
        assert len(rig.scheduler.cancelled) == 1
        assert "subscription_cancelled" in rig.notifier.names()

    run_everywhere(setup, op, expect, now=T0 + 3 * DAY)


def test_expire_past_due_voids_the_unpaid_invoice_and_cancels_sessions():
    def setup(rig):
        sid = _activate(rig)
        nxt = _next_start(rig, sid)
        rig.svc.generate_invoice(sid, 1, now=nxt - 3 * DAY)
        rig.svc.mark_past_due(sid, now=nxt + 3 * DAY)
        return {"sid": sid, "t": nxt + 3 * DAY}

    def op(rig, ctx, now):
        rig.svc.expire_past_due(ctx["sid"], now=now)

    def expect(rig, ctx):
        assert rig.svc.get(ctx["sid"]).status == Status.cancelled
        assert len(rig.scheduler.cancelled) == 1

    run_everywhere(setup, op, expect, now=NXT + 17 * DAY)


def test_mark_past_due_marks_the_overdue_invoice_even_after_a_crash():
    def setup(rig):
        sid = _activate(rig)
        nxt = _next_start(rig, sid)
        rig.svc.generate_invoice(sid, 1, now=nxt - 3 * DAY)
        return {"sid": sid, "t": nxt + 3 * DAY}

    def op(rig, ctx, now):
        rig.svc.mark_past_due(ctx["sid"], now=now)

    def expect(rig, ctx):
        assert rig.svc.get(ctx["sid"]).status == Status.past_due
        inv = [i for i in rig.repo.list("invoice") if i["period_index"] == 1][0]
        assert inv["status"] == "past_due"

    run_everywhere(setup, op, expect, now=NXT + 3 * DAY)


def test_resume_after_the_period_ended_always_ends_with_an_invoice_for_the_new_period():
    def setup(rig):
        sid = _activate(rig)
        rig.svc.pause(sid, now=T0 + DAY, actor="u")
        return {"sid": sid}

    def op(rig, ctx, now):
        rig.svc.resume(ctx["sid"], now=now, actor="u")

    def expect(rig, ctx):
        s = rig.svc.get(ctx["sid"])
        assert s.status == Status.active and s.period_index == 0
        mine = [
            i
            for i in rig.repo.list("invoice")
            if i["period_start_date"] == s.anchor_date.isoformat()
        ]
        assert len(mine) == 1 and mine[0]["status"] == "open"
        assert len(rig.scheduler.regenerated) >= 1

    run_everywhere(setup, op, expect, now=T0 + 50 * DAY)


def test_pause_voids_the_open_renewal_invoice_and_cancels_sessions():
    def setup(rig):
        sid = _activate(rig)
        rig.svc.generate_invoice(sid, 1, now=_next_start(rig, sid) - 3 * DAY)
        return {"sid": sid}

    def op(rig, ctx, now):
        rig.svc.pause(ctx["sid"], now=now, actor="u")

    def expect(rig, ctx):
        assert rig.svc.get(ctx["sid"]).status == Status.paused
        renewal = [i for i in rig.repo.list("invoice") if i["period_index"] == 1][0]
        assert renewal["status"] == "void"
        assert len(rig.scheduler.cancelled) == 1

    run_everywhere(setup, op, expect, now=T0 + 26 * DAY)


def test_roll_with_a_credit_takes_the_credit_exactly_once_whatever_crashes():
    def setup(rig):
        sid = _activate(rig)
        rig.svc.record_platform_miss(sid, "s1", reason="x", now=T0)
        return {"sid": sid}

    def op(rig, ctx, now):
        rig.svc.roll_subscription(ctx["sid"], now=now)

    def expect(rig, ctx):
        sid = ctx["sid"]
        invs = {i["period_index"]: i for i in rig.repo.list("invoice")}
        assert invs[1]["credit_ugx"] == 30_000 and invs[1]["amount"] == 210_000
        # pay it, advance, and the NEXT invoice must not take the same credit
        nxt = _next_start(rig, sid)
        rig.svc.on_payment_confirmed(invs[1]["$id"], "pay-r1", now=nxt)
        rig.svc.roll_subscription(sid, now=nxt + timedelta(hours=1))
        s = rig.svc.get(sid)
        assert s.period_index == 1
        inv2 = rig.svc.generate_invoice(sid, 2, now=period(s.anchor_date, 2).start_at - 3 * DAY)
        assert inv2.credit_ugx == 0 and inv2.amount == 240_000
        assert len(rig.ledger.credits) == 1

    run_everywhere(setup, op, expect, now=NXT - 3 * DAY)


def test_first_payment_always_ends_matching_with_the_match_request_event():
    def setup(rig):
        res = rig.svc.create_subscription(parent_id="p", child_id="c", plan_id="plan2", now=T0)
        return {"sid": res.subscription.id, "inv": res.invoice.id}

    def op(rig, ctx, now):
        rig.svc.on_payment_confirmed(ctx["inv"], "pay-1", now=now)

    def expect(rig, ctx):
        s = rig.svc.get(ctx["sid"])
        assert s.status == Status.matching and s.version == 1
        assert "matching_requested" in rig.notifier.names()
        assert len(rig.ledger.payments) == 1 and rig.ledger.refunds_due == {}
        assert rig.repo.list("invoice")[0]["status"] == "paid"

    run_everywhere(setup, op, expect, now=T0 + timedelta(hours=1))


def test_plan_change_survives_a_crash_at_every_write():
    def setup(rig):
        return {"sid": _activate(rig)}

    def op(rig, ctx, now):
        rig.svc.change_plan(ctx["sid"], "plan3", now=now, actor="u")

    def expect(rig, ctx):
        assert rig.svc.get(ctx["sid"]).pending_plan_id == "plan3"

    run_everywhere(setup, op, expect, now=T0 + DAY)


def test_renewal_advance_survives_a_crash_at_every_write():
    def setup(rig):
        sid = _activate(rig)
        nxt = _next_start(rig, sid)
        inv = rig.svc.generate_invoice(sid, 1, now=nxt - 3 * DAY)
        rig.svc.on_payment_confirmed(inv.id, "pay-r1", now=nxt - 2 * DAY)
        return {"sid": sid}

    def op(rig, ctx, now):
        rig.svc.roll_subscription(ctx["sid"], now=now)

    def expect(rig, ctx):
        s = rig.svc.get(ctx["sid"])
        assert s.period_index == 1 and s.status == Status.active
        assert len(rig.scheduler.regenerated) >= 1

    run_everywhere(setup, op, expect, now=NXT + timedelta(hours=1))


def test_cancel_at_period_end_then_the_boundary_cancel_survives_a_crash_at_every_write():
    def setup(rig):
        sid = _activate(rig)
        rig.svc.cancel(sid, now=T0 + DAY, actor="u")
        return {"sid": sid}

    def op(rig, ctx, now):
        rig.svc.roll_subscription(ctx["sid"], now=now)

    def expect(rig, ctx):
        assert rig.svc.get(ctx["sid"]).status == Status.cancelled
        assert len(rig.scheduler.cancelled) == 1

    run_everywhere(setup, op, expect, now=NXT + timedelta(hours=1))


def test_a_fully_discounted_first_invoice_confirms_itself_and_survives_every_crash():
    def setup(rig):
        return {}

    def op(rig, ctx, now):
        rig.svc.create_subscription(parent_id="p", child_id="c", plan_id="plan2", now=now)

    def expect(rig, ctx):
        (s,) = rig.repo.list("subscription")
        assert s["status"] == "matching"
        (inv,) = rig.repo.list("invoice")
        assert inv["amount"] == 0 and inv["status"] == "paid"
        assert rig.ledger.payments == {}  # nothing to post for 0 UGX

    from billing_crash import CrashClock

    clock = CrashClock()
    assert make_crash_rig(clock, discounts=FixedDiscount(10**9))
    # run with the discount: the generic runner builds plain rigs, so wrap the factory
    import billing_crash as bc

    orig = bc.make_crash_rig
    bc.make_crash_rig = lambda c, config=None, discounts=None: orig(c, config, FixedDiscount(10**9))
    try:
        run_everywhere(setup, op, expect, now=T0)
    finally:
        bc.make_crash_rig = orig


@pytest.mark.parametrize("n", [1])
def test_the_harness_itself_detects_a_missing_recovery(n):
    """Meta-test: a service whose effects are never retried must FAIL the harness."""

    def setup(rig):
        res = rig.svc.create_subscription(parent_id="p", child_id="c", plan_id="plan2", now=T0)
        rig.svc.on_payment_confirmed(res.invoice.id, "pay-1", now=T0)
        return {"sid": res.subscription.id}

    def op(rig, ctx, now):
        rig.svc.cancel(ctx["sid"], now=now, actor="u")

    def expect(rig, ctx):
        assert len(rig.ledger.refunds_due) == 1

    import billing_crash as bc
    from billing_crash import CrashClock

    orig_make = bc.make_crash_rig

    def broken(clock, config=None, discounts=None):
        rig = orig_make(clock, config, discounts)
        rig.svc._run_effects = lambda *a, **k: None  # effects never run
        return rig

    bc.make_crash_rig = broken
    try:
        with pytest.raises(AssertionError):
            run_everywhere(setup, op, expect, now=T0 + DAY)
    finally:
        bc.make_crash_rig = orig_make
    assert CrashClock
