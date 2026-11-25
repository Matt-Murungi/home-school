"""Invariant checks used twice: by the normal suite (must pass on the real code) and by
test_ledger_mutations.py (must FAIL when the code is mutated). Each raises AssertionError when
its invariant is violated."""

from __future__ import annotations

from hs_core.append_only import AppendOnlyRepository, AppendOnlyViolation
from hs_core.ledger.chart import CASH_MTN, RECEIVABLES
from hs_core.ledger.errors import IdempotencyConflict, InvalidAmount, UnbalancedTransaction
from hs_core.ledger.model import Kind, TransactionRequest, credit, debit
from hs_core.ledger.money import Money
from hs_core.ledger.testing import CrashInjected
from ledger_helpers import Env, tx


def check_unbalanced_is_rejected() -> None:
    env = Env("appwrite")
    bad = TransactionRequest(
        "payment:b", Kind.PAYMENT, (debit(CASH_MTN, 100), credit(RECEIVABLES, 90))
    )
    try:
        env.svc.post(bad)
    except UnbalancedTransaction:
        assert env.svc.get_by_key("payment:b") is None
        return
    raise AssertionError("an unbalanced transaction was accepted")


def check_bad_amounts_are_rejected() -> None:
    env = Env("appwrite")
    for amount in (1.5, True, 0, -5):
        try:
            env.svc.post(tx("payment:a", amount))
        except InvalidAmount:
            continue
        raise AssertionError(f"amount {amount!r} was accepted")


def check_same_key_different_content_conflicts() -> None:
    for kind in ("appwrite", "memory"):
        env = Env(kind)
        env.svc.post(tx("payment:p1", 100))
        try:
            env.svc.post(tx("payment:p1", 999))
        except IdempotencyConflict:
            assert env.svc.balance(CASH_MTN) == Money(100)
            continue
        raise AssertionError("same key with different content did not conflict")


def check_replay_does_not_double_count() -> None:
    env = Env("appwrite")
    env.svc.post(tx("payment:p1", 100))
    env.svc.post(tx("payment:p1", 100))
    assert env.svc.balance(CASH_MTN) == Money(100), "a replay double counted"


def check_posted_transaction_is_visible() -> None:
    env = Env("appwrite")
    posted = env.svc.post(tx("payment:p1", 100))
    assert env.svc.get_by_key("payment:p1") is not None, "sealed transaction not visible"
    assert env.svc.balance(CASH_MTN) == Money(100), "sealed transaction not in balances"
    assert posted.entry_count == 2


def check_unsealed_lines_are_invisible() -> None:
    env = Env("appwrite")
    env.faulty.arm(2, "after")  # both lines written, header never
    try:
        env.svc.post(tx("payment:p1", 100))
    except CrashInjected:
        pass
    env.faulty.arm(None)
    assert env.svc.balance(CASH_MTN) == Money(0), "unsealed lines counted in a balance"
    assert env.svc.trial_balance().rows == (), "unsealed lines counted in the trial balance"
    assert env.svc.get_by_key("payment:p1") is None


def check_entries_are_append_only() -> None:
    env = Env("appwrite")
    posted = env.svc.post(tx("payment:p1", 100))
    wrapper = AppendOnlyRepository(env.raw)
    entry_row_id = next(iter(env.raw._t["ledger_entry"]))
    for table, row_id in (("ledger_entry", entry_row_id), ("ledger_transaction", posted.id)):
        try:
            wrapper.update(table, row_id, {"amount": 1})
        except AppendOnlyViolation:
            pass
        else:
            raise AssertionError(f"update allowed on {table}")
        try:
            wrapper.delete(table, row_id)
        except AppendOnlyViolation:
            pass
        else:
            raise AssertionError(f"delete allowed on {table}")
    assert env.svc.balance(CASH_MTN) == Money(100)


ALL = [
    check_unbalanced_is_rejected,
    check_bad_amounts_are_rejected,
    check_same_key_different_content_conflicts,
    check_replay_does_not_double_count,
    check_posted_transaction_is_visible,
    check_unsealed_lines_are_invisible,
    check_entries_are_append_only,
]
