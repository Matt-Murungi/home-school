"""B03 service: lifecycle, idempotency, invoices, credits, payments, cancellation, concurrency."""

from __future__ import annotations

import logging
import threading
from datetime import UTC, date, datetime, timedelta

import pytest

from billing_helpers import T0, activate, make_rig, new_sub
from hs_core.billing.errors import (
    ChildAlreadySubscribed,
    ConcurrentTransition,
    ForbiddenTransition,
    GraceNotExpired,
    InvalidMoney,
    PaymentRefReuse,
    PlanUnavailable,
    UnknownInvoice,
)
from hs_core.billing.fakes import FixedDiscount
from hs_core.billing.periods import period
from hs_core.billing.service import BillingConfig
from hs_core.billing.states import Status, Trigger

DAY = timedelta(days=1)


# ---------------------------------------------------------------- creation (edge: double tap)
def test_create_makes_a_pending_payment_subscription_and_the_first_invoice_at_period_start():
    rig = make_rig()
    res = new_sub(rig)
    s, inv = res.subscription, res.invoice
    assert res.created and s.status == Status.pending_payment and s.version == 0
    assert s.anchor_date == date(2027, 1, 15)  # Kampala date of creation
    p = period(s.anchor_date, 0)
    assert (s.period_start_at, s.period_end_at) == (p.start_at, p.end_at)
    assert inv.period_start_date == "2027-01-15" and inv.period_end_date == "2027-02-14"
    assert inv.amount == 240_000 and type(inv.amount) is int and inv.status == "open"
    assert inv.due_at == T0  # due at creation for the first period


def test_double_tap_creates_one_subscription_and_one_invoice():
    rig = make_rig()
    a = new_sub(rig)
    b = new_sub(rig, now=T0 + timedelta(seconds=2))
    assert a.subscription.id == b.subscription.id and a.created and not b.created
    assert a.invoice.id == b.invoice.id
    assert len(rig.repo.list("subscription")) == 1 and len(rig.repo.list("invoice")) == 1


def test_double_tap_from_50_threads_creates_exactly_one():
    rig = make_rig()
    out, errs = [], []
    barrier = threading.Barrier(50)

    def go():
        barrier.wait()
        try:
            out.append(new_sub(rig))
        except Exception as e:  # noqa: BLE001
            errs.append(e)

    ts = [threading.Thread(target=go) for _ in range(50)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert not errs
    assert len({r.subscription.id for r in out}) == 1
    assert sum(r.created for r in out) == 1
    assert len(rig.repo.list("subscription")) == 1 and len(rig.repo.list("invoice")) == 1


def test_a_different_plan_for_a_child_with_a_live_subscription_is_refused():
    rig = make_rig()
    new_sub(rig)
    with pytest.raises(ChildAlreadySubscribed):
        new_sub(rig, plan="plan3")


def test_each_child_has_its_own_subscription_and_a_parent_can_hold_several():
    rig = make_rig()
    a = new_sub(rig, child="c1")
    b = new_sub(rig, child="c2")
    assert a.subscription.id != b.subscription.id


def test_after_cancellation_the_child_can_subscribe_again():
    rig = make_rig()
    a = new_sub(rig)
    rig.svc.cancel(a.subscription.id, now=T0, actor="u_parent")
    b = new_sub(rig, now=T0 + DAY)
    assert b.created and b.subscription.id != a.subscription.id


def test_unknown_or_inactive_plan_is_refused():
    rig = make_rig()
    with pytest.raises(PlanUnavailable):
        new_sub(rig, plan="nope")


# ---------------------------------------------------------------- state machine in the service
def test_happy_path_pending_matching_active_with_events_and_audit():
    rig = make_rig()
    sid, inv = activate(rig)
    s = rig.svc.get(sid)
    assert s.status == Status.active and s.is_trial and s.assignment_id == "asg1"
    names = rig.notifier.names()
    for n in ("first_payment_confirmed", "matching_requested", "subscription_activated"):
        assert n in names
    actions = rig.audit.actions()
    assert (
        "subscription.payment_confirmed" in actions and "subscription.teacher_assigned" in actions
    )
    assert s.version == 2


def test_forbidden_transitions_raise_and_change_nothing():
    rig = make_rig()
    res = new_sub(rig)
    with pytest.raises(ForbiddenTransition):
        rig.svc.on_teacher_assigned(res.subscription.id, assignment_id="a", is_trial=False, now=T0)
    with pytest.raises(ForbiddenTransition):
        rig.svc.pause(res.subscription.id, now=T0, actor="u")
    with pytest.raises(ForbiddenTransition):
        rig.svc.transition(res.subscription.id, Trigger.resume, now=T0)
    s = rig.svc.get(res.subscription.id)
    assert s.status == Status.pending_payment and s.version == 0
    assert rig.audit.actions() == ["subscription.created"]


def test_cancelled_is_terminal_for_the_service():
    rig = make_rig()
    sid, _ = activate(rig)
    rig.svc.cancel(sid, now=T0, actor="u", immediate=True)
    for fn in (
        lambda: rig.svc.pause(sid, now=T0, actor="u"),
        lambda: rig.svc.resume(sid, now=T0, actor="u"),
        lambda: rig.svc.on_teacher_assigned(sid, assignment_id="x", is_trial=False, now=T0),
    ):
        with pytest.raises(ForbiddenTransition):
            fn()


def test_pause_cancels_future_sessions_and_resume_regenerates():
    rig = make_rig()
    sid, _ = activate(rig)
    t = T0 + 3 * DAY
    s = rig.svc.pause(sid, now=t, actor="u_parent").subscription
    assert s.status == Status.paused and s.paused_at == t
    assert [c["reason"] for c in rig.scheduler.cancelled.values()] == ["paused"]
    s = rig.svc.resume(sid, now=t + 2 * DAY, actor="u_parent").subscription
    assert s.status == Status.active and s.paused_at is None
    assert len(rig.scheduler.regenerated) == 1


def test_pause_does_not_extend_or_shorten_the_paid_period():
    rig = make_rig()
    sid, _ = activate(rig)
    before = rig.svc.get(sid)
    rig.svc.pause(sid, now=T0 + DAY, actor="u")
    rig.svc.resume(sid, now=T0 + 5 * DAY, actor="u")
    after = rig.svc.get(sid)
    assert (after.period_start_at, after.period_end_at) == (
        before.period_start_at,
        before.period_end_at,
    )


def test_resume_after_the_period_ended_starts_a_new_period_on_the_resume_date():
    rig = make_rig()
    sid, _ = activate(rig)
    late = T0 + 50 * DAY  # 2027-03-06
    rig.svc.pause(sid, now=T0 + DAY, actor="u")
    r = rig.svc.resume(sid, now=late, actor="u")
    s = r.subscription
    assert s.anchor_date == date(2027, 3, 6) and s.period_index == 0
    assert s.period_start_at == period(date(2027, 3, 6), 0).start_at
    invs = rig.repo.list("invoice", [])
    assert any(i["period_start_date"] == "2027-03-06" for i in invs)


def test_pause_limit_predicate():
    rig = make_rig(BillingConfig(max_pause_days=30))
    sid, _ = activate(rig)
    rig.svc.pause(sid, now=T0, actor="u")
    s = rig.svc.get(sid)
    assert not rig.svc.pause_limit_exceeded(s, T0 + 29 * DAY)
    assert rig.svc.pause_limit_exceeded(s, T0 + 30 * DAY)


def test_trial_flag_is_stored_from_the_assignment_and_does_not_change_billing():
    rig = make_rig()
    sid, inv = activate(rig)
    assert rig.svc.get(sid).is_trial is True
    assert inv.amount == 240_000
    rig.svc.on_teacher_assigned(sid, assignment_id="asg2", is_trial=False, now=T0 + 20 * DAY)
    s = rig.svc.get(sid)
    assert s.is_trial is False and s.assignment_id == "asg2" and s.status == Status.active


# ---------------------------------------------------------------- payments
def test_pending_payment_never_advances_state():
    rig = make_rig()
    res = new_sub(rig)
    for status in ("pending", "initiated", "unknown", "outcome_unknown", "failed", "expired"):
        out = rig.svc.on_payment_status(res.invoice.id, "ref-1", status, now=T0)
        assert out.status == "not_confirmed"
    assert rig.svc.get(res.subscription.id).status == Status.pending_payment
    assert rig.svc.get_invoice(res.invoice.id).status == "open"
    assert rig.ledger.payments == {}
    out = rig.svc.on_payment_status(res.invoice.id, "ref-1", "succeeded", now=T0)
    assert out.status == "applied"
    assert rig.svc.get(res.subscription.id).status == Status.matching


def test_payment_confirmed_moves_to_matching_marks_invoice_paid_and_posts_to_the_ledger():
    rig = make_rig()
    res = new_sub(rig)
    out = rig.svc.on_payment_confirmed(res.invoice.id, "momo-1", now=T0 + timedelta(hours=1))
    assert out.status == "applied"
    s = rig.svc.get(res.subscription.id)
    inv = rig.svc.get_invoice(res.invoice.id)
    assert s.status == Status.matching and s.first_payment_at == T0 + timedelta(hours=1)
    assert inv.status == "paid" and inv.payment_ref == "momo-1" and inv.paid_at
    assert len(rig.ledger.payments) == 1
    (p,) = rig.ledger.payments.values()
    assert p["amount_ugx"] == 240_000 and type(p["amount_ugx"]) is int


def test_duplicate_callbacks_do_not_double_activate_or_double_credit():
    rig = make_rig()
    res = new_sub(rig)
    outs = [rig.svc.on_payment_confirmed(res.invoice.id, "momo-1", now=T0) for _ in range(5)]
    assert [o.status for o in outs] == ["applied"] + ["duplicate"] * 4
    s = rig.svc.get(res.subscription.id)
    assert s.status == Status.matching and s.version == 1
    assert len(rig.ledger.payments) == 1 and len(rig.ledger.refunds_due) == 0
    assert rig.notifier.names().count("first_payment_confirmed") == 1


def test_50_threads_confirming_the_same_payment_apply_it_once():
    rig = make_rig()
    res = new_sub(rig)
    outs = []
    barrier = threading.Barrier(50)

    def go():
        barrier.wait()
        outs.append(rig.svc.on_payment_confirmed(res.invoice.id, "momo-1", now=T0))

    ts = [threading.Thread(target=go) for _ in range(50)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    kinds = [o.status for o in outs]
    assert kinds.count("applied") == 1
    assert set(kinds) <= {"applied", "duplicate", "in_progress"}
    s = rig.svc.get(res.subscription.id)
    assert s.status == Status.matching and s.version == 1
    assert len(rig.ledger.payments) == 1 and not rig.ledger.refunds_due
    # a later retry settles to duplicate
    assert rig.svc.on_payment_confirmed(res.invoice.id, "momo-1", now=T0).status == "duplicate"


def test_two_different_payments_for_one_invoice_apply_one_and_flag_the_other_for_refund():
    rig = make_rig()
    res = new_sub(rig)
    outs = []
    barrier = threading.Barrier(50)

    def go(i):
        barrier.wait()
        outs.append(rig.svc.on_payment_confirmed(res.invoice.id, f"momo-{i}", now=T0))

    ts = [threading.Thread(target=go, args=(i,)) for i in range(50)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    kinds = [o.status for o in outs]
    assert kinds.count("applied") == 1 and kinds.count("flagged") == 49
    assert {o.flag for o in outs if o.status == "flagged"} == {"duplicate_payment"}
    assert len(rig.ledger.payments) == 1 and len(rig.ledger.refunds_due) == 49
    s = rig.svc.get(res.subscription.id)
    assert s.status == Status.matching and s.version == 1


def test_wrong_amount_is_flagged_and_does_not_activate():
    rig = make_rig()
    res = new_sub(rig)
    out = rig.svc.on_payment_confirmed(res.invoice.id, "momo-1", now=T0, amount_ugx=100_000)
    assert out.status == "flagged" and out.flag == "amount_mismatch"
    assert rig.svc.get(res.subscription.id).status == Status.pending_payment
    assert len(rig.ledger.refunds_due) == 1 and rig.ledger.payments == {}


def test_float_or_bool_amounts_are_refused():
    rig = make_rig()
    res = new_sub(rig)
    for bad in (240_000.0, True, "240000", -1):
        with pytest.raises(InvalidMoney):
            rig.svc.on_payment_confirmed(res.invoice.id, "momo-1", now=T0, amount_ugx=bad)


def test_same_payment_reference_for_another_invoice_is_refused():
    rig = make_rig()
    a = new_sub(rig, child="c1")
    b = new_sub(rig, child="c2")
    rig.svc.on_payment_confirmed(a.invoice.id, "momo-1", now=T0)
    with pytest.raises(PaymentRefReuse):
        rig.svc.on_payment_confirmed(b.invoice.id, "momo-1", now=T0)
    assert rig.svc.get(b.subscription.id).status == Status.pending_payment


def test_unknown_invoice():
    rig = make_rig()
    with pytest.raises(UnknownInvoice):
        rig.svc.on_payment_confirmed("nope", "momo-1", now=T0)


def test_late_payment_after_cancellation_is_flagged_for_refund_and_never_activates():
    rig = make_rig()
    res = new_sub(rig)
    rig.svc.cancel(res.subscription.id, now=T0 + DAY, actor="u_parent")
    out = rig.svc.on_payment_confirmed(res.invoice.id, "momo-late", now=T0 + 2 * DAY)
    assert out.status == "flagged" and out.flag == "late_payment_after_cancellation"
    assert rig.svc.get(res.subscription.id).status == Status.cancelled
    assert len(rig.ledger.refunds_due) == 1 and rig.ledger.payments == {}
    assert rig.svc.on_payment_confirmed(res.invoice.id, "momo-late", now=T0).status == "duplicate"
    assert len(rig.ledger.refunds_due) == 1


def test_cancelling_after_the_first_payment_flags_it_for_refund_review():
    rig = make_rig()
    res = new_sub(rig)
    rig.svc.on_payment_confirmed(res.invoice.id, "momo-1", now=T0)
    rig.svc.cancel(res.subscription.id, now=T0 + DAY, actor="u_parent")  # matching: immediate
    assert rig.svc.get(res.subscription.id).status == Status.cancelled
    (r,) = rig.ledger.refunds_due.values()
    assert r["reason"] == "cancelled_before_activation" and r["amount_ugx"] == 240_000


def test_a_crash_after_the_claim_is_resumed_by_the_retry_without_double_effects():
    rig = make_rig()
    res = new_sub(rig)
    rig.ledger.fail = True
    with pytest.raises(RuntimeError):
        rig.svc.on_payment_confirmed(res.invoice.id, "momo-1", now=T0)
    assert rig.svc.get(res.subscription.id).status == Status.pending_payment
    rig.ledger.fail = False
    # within the lease: another worker may still be running
    assert rig.svc.on_payment_confirmed(res.invoice.id, "momo-1", now=T0).status == "in_progress"
    out = rig.svc.on_payment_confirmed(res.invoice.id, "momo-1", now=T0 + timedelta(minutes=5))
    assert out.status == "applied"
    s = rig.svc.get(res.subscription.id)
    assert s.status == Status.matching and s.version == 1 and len(rig.ledger.payments) == 1


def test_a_crash_after_the_state_change_is_resumed_without_a_second_transition():
    rig = make_rig()
    res = new_sub(rig)
    rig.audit.fail = False
    orig = rig.svc._finish_payment
    calls = {"n": 0}

    def boom(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("crash after the state change")
        return orig(*a, **k)

    rig.svc._finish_payment = boom
    with pytest.raises(RuntimeError):
        rig.svc.on_payment_confirmed(res.invoice.id, "momo-1", now=T0)
    assert rig.svc.get(res.subscription.id).status == Status.matching
    out = rig.svc.on_payment_confirmed(res.invoice.id, "momo-1", now=T0 + timedelta(minutes=5))
    assert out.status == "applied"
    assert rig.svc.get(res.subscription.id).version == 1


# ------------------------------------------- late payment: period start stays, misses credited
def test_payment_after_period_start_keeps_the_period_and_credits_the_missed_sessions():
    rig = make_rig()
    res = new_sub(rig, plan="plan3")  # 3 a week, period 15 Jan - 14 Feb (31 days)
    paid_at = T0 + 14 * DAY  # 29 Jan
    rig.svc.on_payment_confirmed(res.invoice.id, "momo-1", now=paid_at)
    s = rig.svc.get(res.subscription.id)
    p = period(date(2027, 1, 15), 0)
    assert (s.period_start_at, s.period_end_at) == (p.start_at, p.end_at)  # start stays
    credits = rig.repo.list("billing_credit")
    assert len(credits) == 1
    # 14 lost days at 3/week: 3 * 14 // 7 = 6 sessions
    assert credits[0]["sessions"] == 6 and credits[0]["kind"] == "late_payment_start"


def test_payment_on_the_period_start_day_credits_nothing():
    rig = make_rig()
    res = new_sub(rig)
    rig.svc.on_payment_confirmed(res.invoice.id, "momo-1", now=T0 + timedelta(hours=2))
    assert rig.repo.list("billing_credit") == []


def test_late_payment_credit_is_not_duplicated_by_a_duplicate_callback():
    rig = make_rig()
    res = new_sub(rig, plan="plan3")
    for _ in range(3):
        rig.svc.on_payment_confirmed(res.invoice.id, "momo-1", now=T0 + 14 * DAY)
    assert len(rig.repo.list("billing_credit")) == 1


# ---------------------------------------------------------------- credits (SUB-5)
def test_platform_missed_session_is_tracked_once_and_applied_to_the_next_invoice():
    rig = make_rig()
    sid, _ = activate(rig)
    c1 = rig.svc.record_platform_miss(
        sid, "sess-1", reason="teacher_cancelled_no_substitute", now=T0
    )
    c2 = rig.svc.record_platform_miss(
        sid, "sess-1", reason="teacher_cancelled_no_substitute", now=T0
    )
    assert c1.id == c2.id and len(rig.repo.list("billing_credit")) == 1
    rig.svc.record_platform_miss(sid, "sess-2", reason="admin", now=T0)
    s = rig.svc.get(sid)
    nxt = period(s.anchor_date, 1)
    inv = rig.svc.generate_invoice(sid, 1, now=nxt.start_at - 5 * DAY)
    # unit = 240000 // 8 = 30000; two credits
    assert inv.plan_price_ugx == 240_000 and inv.credit_ugx == 60_000 and inv.amount == 180_000
    assert len(inv.credit_ids) == 2
    assert all(c["applied_invoice_id"] == inv.id for c in rig.repo.list("billing_credit"))
    assert len(rig.ledger.credits) == 2
    # a credit is applied once: a later invoice gets none
    rig.svc.on_payment_confirmed(inv.id, "momo-r1", now=period(s.anchor_date, 1).start_at)
    rig.svc.roll_subscription(sid, now=period(s.anchor_date, 1).start_at)
    assert rig.svc.get(sid).period_index == 1
    inv2 = rig.svc.generate_invoice(sid, 2, now=period(s.anchor_date, 2).start_at - 5 * DAY)
    assert inv2.credit_ugx == 0 and inv2.amount == 240_000


def test_credit_never_makes_an_invoice_negative_and_leftover_credit_carries_over():
    rig = make_rig()
    sid, _ = activate(rig)
    for i in range(10):
        rig.svc.record_platform_miss(sid, f"s{i}", reason="x", now=T0)
    s = rig.svc.get(sid)
    inv = rig.svc.generate_invoice(sid, 1, now=period(s.anchor_date, 1).start_at - DAY)
    assert inv.amount == 0 and inv.credit_ugx == 240_000 and inv.status == "paid"
    left = [c for c in rig.repo.list("billing_credit") if not c.get("applied_invoice_id")]
    assert len(left) == 2


def test_credit_value_is_integer_ugx_floor_of_price_over_sessions_included():
    rig = make_rig()
    sid, _ = activate(rig, plan="plan3")  # 330000 / floor(3*31/7=13) = 25384 (floor)
    rig.svc.record_platform_miss(sid, "s1", reason="x", now=T0)
    s = rig.svc.get(sid)
    inv = rig.svc.generate_invoice(sid, 1, now=period(s.anchor_date, 1).start_at - DAY)
    p1 = period(s.anchor_date, 1)
    assert inv.credit_ugx == 330_000 // (3 * p1.days // 7)
    assert type(inv.credit_ugx) is int and type(inv.amount) is int


def test_discount_hook_is_applied_and_must_be_integer_ugx():
    rig = make_rig(discounts=FixedDiscount(40_000))
    res = new_sub(rig)
    assert res.invoice.discount_ugx == 40_000 and res.invoice.amount == 200_000
    for bad in (1000.5, True, -5):
        r2 = make_rig(discounts=FixedDiscount(bad))
        with pytest.raises(InvalidMoney):
            new_sub(r2)
    r3 = make_rig(discounts=FixedDiscount(10**9))  # more than the price: capped at the price
    assert new_sub(r3).invoice.amount == 0


def test_float_plan_price_is_refused():
    from hs_core.billing.models import PlanInfo

    with pytest.raises(InvalidMoney):
        PlanInfo("p", price_ugx=240000.0, sessions_per_week=2)


# ---------------------------------------------------------------- invoices
def test_invoice_per_period_is_unique_by_subscription_and_period_start():
    rig = make_rig()
    sid, _ = activate(rig)
    s = rig.svc.get(sid)
    now = period(s.anchor_date, 1).start_at - 3 * DAY
    a = rig.svc.generate_invoice(sid, 1, now=now)
    b = rig.svc.generate_invoice(sid, 1, now=now + timedelta(hours=1))
    assert a.id == b.id and len(rig.repo.list("invoice")) == 2  # first period + next
    assert a.period_start_date == "2027-02-15" and a.due_at == period(s.anchor_date, 1).start_at


def test_50_threads_generating_the_same_period_make_one_invoice():
    rig = make_rig()
    sid, _ = activate(rig)
    s = rig.svc.get(sid)
    rig.svc.record_platform_miss(sid, "s1", reason="x", now=T0)
    now = period(s.anchor_date, 1).start_at - 3 * DAY
    outs = []
    barrier = threading.Barrier(50)

    def go():
        barrier.wait()
        outs.append(rig.svc.generate_invoice(sid, 1, now=now))

    ts = [threading.Thread(target=go) for _ in range(50)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len({o.id for o in outs}) == 1 and len(rig.repo.list("invoice")) == 2
    assert len(rig.ledger.credits) == 1


def test_no_invoice_for_a_cancelled_subscription_or_a_far_future_period():
    rig = make_rig()
    sid, _ = activate(rig)
    from hs_core.billing.errors import InvoiceNotAllowed

    with pytest.raises(InvoiceNotAllowed):
        rig.svc.generate_invoice(sid, 3, now=T0)
    rig.svc.cancel(sid, now=T0, actor="u", immediate=True)
    with pytest.raises(InvoiceNotAllowed):
        rig.svc.generate_invoice(sid, 1, now=T0)


# ------------------------------------------------ plan change, proration (default: none)
def test_plan_change_applies_from_the_next_period_with_no_proration():
    rig = make_rig()
    sid, _ = activate(rig)
    rig.svc.change_plan(sid, "plan3", now=T0 + 3 * DAY, actor="u_parent")
    s = rig.svc.get(sid)
    assert s.plan_id == "plan2" and s.pending_plan_id == "plan3"
    inv = rig.svc.generate_invoice(sid, 1, now=period(s.anchor_date, 1).start_at - 3 * DAY)
    assert inv.plan_id == "plan3" and inv.plan_price_ugx == 330_000
    assert rig.repo.get("invoice", rig.repo.list("invoice")[0]["$id"])["amount"] in (
        240_000,
        330_000,
    )
    first = [i for i in rig.repo.list("invoice") if i["period_index"] == 0][0]
    assert first["amount"] == 240_000  # current period untouched: no proration, no refund


def test_plan_change_to_unknown_plan_is_refused_and_changing_back_clears_the_pending_change():
    rig = make_rig()
    sid, _ = activate(rig)
    with pytest.raises(PlanUnavailable):
        rig.svc.change_plan(sid, "nope", now=T0, actor="u")
    rig.svc.change_plan(sid, "plan3", now=T0, actor="u")
    rig.svc.change_plan(sid, "plan2", now=T0, actor="u")
    assert rig.svc.get(sid).pending_plan_id is None


# ---------------------------------------------------------------- cancellation
def test_active_cancel_defaults_to_the_end_of_the_paid_period():
    rig = make_rig()
    sid, _ = activate(rig)
    out = rig.svc.cancel(sid, now=T0 + 3 * DAY, actor="u_parent", reason="moving")
    s = out.subscription
    assert s.status == Status.active and s.cancel_at_period_end and s.cancel_reason == "moving"
    assert rig.scheduler.cancelled == {}  # sessions continue to the end of the paid period
    assert "subscription_cancelled" not in rig.notifier.names()
    # idempotent
    rig.svc.cancel(sid, now=T0 + 4 * DAY, actor="u_parent", reason="moving")
    assert rig.svc.get(sid).version == s.version


def test_keep_undoes_a_cancel_at_period_end():
    rig = make_rig()
    sid, _ = activate(rig)
    rig.svc.cancel(sid, now=T0, actor="u")
    rig.svc.keep(sid, now=T0, actor="u")
    assert rig.svc.get(sid).cancel_at_period_end is False


def test_immediate_cancel_cancels_future_sessions_and_emits_events():
    rig = make_rig()
    sid, _ = activate(rig)
    out = rig.svc.cancel(sid, now=T0 + 3 * DAY, actor="u_admin", immediate=True, reason="fraud")
    assert out.subscription.status == Status.cancelled and out.subscription.cancelled_at
    (c,) = rig.scheduler.cancelled.values()
    assert c["from_at"] == T0 + 3 * DAY and c["reason"] == "cancelled"
    assert "subscription_cancelled" in rig.notifier.names()


def test_past_due_and_paused_cancel_immediately():
    rig = make_rig()
    sid, _ = activate(rig)
    rig.svc.pause(sid, now=T0, actor="u")
    assert rig.svc.cancel(sid, now=T0, actor="u").subscription.status == Status.cancelled


def test_cancel_twice_is_idempotent_and_emits_events_once():
    rig = make_rig()
    sid, _ = activate(rig)
    rig.svc.cancel(sid, now=T0, actor="u", immediate=True)
    again = rig.svc.cancel(sid, now=T0, actor="u", immediate=True)
    assert not again.changed
    assert rig.notifier.names().count("subscription_cancelled") == 1


# ------------------------------------------------ past due hooks (B04 owns reminders and pausing)
def test_mark_past_due_requires_the_grace_period_to_have_run_out():
    rig = make_rig(BillingConfig(grace_days=3))
    sid, _ = activate(rig)
    s = rig.svc.get(sid)
    nxt = period(s.anchor_date, 1)
    rig.svc.generate_invoice(sid, 1, now=nxt.start_at - 3 * DAY)
    with pytest.raises(GraceNotExpired):
        rig.svc.mark_past_due(sid, now=nxt.start_at + 2 * DAY + timedelta(hours=23))
    out = rig.svc.mark_past_due(sid, now=nxt.start_at + 3 * DAY)
    assert out.subscription.status == Status.past_due and out.subscription.past_due_at
    inv = [i for i in rig.repo.list("invoice") if i["period_index"] == 1][0]
    assert inv["status"] == "past_due"
    assert "subscription_past_due" in rig.notifier.names()


def test_past_due_then_payment_reactivates_and_credits_the_paused_days():
    rig = make_rig(BillingConfig(grace_days=3))
    sid, _ = activate(rig, plan="plan3")
    s = rig.svc.get(sid)
    nxt = period(s.anchor_date, 1)
    inv = rig.svc.generate_invoice(sid, 1, now=nxt.start_at - 3 * DAY)
    rig.svc.mark_past_due(sid, now=nxt.start_at + 3 * DAY)
    paid = nxt.start_at + 10 * DAY
    out = rig.svc.on_payment_confirmed(inv.id, "momo-r1", now=paid)
    assert out.status == "applied"
    s = rig.svc.get(sid)
    assert s.status == Status.active and s.period_index == 1
    assert s.period_start_at == nxt.start_at  # period start stays at the schedule
    (c,) = rig.repo.list("billing_credit")
    assert c["kind"] == "late_payment_start" and c["sessions"] >= 1
    assert len(rig.scheduler.regenerated) >= 1


def test_expire_past_due_cancels_after_the_configured_days():
    rig = make_rig(BillingConfig(grace_days=3, past_due_cancel_days=14))
    sid, _ = activate(rig)
    s = rig.svc.get(sid)
    nxt = period(s.anchor_date, 1)
    rig.svc.generate_invoice(sid, 1, now=nxt.start_at - 3 * DAY)
    t = nxt.start_at + 3 * DAY
    rig.svc.mark_past_due(sid, now=t)
    with pytest.raises(GraceNotExpired):
        rig.svc.expire_past_due(sid, now=t + 13 * DAY)
    out = rig.svc.expire_past_due(sid, now=t + 14 * DAY)
    assert out.subscription.status == Status.cancelled
    assert len(rig.scheduler.cancelled) == 1


# ---------------------------------------------------------------- renewal (period roll)
def test_roll_creates_the_next_invoice_n_days_before_renewal_and_is_idempotent():
    rig = make_rig(BillingConfig(renewal_lead_days=5))
    sid, _ = activate(rig)
    s = rig.svc.get(sid)
    nxt = period(s.anchor_date, 1)
    assert rig.svc.roll_subscription(sid, now=nxt.start_at - 6 * DAY).action == "none"
    assert len(rig.repo.list("invoice")) == 1
    r = rig.svc.roll_subscription(sid, now=nxt.start_at - 5 * DAY)
    assert r.action == "invoice_created" and len(rig.repo.list("invoice")) == 2
    r = rig.svc.roll_subscription(sid, now=nxt.start_at - 4 * DAY)
    assert r.action == "none" and len(rig.repo.list("invoice")) == 2


def test_roll_job_twice_and_50_threads_create_each_invoice_once():
    rig = make_rig(BillingConfig(renewal_lead_days=5))
    ids = [activate(rig, child=f"c{i}")[0] for i in range(5)]
    s = rig.svc.get(ids[0])
    now = period(s.anchor_date, 1).start_at - 2 * DAY
    barrier = threading.Barrier(50)

    def go():
        barrier.wait()
        rig.svc.roll_due(now=now)

    ts = [threading.Thread(target=go) for _ in range(50)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    invs = rig.repo.list("invoice")
    assert len(invs) == 10
    assert len({(i["subscription_id"], i["period_start_date"]) for i in invs}) == 10


def test_renewal_payment_before_period_end_advances_the_period_at_the_boundary():
    rig = make_rig(BillingConfig(renewal_lead_days=5))
    sid, _ = activate(rig)
    s = rig.svc.get(sid)
    nxt = period(s.anchor_date, 1)
    inv = rig.svc.roll_subscription(sid, now=nxt.start_at - 4 * DAY)
    inv = rig.repo.list("invoice", [])[1]
    rig.svc.on_payment_confirmed(inv["$id"], "momo-r1", now=nxt.start_at - 2 * DAY)
    assert rig.svc.get(sid).period_index == 0  # still inside the paid period
    r = rig.svc.roll_subscription(sid, now=nxt.start_at + timedelta(hours=1))
    assert r.action == "period_advanced"
    s = rig.svc.get(sid)
    assert s.period_index == 1 and (s.period_start_at, s.period_end_at) == (
        nxt.start_at,
        nxt.end_at,
    )
    assert len(rig.scheduler.regenerated) == 1
    assert rig.svc.roll_subscription(sid, now=nxt.start_at + 2 * DAY).action != "period_advanced"


def test_roll_skips_paused_pending_and_past_due_subscriptions():
    rig = make_rig(BillingConfig(renewal_lead_days=5))
    a, _ = activate(rig, child="a")
    b = new_sub(rig, child="b").subscription.id
    rig.svc.pause(a, now=T0, actor="u")
    now = T0 + 40 * DAY
    assert rig.svc.roll_subscription(a, now=now).action == "skipped"
    assert rig.svc.roll_subscription(b, now=now).action == "skipped"
    assert len(rig.repo.list("invoice")) == 2


def test_roll_honours_cancel_at_period_end_no_invoice_then_cancelled_at_the_boundary():
    rig = make_rig(BillingConfig(renewal_lead_days=5))
    sid, _ = activate(rig)
    rig.svc.cancel(sid, now=T0 + DAY, actor="u")
    s = rig.svc.get(sid)
    end = s.period_end_at
    assert rig.svc.roll_subscription(sid, now=end - 3 * DAY).action == "none"
    assert len(rig.repo.list("invoice")) == 1
    r = rig.svc.roll_subscription(sid, now=end)
    assert r.action == "cancelled"
    s = rig.svc.get(sid)
    assert s.status == Status.cancelled
    assert len(rig.scheduler.cancelled) == 1


def test_roll_isolates_errors_per_subscription_and_reports_them():
    rig = make_rig(BillingConfig(renewal_lead_days=5))
    ids = [activate(rig, child=f"c{i}")[0] for i in range(4)]
    s = rig.svc.get(ids[0])
    now = period(s.anchor_date, 1).start_at - 2 * DAY
    bad = ids[1]
    orig = rig.svc.generate_invoice

    def flaky(sub_id, *a, **k):
        if sub_id == bad:
            raise RuntimeError("boom")
        return orig(sub_id, *a, **k)

    rig.svc.generate_invoice = flaky
    res = rig.svc.roll_due(now=now)
    assert res.failed == [bad] and res.processed == 4 and res.invoices_created == 3
    rig.svc.generate_invoice = orig
    res = rig.svc.roll_due(now=now)
    assert res.failed == [] and res.invoices_created == 1


def test_roll_due_is_chunked_with_a_keyset_cursor():
    rig = make_rig(BillingConfig(renewal_lead_days=5, roll_chunk_size=3))
    ids = [activate(rig, child=f"c{i:02d}")[0] for i in range(8)]
    s = rig.svc.get(ids[0])
    now = period(s.anchor_date, 1).start_at - 2 * DAY
    seen, cursor, chunks = [], None, 0
    while True:
        res = rig.svc.roll_due(now=now, cursor=cursor)
        chunks += 1
        seen += res.subscription_ids
        cursor = res.next_cursor
        if cursor is None:
            break
    assert chunks == 4 and sorted(set(seen)) == sorted(ids)
    assert len(rig.repo.list("invoice")) == 16


# ---------------------------------------------------------------- concurrency of transitions
def _race(rig, sid, ops):
    """Run ops (name, callable) at once; returns the outcome names."""
    outcomes = []
    barrier = threading.Barrier(len(ops))

    def run(name, fn):
        barrier.wait()
        try:
            fn()
            outcomes.append(name)
        except (ConcurrentTransition, ForbiddenTransition) as e:
            outcomes.append(type(e).__name__)

    ts = [threading.Thread(target=run, args=op) for op in ops]
    [t.start() for t in ts]
    [t.join() for t in ts]
    return outcomes


def test_two_transitions_from_the_same_version_exactly_one_wins():
    """Deterministic: both writers read version v; the second claim of v+1 must lose."""
    rig = make_rig()
    sid, _ = activate(rig)
    stale_a = rig.svc.get(sid)
    stale_b = rig.svc.get(sid)
    kw = dict(to_status=None, events=[], now=T0, actor="u", reason=None)
    rig.svc.store.commit(stale_a, {"cancel_reason": "a"}, trigger="x", **kw)
    with pytest.raises(ConcurrentTransition):
        rig.svc.store.commit(stale_b, {"cancel_reason": "b"}, trigger="y", **kw)
    s = rig.svc.get(sid)
    assert s.cancel_reason == "a" and s.version == stale_a.version + 1


def _pause_vs_cancel_once():
    rig = make_rig()
    sid, _ = activate(rig)
    outcomes = _race(
        rig,
        sid,
        [
            ("pause", lambda: rig.svc.pause(sid, now=T0, actor="u")),
            ("cancel", lambda: rig.svc.cancel(sid, now=T0, actor="u", immediate=True)),
        ],
    )
    s = rig.svc.get(sid)
    wins = [o for o in outcomes if o in ("pause", "cancel")]
    assert 1 <= len(wins) <= 2
    if len(wins) == 2:  # the loser re-read after the winner: pause then cancel
        assert s.status == Status.cancelled
    claims = rig.repo.list("subscription_transition")
    assert len({c["$id"] for c in claims}) == len(claims)
    assert s.version == len([c for c in claims if c["status"] in ("applied", "delivered")])


def test_two_concurrent_transitions_leave_a_consistent_history():
    for _ in range(20):
        _pause_vs_cancel_once()


def test_50_threads_pause_vs_cancel_leave_a_consistent_state():
    rig = make_rig()
    sid, _ = activate(rig)
    outcomes = []
    barrier = threading.Barrier(50)

    def go(i):
        barrier.wait()
        try:
            if i % 2:
                rig.svc.pause(sid, now=T0, actor="u")
            else:
                rig.svc.cancel(sid, now=T0, actor="u", immediate=True)
            outcomes.append("ok")
        except (ConcurrentTransition, ForbiddenTransition):
            outcomes.append("lost")

    ts = [threading.Thread(target=go, args=(i,)) for i in range(50)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    s = rig.svc.get(sid)
    assert s.status in (Status.paused, Status.cancelled)
    applied = [
        c
        for c in rig.repo.list("subscription_transition")
        if c["status"] in ("applied", "delivered")
    ]
    assert s.version == len(applied)
    assert outcomes.count("ok") >= 1
    # no impossible history: replay the claims in version order through the table
    from hs_core.billing.states import transition

    status = Status.active
    for c in sorted(applied, key=lambda c: c["to_version"]):
        if c["trigger"] in ("pause", "cancel"):
            status = transition(status, c["trigger"]).to
    assert status == s.status


def test_a_stale_claim_from_a_dead_writer_is_repaired_before_the_next_transition():
    rig = make_rig()
    sid, _ = activate(rig)
    before = rig.svc.get(sid)
    real_update = rig.repo.update
    state = {"die": True}

    def dying_update(table, row_id, data, permissions=None):
        if table == "subscription" and state["die"] and data.get("status") == "paused":
            raise RuntimeError("worker died after claiming")
        return real_update(table, row_id, data, permissions)

    rig.repo.update = dying_update
    with pytest.raises(RuntimeError):
        rig.svc.pause(sid, now=T0, actor="u")
    state["die"] = False
    rig.repo.update = real_update
    assert rig.svc.get(sid).version == before.version  # claim exists, row not updated
    # within the lease nothing is repaired; the transition conflicts
    with pytest.raises(ConcurrentTransition):
        rig.svc.cancel(sid, now=T0 + timedelta(seconds=5), actor="u", immediate=True)
    # after the lease the stale claim is rolled forward (the pause happened), then cancel applies
    out = rig.svc.cancel(sid, now=T0 + timedelta(minutes=5), actor="u", immediate=True)
    assert out.subscription.status == Status.cancelled
    assert out.subscription.version == before.version + 2


# ---------------------------------------------------------------- dispatch + logging
def test_a_failing_notifier_or_scheduler_never_undoes_a_committed_transition():
    rig = make_rig()
    sid, _ = activate(rig)
    rig.notifier.fail = True
    rig.scheduler.fail = True
    out = rig.svc.cancel(sid, now=T0, actor="u", immediate=True)
    assert out.subscription.status == Status.cancelled and out.dispatch_failures == 2
    assert rig.svc.get(sid).status == Status.cancelled


def test_a_failing_audit_sink_surfaces_but_the_transition_is_committed():
    rig = make_rig()
    sid, _ = activate(rig)
    rig.audit.fail = True
    out = rig.svc.pause(sid, now=T0, actor="u")
    assert out.subscription.status == Status.paused and out.audit_failed


def test_logs_carry_ids_only_no_phone_numbers_or_amounts_of_people(caplog):
    caplog.set_level(logging.DEBUG, logger="hs_core.billing")
    rig = make_rig()
    sid, inv = activate(rig, parent="parent-0771234567-ignore")
    rig.svc.cancel(sid, now=T0, actor="u", immediate=True)
    text = " ".join(r.getMessage() for r in caplog.records)
    assert "0771234567" not in text and "+256" not in text


def test_events_are_returned_without_being_sent_by_the_pure_transition_call():
    rig = make_rig()
    res = new_sub(rig)
    out = rig.svc.transition(res.subscription.id, Trigger.payment_confirmed, now=T0, dispatch=False)
    assert [e.name for e in out.events][:2] == ["first_payment_confirmed", "matching_requested"]
    assert rig.notifier.events == {}
    assert all(e.event_id.startswith(res.subscription.id) for e in out.events)


# ---------------------------------------------------------------- property tests
def test_random_event_sequences_never_reach_a_forbidden_state_or_corrupt_invariants():
    import random

    rng = random.Random(99)
    for _ in range(40):
        rig = make_rig()
        res = new_sub(rig)
        sid = res.subscription.id
        now = T0
        invoices = [res.invoice.id]
        for _step in range(40):
            now += timedelta(hours=rng.randrange(1, 300))
            op = rng.choice(
                [
                    "pay",
                    "pay",
                    "assign",
                    "pause",
                    "resume",
                    "cancel",
                    "cancel_now",
                    "past_due",
                    "roll",
                    "plan",
                    "dup_pay",
                    "expire",
                ]
            )
            try:
                if op == "pay":
                    inv = rng.choice(invoices)
                    rig.svc.on_payment_confirmed(inv, f"ref-{inv}", now=now)
                elif op == "dup_pay":
                    inv = rng.choice(invoices)
                    rig.svc.on_payment_confirmed(inv, f"ref-{inv}-x{rng.randrange(2)}", now=now)
                elif op == "assign":
                    rig.svc.on_teacher_assigned(sid, assignment_id="a", is_trial=False, now=now)
                elif op == "pause":
                    rig.svc.pause(sid, now=now, actor="u")
                elif op == "resume":
                    rig.svc.resume(sid, now=now, actor="u")
                elif op == "cancel":
                    rig.svc.cancel(sid, now=now, actor="u")
                elif op == "cancel_now":
                    rig.svc.cancel(sid, now=now, actor="u", immediate=True)
                elif op == "past_due":
                    rig.svc.mark_past_due(sid, now=now)
                elif op == "expire":
                    rig.svc.expire_past_due(sid, now=now)
                elif op == "plan":
                    rig.svc.change_plan(sid, rng.choice(["plan2", "plan3"]), now=now, actor="u")
                elif op == "roll":
                    rig.svc.roll_subscription(sid, now=now)
                    invoices = [i["$id"] for i in rig.repo.list("invoice")]
            except (ForbiddenTransition, GraceNotExpired, ConcurrentTransition):
                pass
            s = rig.svc.get(sid)
            # invariants
            assert s.status in Status
            invs = rig.repo.list("invoice")
            keys = [(i["subscription_id"], i["period_start_date"]) for i in invs]
            assert len(keys) == len(set(keys))
            for i in invs:
                assert type(i["amount"]) is int and i["amount"] >= 0
                assert i["amount"] <= i["plan_price_ugx"]
            assert len(rig.ledger.payments) <= len(
                {i["payment_ref"] for i in invs if i["payment_ref"]}
            )
            if s.status == Status.cancelled:
                assert s.cancelled_at is not None
            assert s.version == len(
                [
                    c
                    for c in rig.repo.list("subscription_transition")
                    if c["status"] in ("applied", "delivered")
                ]
            )
        # cancelled is absorbing
        # (a cancelled subscription never regains another status: checked via claims)
        sts = [
            c.get("to_status")
            for c in sorted(rig.repo.list("subscription_transition"), key=lambda c: c["to_version"])
            if c.get("to_status")
        ]
        if "cancelled" in sts:
            assert set(sts[sts.index("cancelled") :]) == {"cancelled"}


def test_the_late_payment_credit_never_exceeds_the_sessions_in_the_period():
    import random

    rng = random.Random(5)
    for _ in range(100):
        rig = make_rig()
        res = new_sub(rig, plan=rng.choice(["plan2", "plan3"]))
        rig.svc.on_payment_confirmed(
            res.invoice.id, "r", now=T0 + timedelta(days=rng.randrange(0, 90))
        )
        for c in rig.repo.list("billing_credit"):
            p = period(date(2027, 1, 15), 0)
            spw = 2 if res.invoice.plan_id == "plan2" else 3
            assert 0 < c["sessions"] <= spw * p.days // 7


def test_utc_vs_kampala_date_boundary_creation_at_2130_utc_is_next_kampala_day():
    rig = make_rig()
    res = new_sub(rig, now=datetime(2027, 1, 31, 21, 30, tzinfo=UTC))  # 1 Feb 00:30 Kampala
    assert res.subscription.anchor_date == date(2027, 2, 1)
    assert res.invoice.period_start_date == "2027-02-01"
    assert res.subscription.period_start_at == datetime(2027, 1, 31, 21, 0, tzinfo=UTC)


def test_month_end_anchor_renewals_do_not_drift():
    rig = make_rig(BillingConfig(renewal_lead_days=5))
    sid, _ = activate(rig, now=datetime(2027, 1, 31, 9, 0, tzinfo=UTC))
    s = rig.svc.get(sid)
    assert s.anchor_date == date(2027, 1, 31)
    i1 = rig.svc.generate_invoice(sid, 1, now=period(s.anchor_date, 1).start_at - 5 * DAY)
    assert i1.period_start_date == "2027-02-28" and i1.period_end_date == "2027-03-30"
    i2 = rig.svc.generate_invoice(sid, 1, now=period(s.anchor_date, 1).start_at - 4 * DAY)
    assert i2.id == i1.id
    p2 = period(s.anchor_date, 2)
    assert p2.start == date(2027, 3, 31)


def test_a_credit_applied_to_one_invoice_is_never_reassigned_to_another():
    rig = make_rig()
    sid, _ = activate(rig)
    c = rig.svc.record_platform_miss(sid, "s1", reason="x", now=T0)
    rig.svc.store.mark_credit_applied(c.id, "inv-a")
    rig.svc.store.mark_credit_applied(c.id, "inv-b")
    assert rig.svc.store.get_credit(c.id).applied_invoice_id == "inv-a"
    assert rig.svc.store.unapplied_credits(sid) == []
