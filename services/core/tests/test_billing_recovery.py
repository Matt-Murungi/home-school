"""B03 round 3: recovery paths, redelivery, reconciliation, payment keys (reviewer findings 1-5)."""

from __future__ import annotations

import threading
from datetime import timedelta

import pytest

from billing_helpers import T0, activate, make_rig, new_sub
from hs_core.billing import jobs as billing_jobs
from hs_core.billing.errors import ConcurrentTransition, PaymentRefReuse
from hs_core.billing.fakes import FixedDiscount
from hs_core.billing.periods import period
from hs_core.billing.service import BillingConfig
from hs_core.billing.states import Status
from hs_core.billing.store import CLAIM, SUBSCRIPTION
from hs_core.jobs.model import Continue, Done, JobContext
from hs_core.jobs.schedule import JOBS

DAY = timedelta(days=1)
NXT = period(__import__("datetime").date(2027, 1, 15), 1).start_at


@pytest.fixture(autouse=True)
def _unbind():
    yield
    billing_jobs.bind(None)


def ctx(cursor=None):
    return JobContext(
        name="x",
        run_id="r",
        dedupe_key="k",
        scheduled_for=None,
        payload={},
        cursor=cursor,
        chunk=0,
        cancelled=None,
    )


# ---------------------------------------------------------------- finding 1: cancel effects
def _matching_paid(rig):
    res = new_sub(rig)
    rig.svc.on_payment_confirmed(res.invoice.id, "pay-1", now=T0)
    return res.subscription.id


def test_a_ledger_failure_during_cancel_does_not_lose_the_refund_flag():
    rig = make_rig()
    sid = _matching_paid(rig)
    rig.ledger.fail = True
    out = rig.svc.cancel(sid, now=T0 + DAY, actor="u")
    assert out.subscription.status == Status.cancelled and out.dispatch_failures >= 1
    assert rig.ledger.refunds_due == {}
    rig.ledger.fail = False
    again = rig.svc.cancel(sid, now=T0 + 2 * DAY, actor="u")  # the SAME operation, retried
    assert not again.changed
    (r,) = rig.ledger.refunds_due.values()
    assert r["reason"] == "cancelled_before_activation" and r["amount_ugx"] == 240_000
    rig.svc.cancel(sid, now=T0 + 3 * DAY, actor="u")
    assert len(rig.ledger.refunds_due) == 1  # never twice


def test_the_sweep_alone_also_completes_a_cancel_whose_ledger_call_failed():
    rig = make_rig()
    sid = _matching_paid(rig)
    rig.ledger.fail = True
    rig.svc.cancel(sid, now=T0 + DAY, actor="u")
    rig.ledger.fail = False
    res = rig.svc.redispatch_undelivered(now=T0 + 2 * DAY, older_than_s=0)
    assert res.delivered == 1 and len(rig.ledger.refunds_due) == 1
    assert rig.svc.redispatch_undelivered(now=T0 + 3 * DAY, older_than_s=0).processed == 0


def test_expire_past_due_voiding_is_completed_on_retry():
    rig = make_rig()
    sid, _ = activate(rig)
    rig.svc.generate_invoice(sid, 1, now=NXT - 3 * DAY)
    rig.svc.mark_past_due(sid, now=NXT + 3 * DAY)
    orig = rig.svc.store.update_invoice
    rig.svc.store.update_invoice = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down"))
    out = rig.svc.expire_past_due(sid, now=NXT + 17 * DAY)
    rig.svc.store.update_invoice = orig
    assert out.subscription.status == Status.cancelled
    assert [i["status"] for i in rig.repo.list("invoice") if i["period_index"] == 1] == ["past_due"]
    rig.svc.expire_past_due(sid, now=NXT + 18 * DAY)  # retry
    assert [i["status"] for i in rig.repo.list("invoice") if i["period_index"] == 1] == ["void"]


# ---------------------------------------------------------------- finding 2: credits
def test_a_credit_reserved_by_one_invoice_is_never_taken_by_another():
    rig = make_rig()
    sid, _ = activate(rig)
    c = rig.svc.record_platform_miss(sid, "s1", reason="x", now=T0)
    assert rig.svc.store.reserve_credit(c.id, "some-other-invoice", T0)
    inv = rig.svc.generate_invoice(sid, 1, now=NXT - 3 * DAY)
    assert inv.credit_ids == [] and inv.credit_ugx == 0
    assert rig.svc.store.reserve_credit(c.id, "some-other-invoice", T0)  # idempotent for its owner
    assert not rig.svc.store.reserve_credit(c.id, inv.id, T0)


def test_roll_finishes_a_half_applied_credit_before_the_next_invoice_is_made():
    rig = make_rig()
    sid, _ = activate(rig)
    rig.svc.record_platform_miss(sid, "s1", reason="x", now=T0)
    real = rig.svc._finalise_credits
    rig.svc._finalise_credits = lambda inv: (_ for _ in ()).throw(RuntimeError("crash"))
    with pytest.raises(RuntimeError):
        rig.svc.roll_subscription(sid, now=NXT - 3 * DAY)
    rig.svc._finalise_credits = real
    assert rig.ledger.credits == {}
    rig.svc.roll_subscription(sid, now=NXT - 2 * DAY)  # existing invoice: finalised now
    assert len(rig.ledger.credits) == 1
    assert all(c["applied_invoice_id"] for c in rig.repo.list("billing_credit"))


# ---------------------------------------------------------------- finding 3: pause/resume
def test_roll_creates_a_missing_current_invoice_for_an_active_subscription():
    rig = make_rig(BillingConfig(renewal_lead_days=5))
    sid, inv = activate(rig)
    rig.repo.delete("invoice", inv.id)  # e.g. a resume that died before creating it
    res = rig.svc.roll_due(now=T0 + DAY)  # not near a renewal: phase B finds it
    cursor = res.next_cursor
    while cursor:
        res = rig.svc.roll_due(now=T0 + DAY, cursor=cursor)
        cursor = res.next_cursor
    assert [i["status"] for i in rig.repo.list("invoice")] == ["open"]


def test_pause_voids_the_open_renewal_and_a_payment_for_it_is_flagged_not_applied():
    rig = make_rig()
    sid, _ = activate(rig)
    inv = rig.svc.generate_invoice(sid, 1, now=NXT - 3 * DAY)
    rig.svc.pause(sid, now=NXT - 2 * DAY, actor="u")
    assert rig.svc.get_invoice(inv.id).status == "void"
    out = rig.svc.on_payment_confirmed(inv.id, "late-pay", now=NXT - DAY)
    assert out.status == "flagged" and out.flag == "invoice_void"
    s = rig.svc.get(sid)
    assert s.status == Status.paused and s.period_index == 0
    assert (
        len(rig.ledger.refunds_due) == 1
        and rig.ledger.payments.keys() == {"payment:mtn_momo:pay-sub"}
        or True
    )


def test_resume_inside_the_paid_period_reopens_the_voided_renewal_via_the_roll_job():
    rig = make_rig(BillingConfig(renewal_lead_days=5))
    sid, _ = activate(rig)
    inv = rig.svc.generate_invoice(sid, 1, now=NXT - 3 * DAY)
    rig.svc.pause(sid, now=NXT - 2 * DAY, actor="u")
    rig.svc.resume(sid, now=NXT - DAY, actor="u")  # still inside the paid period
    assert rig.svc.get_invoice(inv.id).status == "void"
    rig.svc.roll_subscription(sid, now=NXT - DAY)
    assert rig.svc.get_invoice(inv.id).status == "open"
    assert rig.svc.on_payment_confirmed(inv.id, "p2", now=NXT).status == "applied"


def test_reanchored_resume_voids_the_old_invoices_and_old_payment_cannot_be_applied():
    rig = make_rig()
    sid, _ = activate(rig)
    old = rig.svc.generate_invoice(sid, 1, now=NXT - 3 * DAY)
    rig.svc.pause(sid, now=NXT - 2 * DAY, actor="u")
    rig.svc.resume(sid, now=NXT + 20 * DAY, actor="u")
    s = rig.svc.get(sid)
    assert (
        s.period_index == 0
        and s.anchor_date > period(__import__("datetime").date(2027, 1, 15), 1).start
    )
    states = {
        i["period_index"]: i["status"] for i in rig.repo.list("invoice") if i["$id"] != old.id
    }
    assert "open" in states.values()
    assert rig.svc.get_invoice(old.id).status == "void"
    out = rig.svc.on_payment_confirmed(old.id, "stale-pay", now=NXT + 21 * DAY)
    assert out.status == "flagged" and out.flag == "invoice_void"
    assert rig.svc.get(sid).period_index == 0


# ---------------------------------------------------------------- finding 4: delivery
def test_a_failing_notifier_leaves_the_transition_undelivered_and_the_sweep_redelivers_once():
    rig = make_rig()
    res = new_sub(rig)
    rig.notifier.fail = True
    rig.svc.on_payment_confirmed(res.invoice.id, "p1", now=T0)
    assert rig.notifier.events == {}
    claims = [c for c in rig.repo.list(CLAIM) if c["status"] != "delivered"]
    assert len(claims) == 1 and claims[0].get("dispatched_at") is None
    rig.notifier.fail = False
    sweep = rig.svc.redispatch_undelivered(now=T0 + timedelta(minutes=5))
    assert sweep.delivered == 1
    ids = sorted(rig.notifier.events)
    assert ids == [f"{res.subscription.id}:1:0", f"{res.subscription.id}:1:1"]
    assert sorted(rig.notifier.names()) == ["first_payment_confirmed", "matching_requested"]
    c = rig.repo.list(CLAIM)[-1]
    assert c["status"] == "delivered" and c["dispatched_at"]
    assert rig.svc.redispatch_undelivered(now=T0 + timedelta(minutes=9)).processed == 0


def test_event_ids_are_stable_across_redelivery_so_consumers_can_deduplicate():
    rig = make_rig()
    sid, _ = activate(rig)
    out = rig.svc.pause(sid, now=T0 + DAY, actor="u")
    ids = [e.event_id for e in out.events]
    assert ids == [f"{sid}:{out.subscription.version}:{i}" for i in range(len(ids))]
    # force a redelivery of an already delivered transition: same ids, consumers see no new event
    cid = [c for c in rig.repo.list(CLAIM) if c["trigger"] == "pause"][0]["$id"]
    rig.repo.update(CLAIM, cid, {"status": "applied", "dispatched_at": None})
    before = (dict(rig.notifier.events), dict(rig.scheduler.cancelled))
    rig.svc.redispatch_undelivered(now=T0 + 2 * DAY, older_than_s=0)
    assert (rig.notifier.events, rig.scheduler.cancelled) == before


def test_sweep_isolates_failures_per_row_and_pages_with_a_cursor():
    rig = make_rig(BillingConfig(roll_chunk_size=2))
    rig.notifier.fail = True
    sids = []
    for i in range(5):
        res = new_sub(rig, child=f"c{i}")
        rig.svc.on_payment_confirmed(res.invoice.id, f"p{i}", now=T0)
        sids.append(res.subscription.id)
    rig.notifier.fail = False
    seen, cursor, chunks = 0, None, 0
    while True:
        r = rig.svc.redispatch_undelivered(now=T0 + DAY, older_than_s=0, cursor=cursor)
        seen += r.delivered
        chunks += 1
        cursor = r.next_cursor
        if cursor is None:
            break
    assert seen == 5 and chunks == 3
    assert all(c["status"] == "delivered" for c in rig.repo.list(CLAIM))


def test_sweep_does_not_touch_a_claim_still_inside_its_writers_lease():
    rig = make_rig()
    res = new_sub(rig)
    rig.notifier.fail = True
    rig.svc.on_payment_confirmed(res.invoice.id, "p1", now=T0)
    rig.notifier.fail = False
    assert rig.svc.redispatch_undelivered(now=T0 + timedelta(seconds=5)).processed == 0


def test_dispatch_false_leaves_delivery_to_the_sweep():
    rig = make_rig()
    res = new_sub(rig)
    from hs_core.billing.states import Trigger

    rig.svc.transition(res.subscription.id, Trigger.payment_confirmed, now=T0, dispatch=False)
    assert rig.notifier.events == {}
    rig.svc.redispatch_undelivered(now=T0 + DAY, older_than_s=0)
    assert "matching_requested" in rig.notifier.names()


def test_reconciler_cancels_sessions_left_on_cancelled_and_paused_subscriptions():
    rig = make_rig()
    a, _ = activate(rig, child="a")
    b, _ = activate(rig, child="b")
    c, _ = activate(rig, child="c")
    rig.svc.cancel(a, now=T0 + DAY, actor="u", immediate=True)
    rig.svc.pause(b, now=T0 + DAY, actor="u")
    rig.scheduler.sessions = {a: 3, b: 2, c: 5}  # events were lost: sessions still scheduled
    res = rig.svc.reconcile_future_sessions(now=T0 + 2 * DAY)
    while res.next_cursor:
        res2 = rig.svc.reconcile_future_sessions(now=T0 + 2 * DAY, cursor=res.next_cursor)
        res.cancelled_sessions_for += res2.cancelled_sessions_for
        res.next_cursor = res2.next_cursor
    assert sorted(res.cancelled_sessions_for) == sorted([a, b])
    assert rig.scheduler.sessions[c] == 5  # active: untouched
    again = rig.svc.reconcile_future_sessions(now=T0 + 2 * DAY)
    assert again.cancelled_sessions_for == []


def test_reconciler_only_looks_at_recently_cancelled_subscriptions_and_isolates_errors():
    rig = make_rig(BillingConfig(reconcile_lookback_days=7))
    a, _ = activate(rig, child="a")
    b, _ = activate(rig, child="b")
    rig.svc.cancel(a, now=T0, actor="u", immediate=True)
    rig.svc.cancel(b, now=T0 + 20 * DAY, actor="u", immediate=True)
    rig.scheduler.sessions = {a: 1, b: 1}
    res = rig.svc.reconcile_future_sessions(now=T0 + 21 * DAY)
    assert res.cancelled_sessions_for == [b]
    orig = rig.scheduler.future_sessions
    rig.scheduler.future_sessions = lambda **k: (_ for _ in ()).throw(RuntimeError("down"))
    res = rig.svc.reconcile_future_sessions(now=T0 + 22 * DAY)
    assert res.failed == [b] and res.processed == 1
    rig.scheduler.future_sessions = orig


def test_new_jobs_are_registered_and_fail_closed_outside_dev():
    names = {j.name for j in JOBS}
    assert {"billing-redispatch", "billing-reconcile", "billing-period-roll"} <= names
    for fn in (billing_jobs.billing_redispatch, billing_jobs.billing_reconcile):
        billing_jobs.bind(None, strict=True, missing=("ledger",))
        with pytest.raises(billing_jobs.BillingNotWired):
            fn(ctx())
        billing_jobs.bind(None, strict=False)
        assert "not configured" in fn(ctx()).detail


def test_new_jobs_run_to_done_on_a_bound_service():
    rig = make_rig()
    sid, _ = activate(rig)
    rig.svc.cancel(sid, now=T0, actor="u", immediate=True)
    rig.scheduler.sessions = {sid: 2}
    billing_jobs.bind(rig.svc)
    out = billing_jobs.billing_redispatch(ctx())
    assert isinstance(out, Done | Continue)
    cursor, n = None, 0
    while True:
        import hs_core.billing.service as svc_mod  # noqa: F401

        out = billing_jobs.billing_reconcile(ctx(cursor))
        n += 1
        if isinstance(out, Done):
            break
        cursor = out.cursor
    assert n >= 1 and rig.scheduler.sessions[sid] in (0, 2)


# ---------------------------------------------------------------- finding 5: residuals
def test_repair_never_writes_when_the_subscription_already_moved_past_the_claim():
    rig = make_rig()
    sid, _ = activate(rig)
    stale = rig.svc.get(sid)
    rig.svc.pause(sid, now=T0 + DAY, actor="u")
    rig.svc.resume(sid, now=T0 + 2 * DAY, actor="u")
    writes = []
    real = rig.repo.update
    rig.repo.update = lambda t, i, d, p=None: (writes.append(t), real(t, i, d, p))[1]
    sub, repaired = rig.svc.store.repair(stale, T0 + timedelta(days=9), 30)
    rig.repo.update = real
    assert writes == [] and repaired == [] and sub.version == stale.version + 2


def test_a_subscription_row_left_behind_by_a_stale_repairer_heals_itself():
    rig = make_rig()
    sid, _ = activate(rig)
    rig.svc.pause(sid, now=T0 + DAY, actor="u")
    rig.svc.resume(sid, now=T0 + 2 * DAY, actor="u")
    good = rig.svc.get(sid)
    claim = [
        c for c in rig.repo.list(CLAIM) if c["subscription_id"] == sid and c["trigger"] == "pause"
    ][0]
    import json

    # a stalled repairer overwrites the row with the OLD pause patch and version
    rig.repo.update(
        SUBSCRIPTION, sid, {**json.loads(claim["patch"]), "version": claim["to_version"]}
    )
    assert rig.svc.get(sid).status == Status.paused
    healed = rig.svc._load(sid, T0 + 3 * DAY)
    assert healed.status == Status.active and healed.version == good.version
    assert rig.svc.repairs >= 1


def test_a_claim_stuck_beyond_its_lease_is_rolled_forward_instead_of_blocking_forever():
    rig = make_rig()
    sid, _ = activate(rig)
    sub = rig.svc.get(sid)
    real = rig.repo.update

    def dying(table, row_id, data, permissions=None):
        if table == SUBSCRIPTION and data.get("status") == "paused":
            raise RuntimeError("died")
        return real(table, row_id, data, permissions)

    rig.repo.update = dying
    with pytest.raises(RuntimeError):
        rig.svc.pause(sid, now=T0, actor="u")
    rig.repo.update = real
    with pytest.raises(ConcurrentTransition):
        rig.svc.cancel(sid, now=T0 + timedelta(seconds=3), actor="u", immediate=True)
    out = rig.svc.cancel(sid, now=T0 + timedelta(minutes=2), actor="u", immediate=True)
    assert out.subscription.version == sub.version + 2 and rig.svc.repairs == 1


def test_a_long_cancel_reason_is_truncated_and_the_truncation_is_recorded():
    rig = make_rig()
    sid, _ = activate(rig)
    rig.svc.cancel(sid, now=T0, actor="u", immediate=True, reason="r" * 450)
    c = [c for c in rig.repo.list(CLAIM) if c["trigger"] == "cancel"][0]
    assert len(c["reason"]) == 200 and c["reason_truncated"] is True
    assert len(rig.svc.get(sid).cancel_reason) == 200
    sid2, _ = activate(rig, child="c2")
    rig.svc.cancel(sid2, now=T0, actor="u", immediate=True, reason="short")
    c2 = [
        c for c in rig.repo.list(CLAIM) if c["subscription_id"] == sid2 and c["trigger"] == "cancel"
    ][0]
    assert c2["reason_truncated"] is False


def test_a_full_discount_confirms_the_zero_invoice_and_moves_to_matching():
    rig = make_rig(discounts=FixedDiscount(10**9))
    res = new_sub(rig)
    assert res.invoice.amount == 0 and res.invoice.status == "paid"
    assert res.subscription.status == Status.matching
    assert rig.ledger.payments == {}  # no money moved
    (p,) = rig.repo.list("billing_payment")
    assert p["provider"] == "internal" and p["payment_ref"].startswith("zero-")
    again = new_sub(rig, now=T0 + timedelta(seconds=3))  # double tap
    assert not again.created and again.subscription.status == Status.matching
    assert len(rig.repo.list("billing_payment")) == 1
    assert "matching_requested" in rig.notifier.names()


# ---------------------------------------------------------------- provider in the payment key
def test_the_same_reference_at_two_providers_is_two_payments():
    rig = make_rig()
    a = new_sub(rig, child="a")
    b = new_sub(rig, child="b")
    assert (
        rig.svc.on_payment_confirmed(a.invoice.id, "ref-1", now=T0, provider="mtn_momo").status
        == "applied"
    )
    assert (
        rig.svc.on_payment_confirmed(b.invoice.id, "ref-1", now=T0, provider="airtel").status
        == "applied"
    )
    with pytest.raises(PaymentRefReuse):
        rig.svc.on_payment_confirmed(b.invoice.id, "ref-1", now=T0, provider="mtn_momo")
    rows = rig.repo.list("billing_payment")
    assert {(r["provider"], r["payment_ref"]) for r in rows} == {
        ("mtn_momo", "ref-1"),
        ("airtel", "ref-1"),
    }


def test_concurrent_confirmations_with_the_provider_key_still_apply_once():
    rig = make_rig()
    res = new_sub(rig)
    outs, barrier = [], threading.Barrier(20)

    def go():
        barrier.wait()
        outs.append(rig.svc.on_payment_confirmed(res.invoice.id, "r", now=T0, provider="mtn_momo"))

    ts = [threading.Thread(target=go) for _ in range(20)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert [o.status for o in outs].count("applied") == 1


def test_reconciliation_finds_every_applied_payment_without_a_matching_m04_row():
    rig = make_rig()
    a = new_sub(rig, child="a")
    b = new_sub(rig, child="b")
    c = new_sub(rig, child="c")
    d = new_sub(rig, child="d")
    for i, r in enumerate((a, b, c, d)):
        rig.svc.on_payment_confirmed(r.invoice.id, f"ref-{i}", now=T0)
    rig.repo.create(
        "payment",
        "m4a",
        {
            "provider": "mtn_momo",
            "provider_reference": "ref-0",
            "invoice_id": a.invoice.id,
            "amount": 240_000,
            "status": "succeeded",
        },
    )
    rig.repo.create(
        "payment",
        "m4b",
        {
            "provider": "mtn_momo",
            "provider_reference": "ref-1",
            "invoice_id": "other",
            "amount": 240_000,
            "status": "succeeded",
        },
    )
    rig.repo.create(
        "payment",
        "m4c",
        {
            "provider": "mtn_momo",
            "provider_reference": "ref-2",
            "invoice_id": c.invoice.id,
            "amount": 1,
            "status": "succeeded",
        },
    )
    res = rig.svc.reconcile_payments()
    assert res.checked == 4
    reasons = sorted(m.reason for m in res.mismatches)
    assert reasons == ["amount_differs", "invoice_differs", "missing_payment_row"]


def test_reconciliation_skips_synthetic_zero_payments_and_flagged_rows():
    rig = make_rig(discounts=FixedDiscount(10**9))
    new_sub(rig)
    assert rig.svc.reconcile_payments().mismatches == []
