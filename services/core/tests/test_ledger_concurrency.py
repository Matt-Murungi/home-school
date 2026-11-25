"""Concurrency: exactly-once under racing posters; no lost updates on a shared account."""

from __future__ import annotations

import threading

import pytest

from hs_core.ledger import flows
from hs_core.ledger.chart import CASH_MTN, RECEIVABLES, teacher_payable_code
from hs_core.ledger.errors import IdempotencyConflict
from hs_core.ledger.money import Money
from ledger_helpers import Env, tx

THREADS = 50


def run_threads(fn, n=THREADS):
    barrier = threading.Barrier(n)
    results: list = [None] * n

    def work(i):
        barrier.wait()
        try:
            results[i] = ("ok", fn(i))
        except Exception as exc:  # noqa: BLE001
            results[i] = ("err", exc)

    threads = [threading.Thread(target=work, args=(i,)) for i in range(n)]
    [t.start() for t in threads]
    [t.join(timeout=60) for t in threads]
    assert all(r is not None for r in results), "a thread hung"
    return results


def assert_as_of_consistent(env):
    """At every distinct timestamp seen, balances/statements/trial balance are all or nothing."""
    from hs_core.ledger.model import parse_ts

    stamps = set()
    for acct in env.store.list_accounts():
        stamps |= {e.posted_at for e in env.svc.statement(acct.code)}
    for stamp in sorted(stamps):
        at = parse_ts(stamp)
        assert env.svc.trial_balance(at).is_balanced
        total_debits = 0
        total_credits = 0
        for acct in env.store.list_accounts():
            entries = list(env.svc.statement(acct.code, at))
            total_debits += sum(e.amount for e in entries if e.side.value == "debit")
            total_credits += sum(e.amount for e in entries if e.side.value == "credit")
            assert env.svc.balance(acct.code, at).amount == (
                sum(e.amount for e in entries if e.side == acct.normal_side)
                - sum(e.amount for e in entries if e.side != acct.normal_side)
            )
        assert total_debits == total_credits, stamp


@pytest.fixture(params=["appwrite", "memory"])
def env(request):
    return Env(request.param)


def test_50_threads_posting_the_same_key_make_one_transaction(env):
    results = run_threads(lambda i: env.svc.post(tx("payment:same", 1_234)))
    assert all(kind == "ok" for kind, _ in results), [r for r in results if r[0] == "err"]
    posted = [r for _, r in results]
    assert len({p.id for p in posted}) == 1
    assert sum(1 for p in posted if not p.replayed) == 1  # exactly one creator
    assert env.svc.balance(CASH_MTN) == Money(1_234)
    assert env.svc.balance(RECEIVABLES) == Money(-1_234)
    assert len(list(env.svc.statement(CASH_MTN))) == 1
    if env.kind == "appwrite":
        assert len(env.raw._t["ledger_transaction"]) == 1 and len(env.raw._t["ledger_entry"]) == 2
    assert env.svc.trial_balance().is_balanced
    assert_as_of_consistent(env)


def test_50_threads_same_key_different_content_exactly_one_wins(env):
    results = run_threads(lambda i: env.svc.post(tx("payment:same", 1_000 + i)))
    oks = [r for k, r in results if k == "ok"]
    errs = [r for k, r in results if k == "err"]
    assert len({p.id for p in oks}) == 1
    assert all(isinstance(e, IdempotencyConflict) for e in errs), errs
    winner = oks[0]
    assert all(p.total_amount == winner.total_amount for p in oks)
    assert env.svc.balance(CASH_MTN) == Money(winner.total_amount)
    assert env.svc.trial_balance().is_balanced


def test_50_threads_different_transactions_on_one_teacher_payable_lose_no_update(env):
    env.svc.ensure_teacher_payable("t1")
    code = teacher_payable_code("t1")
    results = run_threads(lambda i: env.svc.post(flows.earning_accrued(f"e{i}", "t1", 100 + i)))
    assert all(k == "ok" for k, _ in results), [r for r in results if r[0] == "err"]
    assert env.svc.balance(code) == Money(sum(100 + i for i in range(THREADS)))
    assert env.svc.trial_balance().is_balanced
    assert len(list(env.svc.statement(code))) == THREADS
    assert_as_of_consistent(env)


def test_50_threads_creating_the_same_teacher_payable_make_one_account(env):
    results = run_threads(lambda i: env.svc.ensure_teacher_payable("teacher-9"))
    assert len({r.id for _, r in results}) == 1
    assert len([a for a in env.store.list_accounts() if a.teacher_id == "teacher-9"]) == 1


def test_readers_never_see_a_half_posted_transaction_while_writers_run():
    env = Env("appwrite")
    stop = threading.Event()
    bad: list[str] = []

    def reader():
        while not stop.is_set():
            tb = env.svc.trial_balance()
            if not tb.is_balanced:
                bad.append(str(tb.net))
            # a single account's balance is always a multiple of the posting size
            if env.svc.balance(CASH_MTN).amount % 100:
                bad.append("partial")

    r = threading.Thread(target=reader)
    r.start()
    run_threads(lambda i: env.svc.post(tx(f"payment:w{i}", 100)), n=30)
    stop.set()
    r.join(timeout=30)
    assert bad == []
    assert env.svc.balance(CASH_MTN) == Money(3_000)
