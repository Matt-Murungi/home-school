"""Round 2 finding 1: concurrent workers on the SAME key with different clocks must agree on one
`posted_at` (line 1 is the single source) so as_of reads are all-or-nothing."""

from __future__ import annotations

import random
import threading
import time
from datetime import timedelta

import pytest

from hs_core.ledger.appwrite_store import AppwriteLedgerStore
from hs_core.ledger.chart import CASH_AIRTEL, CASH_MTN, RECEIVABLES, REVENUE
from hs_core.ledger.errors import IdempotencyConflict
from hs_core.ledger.model import Kind, TransactionRequest, credit, debit, parse_ts
from hs_core.ledger.service import LedgerService
from hs_core.ledger.testing import PagingRepository
from ledger_helpers import T0, Clock

CODES = [CASH_MTN, CASH_AIRTEL, REVENUE, RECEIVABLES]


def request(**kw):
    return TransactionRequest(
        "adjustment:race",
        Kind.ADJUSTMENT,
        (
            debit(CASH_MTN, 100),
            debit(CASH_AIRTEL, 50),
            credit(REVENUE, 90),
            credit(RECEIVABLES, 60),
        ),
        reference_type=kw.get("reference_type", "adjustment"),
        reference_id=kw.get("reference_id", "race"),
        description=kw.get("description"),
        created_by_id="admin",
    )


class Jitter:
    """Random sleeps around every repository call (widens the race window)."""

    def __init__(self, inner, rng):
        self.inner, self.rng = inner, rng

    def __getattr__(self, name):
        fn = getattr(self.inner, name)
        if not callable(fn):
            return fn

        def call(*a, **k):
            time.sleep(self.rng.random() * 0.0004)
            out = fn(*a, **k)
            time.sleep(self.rng.random() * 0.0004)
            return out

        return call


def two_services(raw, rng=None):
    out = []
    for offset in (0, 10):
        clock = Clock(T0 + timedelta(seconds=offset))
        repo = Jitter(raw, rng) if rng else raw
        svc = LedgerService(AppwriteLedgerStore(repo, clock=clock), clock=clock)
        svc.ensure_chart_of_accounts()
        out.append(svc)
    return out


def assert_all_or_nothing(svc, raw):
    stamps = {r["posted_at"] for r in raw._t["ledger_entry"].values()}
    stamps |= {r["posted_at"] for r in raw._t["ledger_transaction"].values()}
    assert len(stamps) == 1, stamps  # lines and header agree
    (stamp,) = stamps
    at = parse_ts(stamp)
    for as_of in (at - timedelta(milliseconds=1), at, at + timedelta(seconds=30)):
        seen = {c: svc.balance(c, as_of).amount for c in CODES}
        if as_of < at:
            assert set(seen.values()) == {0}, seen
        else:
            assert seen == {CASH_MTN: 100, CASH_AIRTEL: 50, REVENUE: 90, RECEIVABLES: -60}, seen
        assert svc.trial_balance(as_of).is_balanced
        n = sum(len(list(svc.statement(c, as_of))) for c in CODES)
        assert n == (0 if as_of < at else 4)


def test_gate_based_minimal_race_two_clocks():
    """Deterministic: worker B stalls after seeing no orphans while A writes line 1."""
    raw = PagingRepository()
    a, b = two_services(raw)
    gate_in, gate_go = threading.Event(), threading.Event()
    real = b.store._line_rows

    def paused(tid):
        rows = real(tid)  # B reads the (empty) orphan list first ...
        gate_in.set()
        gate_go.wait(5)  # ... then A runs to completion of line 1 (and more)
        return rows

    b.store._line_rows = paused
    t = threading.Thread(target=lambda: b.post(request()))
    t.start()
    gate_in.wait(5)
    b.store._line_rows = real
    a_done = a.post(request())
    gate_go.set()
    t.join(10)
    assert a_done.posted_at != ""
    assert_all_or_nothing(a, raw)


def test_300_random_runs_two_clocks_never_split_posted_at():
    bad = 0
    for i in range(300):
        rng = random.Random(i)
        raw = PagingRepository()
        a, b = two_services(raw, rng)
        threads = [threading.Thread(target=s.post, args=(request(),)) for s in (a, b)]
        [t.start() for t in threads]
        [t.join(30) for t in threads]
        try:
            assert_all_or_nothing(a, raw)
        except AssertionError:
            bad += 1
    assert bad == 0, f"{bad}/300 runs split posted_at"


def test_posted_at_of_a_crashed_first_line_is_adopted_by_a_slower_clock():
    raw = PagingRepository()
    a, b = two_services(raw)
    from hs_core.ledger.testing import CrashInjected, FaultyRepository

    faulty = FaultyRepository(raw)
    clock = Clock(T0)
    crashed = LedgerService(AppwriteLedgerStore(faulty, clock=clock), clock=clock)
    faulty.arm(1, "after")
    with pytest.raises(CrashInjected):
        crashed.post(request())
    b.post(request())
    assert_all_or_nothing(b, raw)


@pytest.mark.parametrize(
    "change",
    [{"reference_type": "other"}, {"reference_id": "r9"}],
)
def test_orphan_retry_with_different_line_references_conflicts(change):
    """Finding 3: line-carried fields of an orphan are the first writer's; a differing retry
    raises. (Header-only fields cannot be checked against orphans: documented.)"""
    from hs_core.ledger.testing import CrashInjected, FaultyRepository

    raw = PagingRepository()
    faulty = FaultyRepository(raw)
    clock = Clock(T0)
    svc = LedgerService(AppwriteLedgerStore(faulty, clock=clock), clock=clock)
    svc.ensure_chart_of_accounts()
    faulty.arm(2, "after")
    with pytest.raises(CrashInjected):
        svc.post(request())
    faulty.arm(None)
    with pytest.raises(IdempotencyConflict):
        svc.post(request(**change))
    assert svc.get_by_key("adjustment:race") is None
    svc.post(request())  # the original content still completes
    assert svc.get_by_key("adjustment:race") is not None
