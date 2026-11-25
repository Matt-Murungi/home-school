"""B03 round 4: billing through the M06 AuditedRepository (one audit row per logical change)."""

from __future__ import annotations

import json
from datetime import timedelta

import pytest

from billing_helpers import T0, activate, make_rig, new_sub
from hs_core.audit import AuditedRepository, AuditService
from hs_core.audit.errors import AuditContextMissing, AuditUnavailable, ReasonRequired
from hs_core.billing import interfaces
from hs_core.billing.periods import period
from hs_core.billing.states import Status
from hs_core.billing.store import CLAIM, BillingStore
from hs_core.repository import InMemoryRepository

DAY = timedelta(days=1)
NXT = period(__import__("datetime").date(2027, 1, 15), 1).start_at


class AuditDownRepository(InMemoryRepository):
    """The audit store can be switched off (writes to audit_log raise)."""

    def __init__(self) -> None:
        super().__init__()
        self.audit_down = False

    def create(self, table, row_id, data, permissions=None):
        if table == "audit_log" and self.audit_down:
            raise RuntimeError("audit store down")
        return super().create(table, row_id, data, permissions)


def meta(row):
    return json.loads(row["after"])["meta"]


def rows_of(rig, entity, action=None):
    return [
        r
        for r in rig.audit_rows()
        if r["entity"] == entity and (action is None or r["action"] == action)
    ]


# ------------------------------------------------------------------ wiring and bypass
def test_billing_refuses_to_run_on_a_raw_repository():
    with pytest.raises(TypeError):
        BillingStore(InMemoryRepository())


def test_billing_has_no_audit_sink_of_its_own_any_more():
    assert not hasattr(interfaces, "AuditSink") and not hasattr(interfaces, "AuditEntry")
    from hs_core.billing import fakes

    assert not hasattr(fakes, "FakeAuditSink")


def test_a_user_operation_without_an_actor_fails_closed_and_writes_nothing():
    rig = make_rig()
    sid, _ = activate(rig)
    before = (len(rig.audit_rows()), rig.svc.get(sid).version, len(rig.repo.list(CLAIM)))
    for call in (
        lambda: rig.svc.pause(sid, now=T0 + DAY),
        lambda: rig.svc.cancel(sid, now=T0 + DAY),
        lambda: rig.svc.change_plan(sid, "plan3", now=T0 + DAY),
        lambda: rig.svc.create_subscription(parent_id="p", child_id="z", plan_id="plan2", now=T0),
    ):
        with pytest.raises(AuditContextMissing):
            call()
    assert (len(rig.audit_rows()), rig.svc.get(sid).version, len(rig.repo.list(CLAIM))) == before


# ------------------------------------------------------------------ actor attribution
def test_transitions_are_attributed_to_the_verified_actor_and_system_events_to_system():
    rig = make_rig()
    res = new_sub(rig)
    sid = res.subscription.id
    rig.svc.on_payment_confirmed(res.invoice.id, "pay-1", now=T0)
    rig.svc.on_teacher_assigned(sid, assignment_id="a1", is_trial=False, now=T0)
    rig.svc.pause(sid, now=T0 + DAY, ctx=rig.parent)
    rig.svc.resume(sid, now=T0 + 2 * DAY, ctx=rig.admin)
    sub_rows = {r["action"]: r for r in rows_of(rig, "subscription")}
    assert sub_rows["subscription.create"]["actor_id"] == "u_parent"
    assert sub_rows["subscription.pause"]["actor_id"] == "u_parent"
    assert sub_rows["subscription.pause"]["actor_roles"] == "parent"
    assert sub_rows["subscription.resume"]["actor_id"] == "u_admin"
    pay = sub_rows["subscription.payment_confirmed"]
    assert pay["actor_id"] is None and pay["actor_roles"] == "system"
    assert meta(pay)["job"] == "billing-payment:mtn_momo"
    assert meta(sub_rows["subscription.teacher_assigned"])["job"] == "billing-assignment"
    paid = rows_of(rig, "invoice", "invoice.pay")[0]
    assert paid["actor_id"] is None and meta(paid)["job"] == "billing-payment:mtn_momo"
    assert rows_of(rig, "invoice", "invoice.create")[0]["actor_id"] is None


def test_the_roll_job_cancel_at_period_end_is_a_system_change():
    rig = make_rig()
    sid, _ = activate(rig)
    rig.svc.cancel(sid, now=T0 + DAY, ctx=rig.parent, reason="moving away")
    rig.svc.roll_subscription(sid, now=rig.svc.get(sid).period_end_at)
    actions = [(r["action"], r["actor_id"]) for r in rows_of(rig, "subscription")][-3:]
    assert ("subscription.cancel_at_period_end", "u_parent") in actions
    last = rows_of(rig, "subscription")[-1]
    assert last["action"] == "subscription.cancel" and last["actor_id"] is None
    assert meta(last)["job"] == "billing-period-roll"


def test_the_ip_and_request_id_of_the_caller_are_recorded_not_the_payload():
    rig = make_rig()
    sid, _ = activate(rig)
    rig.svc.pause(sid, now=T0 + DAY, ctx=rig.parent)
    r = rows_of(rig, "subscription", "subscription.pause")[0]
    assert r["ip"] == "198.51.100.4" and r["request_id"] == "req-parent"
    text = r["before"] + r["after"]
    assert "198.51" not in text and "0771" not in text


# ------------------------------------------------------------------ reasons
def test_an_admin_cancel_needs_a_reason_and_writes_nothing_without_one():
    rig = make_rig()
    sid, _ = activate(rig)
    before = (len(rig.audit_rows()), len(rig.repo.list(CLAIM)), rig.svc.get(sid).version)
    for bad in (None, "", "  ", "ab"):
        with pytest.raises(ReasonRequired):
            rig.svc.cancel(sid, now=T0, ctx=rig.admin, immediate=True, reason=bad)
    assert (len(rig.audit_rows()), len(rig.repo.list(CLAIM)), rig.svc.get(sid).version) == before
    rig.svc.cancel(sid, now=T0, ctx=rig.admin, immediate=True, reason="duplicate account")
    r = rows_of(rig, "subscription", "subscription.cancel_admin")[0]
    assert r["reason"] == "duplicate account" and r["actor_id"] == "u_admin"


def test_a_parent_cancel_and_the_jobs_need_no_reason():
    rig = make_rig()
    sid, _ = activate(rig)
    rig.svc.cancel(sid, now=T0, ctx=rig.parent)  # end of period, no reason
    rig.svc.cancel(sid, now=T0, ctx=rig.parent, immediate=True)
    assert rows_of(rig, "subscription", "subscription.cancel")
    assert not rows_of(rig, "subscription", "subscription.cancel_admin")


def test_the_registry_requires_a_reason_for_the_admin_cancel_only():
    from hs_core.audit.registry import REASON_REQUIRED

    assert "subscription.cancel_admin" in REASON_REQUIRED
    assert not {"subscription.cancel", "subscription.pause", "invoice.void"} & REASON_REQUIRED


# ------------------------------------------------------------------ audit failure fails closed
def _down_rig():
    raw = AuditDownRepository()
    rig = make_rig(repo=raw)
    return rig, raw


def test_an_audit_outage_means_no_transition_and_no_dangling_claim():
    rig, raw = _down_rig()
    sid, _ = activate(rig)
    v = rig.svc.get(sid).version
    raw.audit_down = True
    with pytest.raises(AuditUnavailable):
        rig.svc.pause(sid, now=T0 + DAY, ctx=rig.parent)
    s = rig.svc.get(sid)
    assert s.status == Status.active and s.version == v
    assert len(rig.repo.list(CLAIM)) == v  # the withdrawn claim is gone: nothing blocks a retry
    raw.audit_down = False
    assert rig.svc.pause(sid, now=T0 + 2 * DAY, ctx=rig.parent).subscription.status == Status.paused


def test_an_audit_outage_never_loses_a_confirmed_payment_the_retry_applies_it_once():
    rig, raw = _down_rig()
    res = new_sub(rig)
    raw.audit_down = True
    with pytest.raises(AuditUnavailable):
        rig.svc.on_payment_confirmed(res.invoice.id, "momo-1", now=T0)  # the callback gets an error
    inv = rig.svc.get_invoice(res.invoice.id)
    assert (
        inv.status == "open" and rig.svc.get(res.subscription.id).status == Status.pending_payment
    )
    assert rig.ledger.payments == {}
    raw.audit_down = False
    # the provider retries the callback (or the poll job re-asks): applied, exactly once
    later = T0 + timedelta(minutes=10)
    assert rig.svc.on_payment_confirmed(res.invoice.id, "momo-1", now=later).status == "applied"
    assert rig.svc.on_payment_confirmed(res.invoice.id, "momo-1", now=later).status == "duplicate"
    assert len(rows_of(rig, "invoice", "invoice.pay")) == 1
    assert len(rows_of(rig, "subscription", "subscription.payment_confirmed")) == 1
    assert rig.svc.get(res.subscription.id).status == Status.matching


def test_a_creation_during_an_audit_outage_can_be_retried_without_a_second_subscription():
    rig, raw = _down_rig()
    raw.audit_down = True
    with pytest.raises(AuditUnavailable):
        new_sub(rig)
    raw.audit_down = False
    a = new_sub(rig, now=T0 + timedelta(seconds=5))
    b = new_sub(rig, now=T0 + timedelta(seconds=9))
    assert a.subscription.id == b.subscription.id and len(rig.repo.list("subscription")) == 1
    assert len(rows_of(rig, "subscription", "subscription.create")) == 1


# ------------------------------------------------------------------ one row per logical change
def test_a_transition_is_exactly_one_audit_row_and_a_retry_adds_none():
    rig = make_rig()
    sid, _ = activate(rig)
    for _ in range(3):
        rig.svc.cancel(sid, now=T0 + DAY, ctx=rig.parent, immediate=True)
    claims = [c for c in rig.repo.list(CLAIM) if c["subscription_id"] == sid]
    subs = rows_of(rig, "subscription")
    assert len(subs) == len(claims) + 1  # + the creation
    assert len({meta(r)["key"] for r in subs}) == len(subs)
    assert not any(meta(r).get("key_collision") for r in rig.audit_rows())


def test_every_invoice_change_is_audited_once_under_the_system_actor():
    rig = make_rig()
    sid, _ = activate(rig)
    inv = rig.svc.generate_invoice(sid, 1, now=NXT - 3 * DAY)
    rig.svc.pause(sid, now=NXT - 2 * DAY, ctx=rig.parent)
    voids = rows_of(rig, "invoice", "invoice.void")
    assert [r["entity_id"] for r in voids] == [inv.id]
    rig.svc.redispatch_undelivered(now=NXT, older_than_s=0)
    rig.svc.pause(sid, now=NXT, ctx=rig.parent)  # retry
    assert len(rows_of(rig, "invoice", "invoice.void")) == 1
    assert all(r["actor_id"] is None for r in rows_of(rig, "invoice"))


def test_the_refund_flag_is_audited_once_and_the_sweeps_use_the_audited_repository():
    rig = make_rig()
    res = new_sub(rig)
    rig.svc.on_payment_confirmed(res.invoice.id, "pay-1", now=T0)
    rig.ledger.fail = True
    rig.svc.cancel(res.subscription.id, now=T0 + DAY, ctx=rig.parent)
    rig.ledger.fail = False
    rig.svc.redispatch_undelivered(now=T0 + 2 * DAY, older_than_s=0)
    rig.svc.cancel(res.subscription.id, now=T0 + 3 * DAY, ctx=rig.parent)
    assert len(rows_of(rig, "invoice", "refund.flag")) == 1
    assert isinstance(rig.svc.store.audited_repo, AuditedRepository)
    assert isinstance(rig.audit, AuditService)


def test_no_billing_source_writes_audited_tables_through_a_raw_repository():
    """The M06 tripwire (tests/test_audit_reads.py) scans the whole tree; this one pins billing."""
    from pathlib import Path

    from hs_core.audit.tripwire import find_unaudited_writes

    root = Path(__file__).resolve().parents[1] / "src" / "hs_core"
    bad = [v for v in find_unaudited_writes(root) if "billing" in str(v)]
    assert bad == []
