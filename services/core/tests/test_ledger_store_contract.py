"""The `LedgerStore` contract, run against every implementation (the Postgres adapter, when it
exists, must pass this file unchanged)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from hs_core.ledger.chart import CASH_MTN, fixed_accounts
from hs_core.ledger.errors import AccountConflict, IdempotencyConflict
from hs_core.ledger.model import Account, AccountType, Side, account_id_for
from ledger_helpers import Env, tx


@pytest.fixture(params=["appwrite", "memory"])
def env(request):
    return Env(request.param)


def prepared(env, key="payment:c1", amount=100, **kw):
    return env.svc._prepare(tx(key, amount, **kw))


def test_create_account_is_idempotent_and_conflicts_on_a_different_definition(env):
    acct = fixed_accounts("2026-01-01T00:00:00.000Z")[0]
    assert env.store.create_account(acct) == env.store.get_account(acct.code)
    other = Account(acct.code, "Renamed", acct.type, acct.normal_side)
    with pytest.raises(AccountConflict):
        env.store.create_account(other)
    other = Account(acct.code, acct.name, AccountType.LIABILITY, Side.CREDIT)
    with pytest.raises(AccountConflict):
        env.store.create_account(other)
    assert env.store.get_account("cash:nope") is None
    assert env.store.get_account(acct.code).id == account_id_for(acct.code)


def test_append_get_replay_and_conflict(env):
    p = prepared(env)
    first = env.store.append(p)
    assert first.replayed is False and env.store.get_transaction(p.id) == first
    assert env.store.get_by_key("payment:c1") == first
    assert env.store.get_by_key("payment:zzz") is None
    again = env.store.append(p)
    assert again.replayed is True and again.posted_at == first.posted_at
    with pytest.raises(IdempotencyConflict):
        env.store.append(prepared(env, amount=101))
    assert env.store.get_transaction(p.id) == first


def test_totals_and_entries_see_sealed_only_and_respect_as_of(env):
    a = env.store.append(prepared(env, "payment:c1", 100))
    env.clock.advance(hours=1)
    env.store.append(prepared(env, "payment:c2", 40))
    cash = account_id_for(CASH_MTN)
    assert env.store.account_totals()[cash].debits == 140
    cut = datetime.fromisoformat(a.posted_at)
    assert env.store.account_totals(as_of=cut)[cash].debits == 100
    assert env.store.account_totals(account_id=cash) == {cash: env.store.account_totals()[cash]}
    assert [e.amount for e in env.store.entries(cash)] == [100, 40]
    assert [e.amount for e in env.store.entries(cash, as_of=cut)] == [100]
    assert sum(t.debits for t in env.store.account_totals().values()) == sum(
        t.credits for t in env.store.account_totals().values()
    )
    assert datetime(2000, 1, 1, tzinfo=UTC)  # tz-aware inputs only
    with pytest.raises(Exception):  # noqa: B017 - naive datetimes are refused
        env.store.account_totals(as_of=datetime(2026, 1, 1))


def test_reversal_is_posted_once(env):
    orig = env.svc.post(tx("payment:c1", 100))
    rev = env.svc.reverse(orig.id, reason="x")
    assert env.store.get_by_key(f"reversal:{orig.id}") == rev
    assert env.store.get_transaction(orig.id) == orig  # the original is untouched
    assert env.svc.reverse(orig.id, reason="x").replayed is True


def test_docs_write_down_the_swap_boundary_and_key_convention():
    from pathlib import Path

    doc = (Path(__file__).parents[3] / "docs" / "ops" / "ledger.md").read_text()
    for needle in (
        "LedgerStore",
        "PostgresLedgerStore",
        "test_ledger_store_contract.py",
        "owner Q10",
        "<prefix>:<source id>",
        "reversal:<original transaction id>",
    ):
        assert needle in doc, needle


def test_docs_state_scope_cost_order_and_recovery():
    from pathlib import Path

    doc = (Path(__file__).parents[3] / "docs" / "ops" / "ledger.md").read_text()
    head = doc.split("## What it guarantees", 1)[1][:600]
    assert "opt-in" in head and "server API key can still update or delete" in head
    for needle in (
        "O(entries of the account)",
        "about 111 calls",
        "creation order, NOT `posted_at` order",
        "Partial reversal is NOT supported",
        "different reason raises `IdempotencyConflict`",
        "make_recover_handler",
        "line 1",
    ):
        assert needle in doc, needle
