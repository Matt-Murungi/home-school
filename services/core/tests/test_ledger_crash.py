"""Crash safety (NFR-8): fault injection after EVERY single write, before and after the write is
applied. Invariants after each crash and after recovery: no half-posted transaction is visible,
trial balance is zero, a retry converges to exactly one transaction, nothing double-counts."""

from __future__ import annotations

import pytest

from hs_core.ledger.chart import CASH_AIRTEL, CASH_MTN, PROVIDER_FEES, RECEIVABLES, REVENUE
from hs_core.ledger.errors import IdempotencyConflict
from hs_core.ledger.model import Kind, TransactionRequest, credit, debit
from hs_core.ledger.money import Money
from hs_core.ledger.testing import CrashInjected
from ledger_helpers import Env, tx


def four_lines(key="payment:big"):
    return TransactionRequest(
        key,
        Kind.PAYMENT,
        (
            debit(CASH_MTN, 9_700),
            debit(PROVIDER_FEES, 300),
            credit(RECEIVABLES, 9_000),
            credit(REVENUE, 1_000),
        ),
        reference_type="payment",
        reference_id="big",
    )


def raw_counts(env):
    return len(env.raw._t.get("ledger_transaction", {})), len(env.raw._t.get("ledger_entry", {}))


def assert_invariants(env, expect_visible: dict[str, int]):
    svc = env.svc
    assert svc.trial_balance().is_balanced
    for code, amount in expect_visible.items():
        assert svc.balance(code) == Money(amount), code


WRITES = {"two": (lambda: tx("payment:p1", 500), 3), "four": (lambda: four_lines(), 5)}


@pytest.mark.parametrize("mode", ["before", "after"])
@pytest.mark.parametrize("shape", ["two", "four"])
def test_crash_at_every_write_then_retry_converges(shape, mode):
    make, n_writes = WRITES[shape]
    for crash_at in range(1, n_writes + 1):
        env = Env("appwrite")
        env.svc.post(tx("payment:base", 1_000))  # an unrelated, healthy transaction
        env.faulty.arm(crash_at, mode)
        request = make()
        with pytest.raises(CrashInjected):
            env.svc.post(request)
        sealed_by_crashed_call = crash_at == n_writes and mode == "after"
        key = request.idempotency_key
        # 1. nothing half-posted is visible; the trial balance is zero; base is untouched
        assert (env.svc.get_by_key(key) is not None) == sealed_by_crashed_call, (crash_at, mode)
        assert env.svc.balance(CASH_MTN).amount == 1_000 + (
            (9_700 if shape == "four" else 500) if sealed_by_crashed_call else 0
        )
        assert env.svc.trial_balance().is_balanced
        # 2. a retry (fresh process) converges to exactly one transaction
        env.faulty.arm(None)
        svc = env.restart()
        posted = svc.post(request)
        assert posted.replayed is sealed_by_crashed_call
        assert svc.post(request).replayed is True
        assert len([1 for _ in svc.statement(CASH_MTN)]) == 2
        assert svc.balance(CASH_MTN).amount == 1_000 + (9_700 if shape == "four" else 500)
        assert svc.trial_balance().is_balanced
        headers, entries = raw_counts(env)
        assert headers == 2 and entries == 2 + len(request.lines)  # no duplicate rows
        assert f"ledger:{posted.id}" in env.audit.events


def test_crash_during_the_retry_too_still_converges():
    request = four_lines()
    for first in range(1, 6):
        for second in range(1, 6):
            env = Env("appwrite")
            env.faulty.arm(first, "after")
            with pytest.raises(CrashInjected):
                env.svc.post(request)
            env.faulty.arm(second, "after")
            try:
                env.svc.post(request)
            except CrashInjected:
                pass
            env.faulty.arm(None)
            posted = env.restart().post(request)
            assert posted.entry_count == 4
            assert env.svc.balance(CASH_MTN) == Money(9_700)
            assert env.svc.trial_balance().is_balanced
            assert raw_counts(env) == (1, 4)


def test_orphan_lines_are_invisible_to_every_read():
    env = Env("appwrite")
    env.faulty.arm(2, "after")  # line 1 and 2 of a 4-line transaction written, no header
    with pytest.raises(CrashInjected):
        env.svc.post(four_lines())
    env.faulty.arm(None)
    assert raw_counts(env) == (0, 2)
    assert env.svc.balance(CASH_MTN) == Money(0)
    assert env.svc.balance(PROVIDER_FEES) == Money(0)
    assert list(env.svc.statement(CASH_MTN)) == []
    assert env.svc.trial_balance().rows == ()
    assert env.svc.get_by_key("payment:big") is None


def test_retry_with_different_content_after_a_crash_is_a_conflict_and_never_mixes_lines():
    env = Env("appwrite")
    env.faulty.arm(2, "after")
    with pytest.raises(CrashInjected):
        env.svc.post(tx("payment:p1", 500))
    env.faulty.arm(None)
    with pytest.raises(IdempotencyConflict):
        env.svc.post(tx("payment:p1", 600))
    with pytest.raises(IdempotencyConflict):
        env.svc.post(tx("payment:p1", 500, debit_code=CASH_AIRTEL))
    assert env.svc.get_by_key("payment:p1") is None
    assert env.svc.post(tx("payment:p1", 500)).total_amount == 500  # the right content completes


def test_recovery_completes_stale_orphans_with_a_rebuilt_request_and_leaves_young_ones():
    env = Env("appwrite")
    env.faulty.arm(2, "after")
    with pytest.raises(CrashInjected):
        env.svc.post(four_lines("payment:old"))
    env.faulty.arm(None)
    env.clock.advance(hours=2)
    env.faulty.arm(2, "after")
    with pytest.raises(CrashInjected):
        env.svc.post(four_lines("payment:young"))
    env.faulty.arm(None)

    report = env.svc.recover(rebuild=lambda g: four_lines("payment:old"))
    assert len(report.completed) == 1 and report.abandoned == []  # young one not touched (lease)
    assert env.svc.get_by_key("payment:old") is not None
    assert env.svc.get_by_key("payment:young") is None
    assert env.svc.balance(CASH_MTN) == Money(9_700)
    # after the lease the young one is stale too; nobody can rebuild it: abandoned, still invisible
    env.clock.advance(hours=2)
    report = env.svc.recover(rebuild=lambda g: None)
    assert len(report.abandoned) == 1 and report.completed == []
    assert env.svc.balance(CASH_MTN) == Money(9_700)
    assert env.svc.trial_balance().is_balanced
    # an abandoned group still completes when its caller retries (exactly-once effect)
    env.svc.post(four_lines("payment:young"))
    assert env.svc.balance(CASH_MTN) == Money(19_400)
    assert env.svc.recover().abandoned == [] and env.svc.recover().completed == []


def test_recovery_ignores_a_rebuilt_request_for_another_transaction():
    env = Env("appwrite")
    env.faulty.arm(1, "after")
    with pytest.raises(CrashInjected):
        env.svc.post(tx("payment:p1", 500))
    env.faulty.arm(None)
    env.clock.advance(hours=2)
    report = env.svc.recover(rebuild=lambda g: tx("payment:other", 500))
    assert report.completed == [] and len(report.abandoned) == 1
    assert env.svc.get_by_key("payment:other") is None


def test_crash_of_a_reversal_at_every_write_converges_to_one_reversal():
    for crash_at in range(1, 4):
        env = Env("appwrite")
        orig = env.svc.post(tx("payment:p1", 700))
        env.faulty.arm(crash_at, "after")
        with pytest.raises(CrashInjected):
            env.svc.reverse(orig.id, reason="dup")
        env.faulty.arm(None)
        env.restart().reverse(orig.id, reason="dup")
        assert env.svc.balance(CASH_MTN) == Money(0)
        assert raw_counts(env) == (2, 4)


def test_orphan_age_uses_the_first_attempts_timestamp():
    env = Env("appwrite")
    env.faulty.arm(1, "after")
    with pytest.raises(CrashInjected):
        env.svc.post(tx("payment:p1", 500))
    env.faulty.arm(None)
    first = next(iter(env.raw._t["ledger_entry"].values()))["posted_at"]
    env.clock.advance(minutes=5)
    posted = env.restart().post(tx("payment:p1", 500))
    assert posted.posted_at == first  # lines and header agree on posted_at
    assert {e.posted_at for e in posted.lines} == {first}


def test_recover_job_handler_completes_reports_abandoned_and_is_idempotent(caplog):
    import logging

    from hs_core.jobs.model import JobContext
    from hs_core.ledger.jobs import make_recover_handler

    env = Env("appwrite")
    for key in ("payment:a", "payment:b"):
        env.faulty.arm(1, "after")
        with pytest.raises(CrashInjected):
            env.svc.post(tx(key, 500))
    env.faulty.arm(None)
    env.clock.advance(hours=2)
    handler = make_recover_handler(
        lambda: env.svc, lambda g: tx("payment:a", 500) if True else None
    )
    ctx = JobContext("ledger-recover", "r1", "ledger-recover:1", None, {}, None, 0)
    caplog.set_level(logging.WARNING, logger="hs_core.ledger.recover")
    first = handler(ctx)
    assert first.items == 2 and env.svc.get_by_key("payment:a") is not None
    assert any("abandoned" in r.getMessage() for r in caplog.records)
    assert handler(ctx).items == 1  # b is still abandoned; a does not repeat
    assert env.svc.balance(CASH_MTN) == Money(500)
