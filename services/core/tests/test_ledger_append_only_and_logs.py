"""The ledger writes only through AppendOnlyRepository (SEC-6, M04) and logs no PII."""

from __future__ import annotations

import logging
from pathlib import Path

import hs_core
from hs_core.append_only import APPEND_ONLY_TABLES, find_append_only_violations
from hs_core.ledger import flows
from hs_core.ledger.chart import CASH_MTN
from hs_core.ledger.testing import CrashInjected
from ledger_helpers import Env, tx


class Spy:
    """Records which repository methods the ledger store used, per table."""

    def __init__(self, inner):
        self.inner = inner
        self.calls: set[tuple[str, str]] = set()

    def __getattr__(self, name):
        target = getattr(self.inner, name)
        if not callable(target):
            return target

        def call(table, *a, **k):
            self.calls.add((name, table))
            return target(table, *a, **k)

        return call


def test_ledger_store_only_creates_reads_and_lists_ledger_rows():
    import hs_core.ledger.appwrite_store as aw

    env = Env("appwrite")
    spy = Spy(env.raw)
    store = aw.AppwriteLedgerStore(spy, clock=env.clock)
    from hs_core.ledger.service import LedgerService

    svc = LedgerService(store, clock=env.clock)
    svc.ensure_chart_of_accounts()
    p = svc.post(tx("payment:p1", 100))
    svc.reverse(p.id, reason="x")
    svc.post(flows.invoice_issued("i1", 5))
    svc.balance(CASH_MTN)
    svc.trial_balance()
    svc.recover()
    assert {m for m, _ in spy.calls} <= {"create", "get", "list"}, spy.calls
    assert {("create", t) for t in APPEND_ONLY_TABLES if t.startswith("ledger")} <= spy.calls


def test_no_ledger_module_calls_an_update_or_delete_on_append_only_tables():
    root = Path(hs_core.__file__).parent / "ledger"
    assert find_append_only_violations(root) == []


def test_ledger_modules_never_call_sdk_write_methods_other_than_create():
    import re

    from hs_core.append_only import WRITE_METHODS

    pattern = re.compile(r"\.(" + "|".join(sorted(WRITE_METHODS)) + r")\(")
    root = Path(hs_core.__file__).parent / "ledger"
    offenders = [
        f"{p.name}: {m.group(0)}"
        for p in root.glob("*.py")
        if p.name != "testing.py"
        for m in pattern.finditer(p.read_text())
    ]
    assert offenders == []


def test_logs_carry_ids_only(caplog):
    caplog.set_level(logging.DEBUG, logger="hs_core.ledger")
    env = Env("appwrite")
    env.faulty.arm(2, "after")
    try:
        env.svc.post(tx("payment:p1", 1_234, description="Jane Doe 0772123456"))
    except CrashInjected:
        pass
    env.faulty.arm(None)
    env.svc.post(tx("payment:p1", 1_234, description="Jane Doe 0772123456"))
    env.audit.fail_next = 1
    try:
        env.svc.post(tx("payment:p2", 50, description="John Smith"))
    except Exception:  # noqa: BLE001
        pass
    text = " ".join(f"{r.getMessage()} {r.__dict__}" for r in caplog.records)
    assert "ledger.post" in text
    for forbidden in ("Jane", "Doe", "0772", "John", "Smith", "1234", "1_234"):
        assert forbidden not in text, forbidden
