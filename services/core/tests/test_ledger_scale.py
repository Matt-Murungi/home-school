"""Efficient reads within Appwrite's 5,000 cap: cursor pagination, never list-all, never offset."""

from __future__ import annotations

import time

from hs_core.ledger.chart import CASH_MTN, RECEIVABLES
from hs_core.ledger.model import account_id_for, entry_id_for, transaction_id_for
from hs_core.ledger.money import Money
from ledger_helpers import Env

N_TX = 10_000  # 20,000 entries


def seed(env, n_tx=N_TX, amount=10):
    """Bulk-load sealed transactions straight into the raw repository (fast), in protocol order."""
    cash, rec = account_id_for(CASH_MTN), account_id_for(RECEIVABLES)
    for i in range(n_tx):
        tid = transaction_id_for(f"payment:s{i}")
        ts = f"2026-11-25T09:00:{i % 60:02d}.000Z"
        for line, (acct, side) in enumerate(((cash, "debit"), (rec, "credit")), start=1):
            env.raw.create(
                "ledger_entry",
                entry_id_for(tid, line),
                {
                    "transaction_id": tid,
                    "line": line,
                    "account_id": acct,
                    "side": side,
                    "amount": amount,
                    "currency": "UGX",
                    "reference_type": "payment",
                    "reference_id": f"s{i}",
                    "posted_at": ts,
                },
            )
        env.raw.create(
            "ledger_transaction",
            tid,
            {
                "kind": "payment",
                "idempotency_key": f"payment:s{i}",
                "entry_count": 2,
                "total_amount": amount,
                "currency": "UGX",
                "posted_at": ts,
            },
        )
    # plus 100 orphan lines (no header) that must not count
    for i in range(100):
        tid = transaction_id_for(f"payment:orphan{i}")
        env.raw.create(
            "ledger_entry",
            entry_id_for(tid, 1),
            {
                "transaction_id": tid,
                "line": 1,
                "account_id": cash,
                "side": "debit",
                "amount": 7,
                "currency": "UGX",
                "posted_at": "2026-11-25T09:00:00.000Z",
            },
        )


def test_20000_entries_balance_trial_and_statement_page_by_cursor():
    env = Env("appwrite")
    seed(env)
    env.raw.list_calls.clear()
    started = time.perf_counter()
    assert env.svc.balance(CASH_MTN) == Money(N_TX * 10)
    assert env.svc.balance(RECEIVABLES) == Money(-N_TX * 10)
    tb = env.svc.trial_balance()
    assert tb.is_balanced and sum(r.debits for r in tb.rows) == N_TX * 10
    assert sum(1 for _ in env.svc.statement(CASH_MTN)) == N_TX
    elapsed = time.perf_counter() - started
    assert elapsed < 60  # generous; the point is it completes with paging, not how fast the fake is
    assert env.raw.max_page_seen <= 1000
    assert any(cursor for _, _, cursor in env.raw.list_calls), "must page with cursors"
    entry_pages = [n for t, n, _ in env.raw.list_calls if t == "ledger_entry"]
    assert max(entry_pages) <= 1000 and len(entry_pages) >= 20


def test_balance_as_of_uses_the_account_posted_at_index_and_matches():
    env = Env("appwrite")
    seed(env, n_tx=600)
    from datetime import UTC, datetime

    cut = datetime(2026, 11, 25, 9, 0, 29, 999000, tzinfo=UTC)  # seconds 0..29 of each minute
    expected = sum(10 for i in range(600) if i % 60 < 30)
    assert env.svc.balance(CASH_MTN, as_of=cut) == Money(expected)
    assert env.svc.trial_balance(as_of=cut).is_balanced
