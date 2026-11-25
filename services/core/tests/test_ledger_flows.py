"""Posting helpers for the SR flows (pure), idempotency key conventions, reconciliation."""

from __future__ import annotations

import pytest

from hs_core.ledger import flows
from hs_core.ledger.chart import (
    CASH_AIRTEL,
    CASH_CARD,
    CASH_MTN,
    PROVIDER_FEES,
    RECEIVABLES,
    REFUNDS_CREDITS,
    REVENUE,
    teacher_payable_code,
)
from hs_core.ledger.errors import InvalidAmount, InvalidTransaction
from hs_core.ledger.model import Kind, TransactionRequest
from hs_core.ledger.money import Money
from hs_core.ledger.reconcile import ProviderPayment, reconcile_cash, reconcile_payments
from ledger_helpers import Env


@pytest.fixture(params=["appwrite", "memory"])
def env(request):
    e = Env(request.param)
    e.svc.ensure_teacher_payable("t1")
    return e


def lines_of(req: TransactionRequest):
    return sorted((ln.account_code, str(ln.side), ln.amount) for ln in req.lines)


def test_helpers_are_pure_and_return_requests():
    req = flows.payment_received("p1", 500, "mtn_momo", invoice_id="i1")
    assert isinstance(req, TransactionRequest) and req.idempotency_key == "payment:p1"
    assert flows.payment_received("p1", 500, flows.Provider.MTN_MOMO, invoice_id="i1") == req


@pytest.mark.parametrize(
    ("req", "key", "kind", "dr", "cr"),
    [
        (flows.invoice_issued("i1", 900), "invoice:i1", Kind.ADJUSTMENT, RECEIVABLES, REVENUE),
        (
            flows.payment_received("p1", 900, "mtn_momo"),
            "payment:p1",
            Kind.PAYMENT,
            CASH_MTN,
            RECEIVABLES,
        ),
        (
            flows.payment_received("p2", 900, "airtel_money"),
            "payment:p2",
            Kind.PAYMENT,
            CASH_AIRTEL,
            RECEIVABLES,
        ),
        (
            flows.payment_received("p3", 900, "card_gateway"),
            "payment:p3",
            Kind.PAYMENT,
            CASH_CARD,
            RECEIVABLES,
        ),
        (
            flows.earning_accrued("e1", "t1", 900),
            "earning:e1",
            Kind.EARNING,
            REVENUE,
            teacher_payable_code("t1"),
        ),
        (
            flows.payout_disbursed("o1", "t1", 900, "mtn_momo"),
            "payout:o1",
            Kind.PAYOUT,
            teacher_payable_code("t1"),
            CASH_MTN,
        ),
        (
            flows.refund_paid("r1", 900, "mtn_momo"),
            "refund:r1",
            Kind.REFUND,
            REFUNDS_CREDITS,
            CASH_MTN,
        ),
        (flows.credit_issued("c1", 900), "credit:c1", Kind.REFUND, REFUNDS_CREDITS, RECEIVABLES),
        (
            flows.provider_fee("f1", 900, "airtel_money"),
            "fee:f1",
            Kind.FEE,
            PROVIDER_FEES,
            CASH_AIRTEL,
        ),
    ],
)
def test_each_flow_debits_and_credits_the_documented_accounts(req, key, kind, dr, cr):
    assert req.idempotency_key == key and req.kind == kind
    assert lines_of(req) == sorted([(dr, "debit", 900), (cr, "credit", 900)])
    assert req.reference_id == key.split(":", 1)[1]


def test_negative_earning_flips_sides_and_zero_or_bool_is_still_refused(env):
    claw = flows.earning_accrued("e2", "t1", -400)
    assert lines_of(claw) == sorted(
        [(teacher_payable_code("t1"), "debit", 400), (REVENUE, "credit", 400)]
    )
    for bad in (0, True, 2.5):
        with pytest.raises(InvalidAmount):
            env.svc.post(flows.earning_accrued("e3", "t1", bad))


def test_bad_inputs_to_helpers():
    with pytest.raises(InvalidTransaction):
        flows.payment_received("p1", 5, "paypal")
    for bad_id in ("", "has space", "x" * 37, "a:b"):
        with pytest.raises(InvalidTransaction):
            flows.invoice_issued(bad_id, 5)
    with pytest.raises(InvalidTransaction):
        flows.earning_accrued("e1", "bad teacher", 5)
    with pytest.raises(InvalidTransaction):
        flows.adjustment("a1", [], reason=" ", created_by_id="admin")


def test_full_lifecycle_balances_and_trial_balance_zero(env):
    post = env.svc.post
    post(flows.invoice_issued("i1", 100_000))
    post(flows.payment_received("p1", 100_000, "mtn_momo", invoice_id="i1"))
    post(flows.provider_fee("f1", 1_500, "mtn_momo"))
    post(flows.earning_accrued("e1", "t1", 70_000))
    post(flows.payout_disbursed("o1", "t1", 70_000, "mtn_momo"))
    post(flows.credit_issued("c1", 5_000))
    post(flows.refund_paid("r1", 2_000, "mtn_momo"))
    b = env.svc.balance
    assert b(RECEIVABLES) == Money(100_000 - 100_000 - 5_000)
    assert b(CASH_MTN) == Money(100_000 - 1_500 - 70_000 - 2_000)
    assert b(teacher_payable_code("t1")) == Money(0)
    assert b(REVENUE) == Money(100_000 - 70_000)
    assert b(PROVIDER_FEES) == Money(1_500) and b(REFUNDS_CREDITS) == Money(7_000)
    assert env.svc.trial_balance().is_balanced
    # every key is replay-safe
    assert env.svc.post(flows.payment_received("p1", 100_000, "mtn_momo", invoice_id="i1")).replayed


def test_adjustment_with_reason_and_actor(env):
    from hs_core.ledger.model import credit, debit

    req = flows.adjustment(
        "a1",
        [debit(CASH_MTN, 50), credit(REVENUE, 50)],
        reason="bank correction",
        created_by_id="admin1",
    )
    posted = env.svc.post(req)
    assert posted.kind is Kind.ADJUSTMENT and posted.created_by_id == "admin1"
    assert posted.description == "bank correction"


def test_reconcile_payments_matches_flags_missing_extra_and_mismatch(env):
    env.svc.post(flows.payment_received("p1", 1_000, "mtn_momo"))
    env.svc.post(flows.payment_received("p2", 2_000, "mtn_momo"))
    env.svc.post(flows.payment_received("p3", 3_000, "mtn_momo"))
    rev = env.svc.post(flows.payment_received("p4", 500, "mtn_momo"))
    env.svc.reverse(rev.id, reason="charged twice")  # reversed: counts as absent
    env.svc.post(flows.payment_received("pa", 700, "airtel_money"))  # other provider: ignored
    ok = reconcile_payments(
        env.svc,
        "mtn_momo",
        [ProviderPayment("p1", 1_000), ProviderPayment("p2", 2_000), ProviderPayment("p3", 3_000)],
    )
    assert ok.ok and ok.provider_total == ok.ledger_total == 6_000
    bad = reconcile_payments(
        env.svc,
        "mtn_momo",
        [ProviderPayment("p1", 1_000), ProviderPayment("p2", 1_999), ProviderPayment("p9", 40)],
    )
    assert not bad.ok
    assert bad.missing_in_ledger == ("p9",) and bad.unexpected_in_ledger == ("p3",)
    assert bad.amount_mismatch == ("p2",)
    with pytest.raises(InvalidTransaction):
        reconcile_payments(
            env.svc, "mtn_momo", [ProviderPayment("p1", 1), ProviderPayment("p1", 1)]
        )
    with pytest.raises(InvalidAmount):
        reconcile_payments(env.svc, "mtn_momo", [ProviderPayment("p1", 1.5)])


def test_reconcile_cash_balance(env):
    env.svc.post(flows.payment_received("p1", 1_000, "mtn_momo"))
    env.svc.post(flows.provider_fee("f1", 100, "mtn_momo"))
    good = reconcile_cash(env.svc, "mtn_momo", 900)
    assert good.ok and good.difference == 0
    off = reconcile_cash(env.svc, "mtn_momo", 950)
    assert not off.ok and off.difference == 50
    with pytest.raises(InvalidTransaction):
        reconcile_cash(env.svc, "mtn_momo", 900.0)
