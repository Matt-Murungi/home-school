"""Acceptance criteria and edge cases of #76 at the service level, on BOTH stores."""

from __future__ import annotations

import pytest

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
from hs_core.ledger.errors import (
    DuplicateLine,
    IdempotencyConflict,
    InvalidAmount,
    InvalidTransaction,
    MixedCurrencies,
    TooFewLines,
    UnbalancedTransaction,
    UnknownAccount,
)
from hs_core.ledger.model import Kind, TransactionRequest, credit, debit
from hs_core.ledger.money import MAX_AMOUNT, Money
from ledger_helpers import Env, tx


@pytest.fixture(params=["appwrite", "memory"])
def env(request):
    return Env(request.param)


def test_post_writes_header_and_lines_and_returns_posted(env):
    posted = env.svc.post(tx())
    assert posted.entry_count == 2 and posted.total_amount == 10_000
    assert {(e.side.value, e.amount) for e in posted.lines} == {
        ("debit", 10_000),
        ("credit", 10_000),
    }
    assert posted.replayed is False
    assert env.svc.get_transaction(posted.id) == posted


def test_unbalanced_is_rejected_and_writes_nothing(env):
    bad = TransactionRequest(
        "payment:x", Kind.PAYMENT, (debit(CASH_MTN, 100), credit(RECEIVABLES, 99))
    )
    before = env.faulty.writes
    with pytest.raises(UnbalancedTransaction):
        env.svc.post(bad)
    assert env.faulty.writes == before
    assert env.svc.get_by_key("payment:x") is None


@pytest.mark.parametrize("amount", [0, -1, -10_000, 1.5, 100.0, True, False, "100", None, 10.00])
def test_bad_amounts_rejected(env, amount):
    with pytest.raises(InvalidAmount):
        env.svc.post(tx("payment:a", amount))


def test_huge_amounts_rejected_and_ceiling_accepted(env):
    for n in (MAX_AMOUNT + 1, 2**63, 2**64, 10**30):
        with pytest.raises(InvalidAmount):
            env.svc.post(tx("payment:h", n))
    ok = env.svc.post(tx("payment:ceiling", MAX_AMOUNT))
    assert ok.total_amount == MAX_AMOUNT
    assert env.svc.balance(CASH_MTN) == Money(MAX_AMOUNT)


def test_unknown_account_rejected(env):
    with pytest.raises(UnknownAccount):
        env.svc.post(tx("payment:u", credit_code="receivable:nobody"))
    with pytest.raises(UnknownAccount):
        env.svc.post(tx("payment:u", credit_code=teacher_payable_code("never-created")))


def test_empty_and_single_line_rejected(env):
    with pytest.raises(TooFewLines):
        env.svc.post(TransactionRequest("adjustment:e", Kind.ADJUSTMENT, ()))
    with pytest.raises(TooFewLines):
        env.svc.post(TransactionRequest("adjustment:s", Kind.ADJUSTMENT, (debit(CASH_MTN, 5),)))


def test_duplicate_lines_rejected(env):
    dup = TransactionRequest(
        "adjustment:d",
        Kind.ADJUSTMENT,
        (debit(CASH_MTN, 5), debit(CASH_MTN, 5), credit(RECEIVABLES, 10)),
    )
    with pytest.raises(DuplicateLine):
        env.svc.post(dup)
    both = TransactionRequest(
        "adjustment:d2", Kind.ADJUSTMENT, (debit(CASH_MTN, 5), credit(CASH_MTN, 5))
    )
    with pytest.raises(DuplicateLine):
        env.svc.post(both)


def test_mixed_currencies_rejected(env):
    req = TransactionRequest(
        "payment:m", Kind.PAYMENT, (debit(CASH_MTN, 5), credit(RECEIVABLES, 5)), currency="USD"
    )
    with pytest.raises(MixedCurrencies):
        env.svc.post(req)  # accounts are UGX, request says USD


@pytest.mark.parametrize(
    "bad_key", ["", "nokind", "Payment:1", "payment:", "payment:" + "x" * 200, 5, None]
)
def test_idempotency_key_shape_is_enforced(env, bad_key):
    with pytest.raises(InvalidTransaction):
        env.svc.post(tx(bad_key))


def test_unknown_kind_and_bad_side_rejected(env):
    with pytest.raises(InvalidTransaction):
        env.svc.post(tx(kind="gift"))
    req = TransactionRequest(
        "payment:s",
        Kind.PAYMENT,
        (debit(CASH_MTN, 5), type(debit(CASH_MTN, 1))(RECEIVABLES, "left", 5)),
    )
    with pytest.raises(InvalidTransaction):
        env.svc.post(req)


def test_replay_same_key_same_content_returns_original(env):
    first = env.svc.post(tx())
    writes = env.faulty.writes
    again = env.svc.post(tx())
    assert again.id == first.id and again.replayed is True
    assert again.posted_at == first.posted_at
    assert env.faulty.writes == writes or env.kind == "memory"
    assert env.svc.balance(CASH_MTN) == Money(10_000)


def test_replay_with_lines_in_another_order_is_the_same_content(env):
    env.svc.post(tx())
    swapped = TransactionRequest(
        "payment:p1",
        Kind.PAYMENT,
        (credit(RECEIVABLES, 10_000), debit(CASH_MTN, 10_000)),
        reference_type="payment",
        reference_id="p1",
    )
    assert env.svc.post(swapped).replayed is True


@pytest.mark.parametrize(
    "change",
    [
        {"amount": 10_001},
        {"debit_code": CASH_AIRTEL},
        {"credit_code": REVENUE},
        {"reference_id": "p2"},
        {"description": "other"},
        {"created_by_id": "admin1"},
        {"kind": Kind.ADJUSTMENT},
    ],
)
def test_same_key_different_content_is_a_named_conflict_and_changes_nothing(env, change):
    first = env.svc.post(tx())
    writes = env.faulty.writes
    with pytest.raises(IdempotencyConflict) as e:
        env.svc.post(tx(**change))
    assert e.value.key == "payment:p1" and e.value.fields
    assert env.faulty.writes == writes or env.kind == "memory"
    assert env.svc.get_transaction(first.id) == first
    assert env.svc.balance(CASH_MTN) == Money(10_000)


def test_balances_derive_from_entries_and_follow_normal_sides(env):
    env.svc.post(tx("payment:p1", 10_000))
    env.svc.post(tx("payment:p2", 2_500, debit_code=CASH_AIRTEL))
    env.svc.post(
        tx(
            "fee:f1",
            300,
            kind=Kind.FEE,
            debit_code=PROVIDER_FEES,
            credit_code=CASH_MTN,
            reference_type="fee",
            reference_id="f1",
        )
    )
    env.svc.post(
        tx(
            "refund:r1",
            1_000,
            kind=Kind.REFUND,
            debit_code=REFUNDS_CREDITS,
            credit_code=CASH_MTN,
            reference_type="refund",
            reference_id="r1",
        )
    )
    assert env.svc.balance(CASH_MTN) == Money(10_000 - 300 - 1_000)  # asset, debit normal
    assert env.svc.balance(CASH_AIRTEL) == Money(2_500)
    assert env.svc.balance(RECEIVABLES) == Money(-12_500)  # credited more than debited
    assert env.svc.balance(PROVIDER_FEES) == Money(300)
    assert env.svc.balance(REFUNDS_CREDITS) == Money(1_000)  # contra revenue: debit normal
    tb = env.svc.trial_balance()
    assert tb.is_balanced and tb.net == {"UGX": 0}


def test_balance_as_of(env):
    a = env.svc.post(tx("payment:p1", 100))
    env.clock.advance(hours=1)
    env.svc.post(tx("payment:p2", 50))
    from datetime import datetime

    cut = datetime.fromisoformat(a.posted_at)
    assert env.svc.balance(CASH_MTN, as_of=cut) == Money(100)
    assert env.svc.balance(CASH_MTN) == Money(150)
    assert env.svc.trial_balance(as_of=cut).is_balanced


def test_entries_reference_their_source(env):
    p = env.svc.post(tx("payment:p1", 100, reference_type="payment", reference_id="p1"))
    assert all(e.reference_type == "payment" and e.reference_id == "p1" for e in p.lines)
    assert p.reference_type == "payment" and p.reference_id == "p1"


def test_reversal_posts_a_new_contra_transaction_and_never_edits_the_original(env):
    orig = env.svc.post(tx("payment:p1", 700))
    rev = env.svc.reverse(orig.id, reason="duplicate charge", created_by_id="admin1")
    assert rev.kind is Kind.REVERSAL and rev.reverses_transaction_id == orig.id
    assert rev.id != orig.id and rev.idempotency_key == f"reversal:{orig.id}"
    assert env.svc.get_transaction(orig.id) == orig  # untouched
    assert env.svc.balance(CASH_MTN) == Money(0) and env.svc.balance(RECEIVABLES) == Money(0)
    again = env.svc.reverse(orig.id, reason="duplicate charge", created_by_id="admin1")
    assert again.id == rev.id and again.replayed
    with pytest.raises(IdempotencyConflict):
        env.svc.reverse(orig.id, reason="another reason", created_by_id="admin1")


def test_reversal_rules(env):
    from hs_core.ledger.errors import UnknownTransaction

    orig = env.svc.post(tx("payment:p1", 700))
    rev = env.svc.reverse(orig.id, reason="r")
    with pytest.raises(UnknownTransaction):
        env.svc.reverse("tdoesnotexist", reason="r")
    with pytest.raises(InvalidTransaction):  # a reversal cannot be reversed
        env.svc.reverse(rev.id, reason="r")
    # a hand-made "reversal" that does not mirror the original is refused
    fake = TransactionRequest(
        "reversal:" + orig.id,
        Kind.REVERSAL,
        (debit(CASH_MTN, 1), credit(RECEIVABLES, 1)),
        reverses_transaction_id=orig.id,
        reversal_reason="x",
    )
    with pytest.raises(InvalidTransaction):
        env.svc.post(fake)
    # reversal fields on a non-reversal are refused
    with pytest.raises(InvalidTransaction):
        env.svc.post(tx("payment:q", reverses_transaction_id=orig.id))


def test_audit_entry_written_per_post_and_pii_free(env):
    p = env.svc.post(tx("payment:p1", 100, description="Jane Doe 0772123456"))
    ev = env.audit.events[f"ledger:{p.id}"]
    assert ev.action == "ledger.post" and ev.entity_id == p.id
    blob = repr(ev)
    assert "Jane" not in blob and "0772" not in blob


def test_audit_failure_after_seal_is_recovered_by_retry(env):
    env.audit.fail_next = 1
    from hs_core.ledger.errors import AuditFailed

    with pytest.raises(AuditFailed):
        env.svc.post(tx("payment:p1", 100))
    assert env.svc.balance(CASH_MTN) == Money(100)  # sealed
    again = env.svc.post(tx("payment:p1", 100))
    assert again.replayed and f"ledger:{again.id}" in env.audit.events
    assert env.svc.balance(CASH_MTN) == Money(100)


def test_chart_of_accounts_is_complete_and_idempotent(env):
    accounts = env.svc.ensure_chart_of_accounts()
    again = env.svc.ensure_chart_of_accounts()
    assert accounts == again
    assert set(accounts) == {
        RECEIVABLES,
        CASH_MTN,
        CASH_AIRTEL,
        CASH_CARD,
        REVENUE,
        REFUNDS_CREDITS,
        PROVIDER_FEES,
    }
    assert len(env.store.list_accounts()) == 7
    assert (
        accounts[CASH_MTN].type.value == "asset" and accounts[CASH_MTN].normal_side.value == "debit"
    )
    assert (
        accounts[REVENUE].type.value == "revenue"
        and accounts[REVENUE].normal_side.value == "credit"
    )
    assert accounts[REFUNDS_CREDITS].is_contra and accounts[REFUNDS_CREDITS].type.value == "revenue"
    assert accounts[PROVIDER_FEES].type.value == "expense"


def test_teacher_payable_account_created_on_demand_once(env):
    a = env.svc.ensure_teacher_payable("teacher-1")
    b = env.svc.ensure_teacher_payable("teacher-1")
    assert a == b and a.code == "teacher_payable:teacher-1" and a.teacher_id == "teacher-1"
    assert a.type.value == "liability" and a.normal_side.value == "credit"
    assert len(env.store.list_accounts()) == 8
    for bad in ["", "a b", "x" * 37, "../x", None, 5]:
        with pytest.raises(InvalidTransaction):
            env.svc.ensure_teacher_payable(bad)


def test_money_value_object():
    assert Money(5) + Money(7) == Money(12)
    assert (Money(5) - Money(7)).amount == -2
    for bad in (1.5, True, "5", None, 2**200):
        with pytest.raises(InvalidAmount):
            Money(bad)
    with pytest.raises(MixedCurrencies):
        Money(1) + Money(1, "USD")
