"""M06 (#41): crash protocol, proven by fault injection after EVERY write.

Protocol (docs/ops/audit.md): audit-first. The intent row is written, then the business write;
a failure that is seen is marked by a `failed` row; a crash is settled by the `audit-recover` job,
which compares the entity with the intent and writes a `not_applied` row when the change never
landed. Invariant checked here for every crash point: the business change never exists without a
live audit row, and a change that did not happen is never left looking like one that did.
"""

import json
from datetime import timedelta

import pytest

from audit_helpers import Crash, FaultyRepository, StepClock, admin_ctx, audit_rows, build
from hs_core.audit import AuditedRepository, AuditService, AuditUnavailable
from hs_core.audit.recover import RECOVER_JOB, make_recover_handler, recover_audit
from hs_core.jobs.model import Done, JobContext
from hs_core.jobs.schedule import JOBS


def meta(row):
    return json.loads(row["after"])["meta"]


def live_intents(repo, entity=None, entity_id=None):
    """Intent rows that no failed/not_applied row refers to: the changes the log says happened."""
    rows = audit_rows(repo)
    dead = {meta(r)["ref"] for r in rows if meta(r)["status"] in ("failed", "not_applied")}
    return [
        r
        for r in rows
        if meta(r)["status"] in ("intent", "recorded")
        and r["$id"] not in dead
        and (entity is None or r["entity"] == entity)
        and (entity_id is None or r["entity_id"] == entity_id)
    ]


def settle(repo, clock):
    clock.advance(minutes=30)
    svc = AuditService(repo, clock=clock)
    return recover_audit(repo, svc, now=clock(), grace=timedelta(minutes=5),
                         lookback=timedelta(hours=6))  # fmt: skip


def fresh(op):
    repo = FaultyRepository()
    _, service, audited, clock = build(repo)
    if op in ("update", "delete"):
        repo.create("tier", "t1", {"rate": 1, "name": "Gold"})
    return repo, service, audited, clock


def run(audited, op):
    ctx = admin_ctx(request_id="req-crash")
    if op == "create":
        return audited.create("tier", "t1", {"rate": 1, "name": "Gold"}, ctx=ctx)
    if op == "update":
        return audited.update("tier", "t1", {"rate": 2}, ctx=ctx)
    return audited.delete("tier", "t1", ctx=ctx)


def landed(repo, op):
    try:
        row = repo.get("tier", "t1")
    except Exception:  # noqa: BLE001
        return op == "delete"
    return {"create": True, "update": row["rate"] == 2, "delete": False}[op]


CASES = [(op, n, when) for op in ("create", "update", "delete") for n in (1, 2, 3, 4)
         for when in ("before", "after")]  # fmt: skip


class TestCrashAfterEveryWrite:
    @pytest.mark.parametrize(("op", "n", "when"), CASES)
    def test_process_death_at_each_write(self, op, n, when):
        repo, _, audited, clock = fresh(op)
        repo.arm(n, when)
        try:
            run(audited, op)
            crashed = False
        except Crash:
            crashed = True
        repo.disarm()
        settle(repo, clock)
        happened = landed(repo, op)
        live = live_intents(repo, "tier", "t1")
        if happened:
            assert len(live) == 1, "a business change landed without a live audit row"
        else:
            assert live == [], "a change that did not happen is still claimed by the log"
        if not crashed:
            assert happened and len(live) == 1  # n beyond the write count: plain success
        # recovery is idempotent
        before = len(audit_rows(repo))
        settle(repo, clock)
        assert len(audit_rows(repo)) == before

    @pytest.mark.parametrize(("op", "n", "when"), CASES)
    def test_client_retry_after_the_crash_converges_to_one_live_row(self, op, n, when):
        repo, _, audited, clock = fresh(op)
        repo.arm(n, when)
        try:
            run(audited, op)
        except Crash:
            pass
        repo.disarm()
        # restart: new objects, same request id (the client retries)
        _, _, audited2, _ = build(repo, clock)
        run(audited2, op)
        settle(repo, clock)
        assert landed(repo, op)
        assert len(live_intents(repo, "tier", "t1")) == 1

    @pytest.mark.parametrize(("op", "n", "when"), CASES)
    def test_recovery_first_then_retry_also_converges(self, op, n, when):
        repo, _, audited, clock = fresh(op)
        repo.arm(n, when)
        try:
            run(audited, op)
        except Crash:
            pass
        repo.disarm()
        settle(repo, clock)
        _, _, audited2, _ = build(repo, clock)
        run(audited2, op)
        settle(repo, clock)
        assert landed(repo, op)
        assert len(live_intents(repo, "tier", "t1")) == 1

    @pytest.mark.parametrize(("op", "n"), [(o, n) for o in ("create", "update", "delete")
                                          for n in (1, 2, 3)])  # fmt: skip
    def test_ordinary_error_at_each_write_never_leaves_a_false_success(self, op, n):
        repo, _, audited, clock = fresh(op)
        repo.arm(n, "before", error=RuntimeError("blip"))
        try:
            run(audited, op)
        except (RuntimeError, AuditUnavailable):  # write 1 = the audit row: fail closed
            pass
        repo.disarm()
        settle(repo, clock)
        live = live_intents(repo, "tier", "t1")
        assert len(live) == (1 if landed(repo, op) else 0)

    def test_every_write_of_the_protocol_was_exercised(self):
        """Guards the matrix: an audited update is exactly intent + business write (2 writes)."""
        repo, _, audited, _ = fresh("update")
        repo.arm(0)
        run(audited, "update")
        assert len(repo.writes) == 2
        repo, _, audited, _ = fresh("create")
        repo.arm(0)
        run(audited, "create")
        assert len(repo.writes) == 2


class TestRecoveryJob:
    def test_in_flight_changes_younger_than_the_grace_are_left_alone(self):
        repo, _, audited, clock = fresh("update")
        repo.arm(1, "after")  # intent written, business write never happens
        try:
            run(audited, "update")
        except Crash:
            pass
        repo.disarm()
        svc = AuditService(repo, clock=clock)
        res = recover_audit(repo, svc, now=clock(), grace=timedelta(minutes=5),
                            lookback=timedelta(hours=1))  # fmt: skip
        assert res.skipped_young == 1 and res.not_applied == 0
        assert len(audit_rows(repo)) == 1

    def test_not_applied_row_references_the_intent_and_keeps_entity_and_action(self):
        repo, _, audited, clock = fresh("update")
        repo.arm(1, "after")
        try:
            run(audited, "update")
        except Crash:
            pass
        repo.disarm()
        res = settle(repo, clock)
        rows = audit_rows(repo)
        assert res.not_applied == 1 and len(rows) == 2
        assert meta(rows[1])["status"] == "not_applied" and meta(rows[1])["ref"] == rows[0]["$id"]
        assert rows[1]["action"] == rows[0]["action"] and rows[1]["entity_id"] == "t1"
        assert rows[1]["actor_id"] is None and rows[1]["actor_roles"] == "system"

    def test_applied_change_is_counted_and_gets_no_extra_row(self):
        repo, _, audited, clock = fresh("update")
        repo.arm(2, "after")  # business write landed, caller never learned
        try:
            run(audited, "update")
        except Crash:
            pass
        repo.disarm()
        res = settle(repo, clock)
        assert res.applied == 1 and len(audit_rows(repo)) == 1

    def test_changes_older_than_the_lookback_are_not_examined(self):
        repo, _, audited, clock = fresh("update")
        repo.arm(1, "after")
        try:
            run(audited, "update")
        except Crash:
            pass
        repo.disarm()
        clock.advance(hours=10)
        svc = AuditService(repo, clock=clock)
        res = recover_audit(repo, svc, now=clock(), grace=timedelta(minutes=5),
                            lookback=timedelta(hours=6))  # fmt: skip
        assert res.examined == 0

    def test_state_that_matches_neither_side_is_reported_unverifiable_not_guessed(self):
        repo, _, audited, clock = fresh("update")
        repo.arm(1, "after")
        try:
            run(audited, "update")
        except Crash:
            pass
        repo.disarm()
        repo.update("tier", "t1", {"rate": 99})  # somebody else changed it meanwhile
        res = settle(repo, clock)
        assert res.unverifiable == 1 and len(audit_rows(repo)) == 1

    def test_non_table_actions_cannot_be_verified_and_are_reported(self):
        repo = FaultyRepository()
        _, service, _, clock = build(repo)
        repo.arm(1, "after")
        with pytest.raises(Crash):
            service.run_change(
                admin_ctx(), action="user.label.add", entity="user", entity_id="u1", op="action",
                before={"l": []}, after={"l": ["x"]}, reason=None, apply=lambda: None,
            )  # fmt: skip
        repo.disarm()
        res = settle(repo, clock)
        assert res.unverifiable == 1

    def test_pages_through_more_rows_than_one_page(self):
        repo = FaultyRepository()
        _, service, audited, clock = build(repo)
        for i in range(7):
            repo.create("tier", f"t{i}", {"rate": 0})
        for i in range(7):
            audited.update("tier", f"t{i}", {"rate": 1}, ctx=admin_ctx(request_id=f"p{i}"))
        clock.advance(minutes=30)
        res = recover_audit(repo, service, now=clock(), grace=timedelta(minutes=5),
                            lookback=timedelta(hours=6), page=3)  # fmt: skip
        assert res.examined == 7 and res.applied == 7

    def test_handler_runs_as_a_system_job_and_is_registered_in_the_schedule(self):
        spec = next(j for j in JOBS if j.name == RECOVER_JOB == "audit-recover")
        assert spec.schedule is not None and spec.schedule.every <= timedelta(minutes=30)
        repo, service, audited, clock = fresh("update")
        repo.arm(1, "after")
        try:
            run(audited, "update")
        except Crash:
            pass
        repo.disarm()
        clock.advance(minutes=30)
        handler = make_recover_handler(repo, service, clock=clock)
        out = handler(JobContext("audit-recover", "r1", "audit-recover:x", None, {}, None, 0, None))
        assert isinstance(out, Done) and out.items == 1
        row = audit_rows(repo)[-1]
        assert meta(row)["status"] == "not_applied" and meta(row)["job"] == "audit-recover"
        assert meta(row)["source"] == "system"

    def test_manual_run_can_widen_the_lookback_through_the_payload(self):
        repo, service, audited, clock = fresh("update")
        repo.arm(1, "after")
        try:
            run(audited, "update")
        except Crash:
            pass
        repo.disarm()
        clock.advance(hours=20)
        handler = make_recover_handler(repo, service, clock=clock, lookback=timedelta(hours=6))
        ctx0 = JobContext("audit-recover", "r1", "k", None, {}, None, 0, None)
        assert handler(ctx0).items == 0
        ctx1 = JobContext("audit-recover", "r2", "k2", None, {"lookback_hours": 48}, None, 0, None)
        assert handler(ctx1).items == 1


def test_recovery_with_audited_repository_type_is_not_required():
    """Recovery reads the entity with the raw repository handed to it; it never writes it."""
    repo, service, audited, clock = fresh("update")
    assert isinstance(audited, AuditedRepository)
    settle(repo, clock)
    assert repo.get("tier", "t1")["rate"] == 1
    assert StepClock  # keep the import used
