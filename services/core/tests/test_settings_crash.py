"""H05 (#134): fault injection after EVERY write of an edit (audit intent, history row, audit
outcome, value-row intent, value row, outcome). Invariants after the crash and recovery
(`audit-recover` + `settings-activate`): the value row never exists without its history row, history
and value row agree, every history row has a live audit intent with the reason, a retry is safe,
and a reader never sees anything but the old or the new value."""

import json
from datetime import timedelta

import pytest

from audit_helpers import Crash, FaultyRepository, StepClock
from hs_core.app_settings.model import RevisionConflict
from hs_core.audit import AuditContext, AuditService
from hs_core.audit.recover import recover_audit
from settings_helpers import (
    AUTO,
    LATE,
    NEXT_MIDNIGHT,
    REASON,
    T0,
    ManualClock,
    ctx,
    edit,
    history,
    stack,
    value_row,
)  # fmt: skip


def meta(row):
    return json.loads(row["after"])["meta"]


def settle(raw, clock):
    """What production does after a crash: audit-recover, then settings-activate."""
    audit_clock = StepClock(T0 + timedelta(minutes=30))
    recover_audit(
        raw, AuditService(raw, clock=audit_clock), now=audit_clock(),
        grace=timedelta(minutes=5), lookback=timedelta(hours=6),
    )  # fmt: skip
    _r, fresh, _a, _c = stack(repo=raw, clock=clock)
    system = AuditContext.system("settings-activate", run_key="run-after-crash")
    fresh.reconcile(ctx=system)
    return fresh


def live_intents(raw, entity):
    rows = raw.list("audit_log")
    dead = {meta(r)["ref"] for r in rows if meta(r)["status"] in ("failed", "not_applied")}
    return [
        r for r in rows
        if meta(r)["status"] == "intent" and r["entity"] == entity and r["$id"] not in dead
    ]  # fmt: skip


def check_invariants(raw, svc, key):
    h = history(raw, key)
    row = value_row(raw, key)
    if row is not None:
        assert h, "value row without history"
        assert row["revision"] == h[-1]["revision"], "projection behind history after settle"
    if h:
        assert row is not None, "history without a projection after settle"
    # every history row has a live audit intent with the reason (audit-first)
    intents = live_intents(raw, "app_setting_change")
    assert {r["entity_id"] for r in intents} == {r["$id"] for r in h}
    for r in intents:
        assert r["reason"] == REASON and r["actor_id"] == "u_super"


def prepare(op):
    raw = FaultyRepository()
    raw_, svc, audited, clock = stack(raw)
    if op == "rollback":
        edit(svc, AUTO, 24, 0)
        edit(svc, AUTO, 36, 1)
    if op == "cancel":
        edit(svc, LATE, 12, 0)
    return raw, svc, clock


def operation(op, svc):
    if op == "set":
        return svc.change(AUTO, 24, reason=REASON, expected_revision=0, ctx=ctx())
    if op == "scheduled":
        return svc.change(LATE, 12, reason=REASON, expected_revision=0, ctx=ctx())
    if op == "rollback":
        return svc.rollback(AUTO, reason=REASON, expected_revision=2, ctx=ctx())
    if op == "cancel":
        return svc.cancel_pending(LATE, reason=REASON, expected_revision=1, ctx=ctx())
    raise AssertionError(op)


def key_of(op):
    return AUTO if op in ("set", "rollback") else LATE


def old_new(op):
    return {"set": (48, 24), "scheduled": (24, 24), "rollback": (36, 24), "cancel": (24, 24)}[op]


def count_writes(op):
    raw, svc, _clock = prepare(op)
    raw.writes.clear()
    operation(op, svc)
    return len(raw.writes)


OPS = ["set", "scheduled", "rollback", "cancel"]


@pytest.mark.parametrize("op", OPS)
@pytest.mark.parametrize("when", ["before", "after"])
def test_crash_at_every_write_of_an_edit_leaves_a_consistent_recoverable_state(op, when):
    total = count_writes(op)
    assert total >= 4  # intent, history row, intent, value row
    for n in range(1, total + 1):
        raw, svc, clock = prepare(op)
        before = len(history(raw, key_of(op)))
        raw.arm(n, when, Crash())
        with pytest.raises(Crash):
            operation(op, svc)
        raw.disarm()
        fresh = settle(raw, clock)
        check_invariants(raw, fresh, key_of(op))
        committed = len(history(raw, key_of(op))) == before + 1
        old, new = old_new(op)
        # a reader sees the old value or the new one, never anything else
        assert fresh.get(key_of(op)) in (old, new), (n, when)
        if op in ("set", "rollback"):
            assert fresh.get(key_of(op)) == (new if committed else old), (n, when)
        # a retry is safe: it either conflicts (the change landed) or applies it once
        rev = before
        try:
            operation_retry(op, fresh, rev)
        except RevisionConflict:
            assert committed, (n, when)
        else:
            assert not committed, (n, when)
        check_invariants(raw, settle(raw, clock), key_of(op))
        assert len(history(raw, key_of(op))) == before + 1


def operation_retry(op, svc, rev):
    c = ctx()
    if op == "set":
        return svc.change(AUTO, 24, reason=REASON, expected_revision=rev, ctx=c)
    if op == "scheduled":
        return svc.change(LATE, 12, reason=REASON, expected_revision=rev, ctx=c)
    if op == "rollback":
        return svc.rollback(AUTO, reason=REASON, expected_revision=rev, ctx=c)
    return svc.cancel_pending(LATE, reason=REASON, expected_revision=rev, ctx=c)


@pytest.mark.parametrize("when", ["before", "after"])
def test_crash_at_every_write_of_an_activation(when):
    raw = FaultyRepository()
    _r, svc, _a, clock = stack(raw)
    edit(svc, LATE, 12, 0)
    clock.set(NEXT_MIDNIGHT + timedelta(minutes=1))
    system = AuditContext.system("settings-activate", run_key="k1")
    raw.writes.clear()
    svc.reconcile(ctx=system)
    total = len(raw.writes)
    assert total >= 2
    for n in range(1, total + 1):
        raw = FaultyRepository()
        _r, svc, _a, clock = stack(raw)
        edit(svc, LATE, 12, 0)
        clock.set(NEXT_MIDNIGHT + timedelta(minutes=1))
        raw.arm(n, when, Crash())
        with pytest.raises(Crash):
            svc.reconcile(ctx=AuditContext.system("settings-activate", run_key=f"k{n}"))
        raw.disarm()
        # readers switch at the effective time whatever the projection state
        _r2, reader, _a2, _c2 = stack(repo=raw, clock=clock)
        assert reader.get(LATE) == 12
        fresh = settle(raw, clock)
        assert fresh.get(LATE) == 12
        row = value_row(raw, LATE)
        assert row["value"] == "12" and row["pending_effective_at"] is None


def test_an_unavailable_store_in_the_middle_of_an_edit_is_not_silently_swallowed():
    raw = FaultyRepository()
    _r, svc, _a, clock = stack(raw)
    from hs_core.repository import RepositoryUnavailable

    for n in range(1, 5):
        raw2 = FaultyRepository()
        _r, svc, _a, clock = stack(raw2)
        raw2.plan = {n: RepositoryUnavailable("down")}
        raw2.writes.clear()
        try:
            edit(svc, AUTO, 24, 0)
            raised = False
        except Exception:  # noqa: BLE001
            raised = True
        raw2.disarm()
        fresh = settle(raw2, clock)
        h = history(raw2, AUTO)
        # either the caller was told (error) or the change fully landed
        assert raised or h
        check_invariants(raw2, fresh, AUTO)
        assert isinstance(ManualClock(), ManualClock)
