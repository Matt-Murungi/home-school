"""Shared builders for the ledger tests."""

from __future__ import annotations

import itertools
from datetime import UTC, datetime, timedelta

from hs_core.ledger.appwrite_store import AppwriteLedgerStore
from hs_core.ledger.chart import CASH_MTN, RECEIVABLES, REVENUE
from hs_core.ledger.memory import InMemoryLedgerStore
from hs_core.ledger.model import Kind, TransactionRequest, credit, debit
from hs_core.ledger.service import LedgerService
from hs_core.ledger.testing import FakeAuditSink, FaultyRepository, PagingRepository

T0 = datetime(2026, 11, 25, 9, 0, 0, tzinfo=UTC)


class Clock:
    """Deterministic, strictly increasing UTC clock (1 ms per call)."""

    def __init__(self, start: datetime = T0) -> None:
        self.now = start
        self._n = itertools.count()

    def __call__(self) -> datetime:
        self.now = self.now + timedelta(milliseconds=1)
        return self.now

    def advance(self, **kw) -> None:
        self.now = self.now + timedelta(**kw)


class Env:
    """A service over the Appwrite-backed store (with fault injection) or the in-memory store."""

    def __init__(self, kind: str = "appwrite") -> None:
        self.kind = kind
        self.clock = Clock()
        self.audit = FakeAuditSink()
        self.raw = PagingRepository()
        self.faulty = FaultyRepository(self.raw)
        self.store = (
            AppwriteLedgerStore(self.faulty, clock=self.clock)
            if kind == "appwrite"
            else InMemoryLedgerStore()
        )
        self.svc = LedgerService(self.store, clock=self.clock, audit=self.audit)
        self.svc.ensure_chart_of_accounts()
        self.faulty.writes = 0

    def restart(self) -> LedgerService:
        """A fresh service/store over the same repository, as after a process restart."""
        if self.kind == "appwrite":
            self.store = AppwriteLedgerStore(self.faulty, clock=self.clock)
        self.svc = LedgerService(self.store, clock=self.clock, audit=self.audit)
        return self.svc


def tx(
    key: str = "payment:p1",
    amount=10_000,
    *,
    kind: Kind | str = Kind.PAYMENT,
    debit_code: str = CASH_MTN,
    credit_code: str = RECEIVABLES,
    **kw,
) -> TransactionRequest:
    return TransactionRequest(
        idempotency_key=key,
        kind=kind,
        lines=(debit(debit_code, amount), credit(credit_code, amount)),
        reference_type=kw.pop("reference_type", "payment"),
        reference_id=kw.pop("reference_id", "p1"),
        **kw,
    )


__all__ = ["Env", "Clock", "tx", "T0", "REVENUE"]
