"""M06 (#41): AuditService and the AuditedRepository choke point (happy paths and edge cases).

Every test builds its own in-memory repository: offline, no Appwrite.
"""

import json
import threading

import pytest

from audit_helpers import FaultyRepository, StepClock, admin_ctx, audit_rows, build, user_ctx
from hs_core.append_only import APPEND_ONLY_TABLES, AppendOnlyRepository, AppendOnlyViolation
from hs_core.audit import (
    AuditContext,
    AuditContextMissing,
    AuditedRepository,
    AuditService,
    AuditUnavailable,
    ReasonRequired,
    UnauditedRead,
)
from hs_core.audit.registry import AUDITED_TABLES, REASON_REQUIRED
from hs_core.repository import Conflict, InMemoryRepository, NotFound


def meta(row):
    return json.loads(row["after"])["meta"]


def seed(repo, table, row_id, data):
    repo.create(table, row_id, data)


class TestAcceptanceOneRowPerChange:
    """#41 'How we'll know': a setting change, a dispute resolution and an opened case each
    produce exactly one audit row."""

    def test_changing_a_setting_writes_exactly_one_audit_row(self):
        repo, _, audited, _ = build()
        seed(repo, "app_setting", "late_fee_ugx", {"value": "5000"})
        audited.update(
            "app_setting", "late_fee_ugx", {"value": "7000"},
            ctx=admin_ctx(), action="setting.update", reason="Board decision 12",
        )  # fmt: skip
        rows = audit_rows(repo)
        assert len(rows) == 1
        r = rows[0]
        assert (r["action"], r["entity"], r["entity_id"]) == (
            "setting.update",
            "app_setting",
            "late_fee_ugx",
        )
        assert r["actor_id"] == "u_admin" and r["actor_roles"] == "opsadmin"
        assert r["reason"] == "Board decision 12" and r["ip"] == "203.0.113.9"
        assert r["request_id"] == "req-1"
        assert json.loads(r["before"])["fields"] == {"value": "5000"}
        assert json.loads(r["after"])["fields"] == {"value": "7000"}
        assert meta(r)["source"] == "admin" and meta(r)["status"] == "intent"
        assert repo.get("app_setting", "late_fee_ugx")["value"] == "7000"

    def test_resolving_a_dispute_writes_exactly_one_audit_row(self):
        repo, _, audited, _ = build()
        seed(repo, "dispute", "d1", {"status": "open", "session_id": "s1"})
        audited.update(
            "dispute", "d1", {"status": "resolved", "resolution_note": "refund agreed"},
            ctx=admin_ctx(), action="dispute.resolve", reason="Teacher no-show confirmed",
        )  # fmt: skip
        rows = audit_rows(repo)
        assert [(r["action"], r["entity"], r["entity_id"]) for r in rows] == [
            ("dispute.resolve", "dispute", "d1")
        ]
        assert rows[0]["reason"] == "Teacher no-show confirmed"

    def test_opening_a_case_writes_exactly_one_audit_row(self):
        repo, _, audited, _ = build()
        audited.create(
            "safeguarding_case", "c1", {"status": "open", "session_id": "s1", "description": "x"},
            ctx=user_ctx(actor="u_teacher"), action="safeguarding_case.open",
        )  # fmt: skip
        rows = audit_rows(repo)
        assert len(rows) == 1 and rows[0]["entity"] == "safeguarding_case"
        assert rows[0]["actor_id"] == "u_teacher"
        assert "x" not in json.loads(rows[0]["after"])["fields"].values()  # narrative redacted
        assert repo.get("safeguarding_case", "c1")["status"] == "open"

    def test_delete_writes_one_row_with_before_state(self):
        repo, _, audited, _ = build()
        seed(repo, "bonus_rule", "b1", {"name": "x", "rate": 3})
        audited.delete("bonus_rule", "b1", ctx=admin_ctx())
        rows = audit_rows(repo)
        assert len(rows) == 1 and rows[0]["action"] == "bonus_rule.delete"
        assert json.loads(rows[0]["before"])["fields"]["rate"] == 3
        with pytest.raises(NotFound):
            repo.get("bonus_rule", "b1")


class TestEveryAuditedTableIsCovered:
    @pytest.mark.parametrize("table", sorted(AUDITED_TABLES - APPEND_ONLY_TABLES))
    def test_create_update_delete_each_write_one_row(self, table):
        repo, _, audited, _ = build()
        ctx = admin_ctx()
        audited.create(table, "r1", {"a": 1}, ctx=ctx, reason="r")
        audited.update(table, "r1", {"a": 2}, ctx=ctx, reason="r")
        audited.delete(table, "r1", ctx=ctx, reason="r")
        assert [r["action"] for r in audit_rows(repo)] == [
            f"{table}.create", f"{table}.update", f"{table}.delete"
        ]  # fmt: skip

    @pytest.mark.parametrize("table", sorted(AUDITED_TABLES & APPEND_ONLY_TABLES))
    def test_append_only_audited_tables_allow_create_only(self, table):
        repo, _, audited, _ = build()
        audited.create(table, "r1", {"a": 1}, ctx=admin_ctx())
        with pytest.raises(AppendOnlyViolation):
            audited.update(table, "r1", {"a": 2}, ctx=admin_ctx())
        with pytest.raises(AppendOnlyViolation):
            audited.delete(table, "r1", ctx=admin_ctx())
        assert [r["action"] for r in audit_rows(repo)] == [f"{table}.create"]
        assert repo.get(table, "r1")["a"] == 1

    def test_required_tables_are_in_the_set(self):
        need = {
            "invoice", "payment", "earning", "payout", "payout_batch", "ledger_account",
            "ledger_transaction", "ledger_entry", "tier", "tier_history", "bonus_rule",
            "session", "session_checkin", "session_confirmation", "cancellation", "dispute",
            "session_log", "session_log_topic", "session_log_struggle", "safeguarding_case",
            "safeguarding_action", "case_status", "app_setting", "role_assignment",
            "teacher_approval", "teacher_document",
        }  # fmt: skip
        assert need <= AUDITED_TABLES

    def test_non_audited_tables_pass_through_without_context_or_rows(self):
        repo, _, audited, _ = build()
        audited.create("job_run", "j1", {"n": 1})
        audited.update("job_run", "j1", {"n": 2})
        audited.delete("job_run", "j1")
        assert repo.list("audit_log") == []


class TestChokePoint:
    def test_audited_write_without_a_context_is_refused_and_nothing_is_written(self):
        repo, _, audited, _ = build()
        with pytest.raises(AuditContextMissing):
            audited.create("invoice", "i1", {"amount": 1})
        assert repo.list("invoice") == [] and repo.list("audit_log") == []

    def test_audit_row_is_written_before_the_business_write(self):
        repo = FaultyRepository()
        _, _, audited, _ = build(repo)
        repo.create("tier", "t1", {"rate": 1})
        repo.writes.clear()
        audited.update("tier", "t1", {"rate": 2}, ctx=admin_ctx())
        assert [(w[0], w[1]) for w in repo.writes] == [("create", "audit_log"), ("update", "tier")]

    def test_intent_write_failure_blocks_the_business_write(self):
        repo = FaultyRepository()
        _, _, audited, _ = build(repo)
        repo.create("tier", "t1", {"rate": 1})
        repo.arm(1, "before", error=RuntimeError("audit store down"))
        with pytest.raises(AuditUnavailable):
            audited.update("tier", "t1", {"rate": 2}, ctx=admin_ctx())
        assert repo.get("tier", "t1")["rate"] == 1  # fail closed: no unaudited change

    def test_audit_log_itself_cannot_be_written_through_the_wrapper(self):
        repo, _, audited, _ = build()
        for call in (
            lambda: audited.create("audit_log", "x", {}, ctx=admin_ctx()),
            lambda: audited.update("audit_log", "x", {}, ctx=admin_ctx()),
            lambda: audited.delete("audit_log", "x", ctx=admin_ctx()),
        ):
            with pytest.raises(AppendOnlyViolation):
                call()

    def test_service_writes_audit_log_only_through_the_append_only_wrapper(self):
        _, service, *_ = build()
        assert isinstance(service._log, AppendOnlyRepository)

    def test_get_and_list_pass_through_for_ordinary_audited_tables(self):
        repo, _, audited, _ = build()
        seed(repo, "invoice", "i1", {"amount": 5})
        assert audited.get("invoice", "i1")["amount"] == 5
        assert len(audited.list("invoice")) == 1
        audited.ping()

    @pytest.mark.parametrize("table", ["safeguarding_case", "safeguarding_action"])
    def test_plain_reads_of_case_tables_are_refused(self, table):
        repo, _, audited, _ = build()
        seed(repo, table, "c1", {"status": "open"})
        with pytest.raises(UnauditedRead):
            audited.get(table, "c1")
        with pytest.raises(UnauditedRead):
            audited.list(table)

    def test_bind_gives_a_view_with_a_fixed_context(self):
        repo, _, audited, _ = build()
        seed(repo, "tier", "t1", {"rate": 1})
        bound = audited.bind(admin_ctx())
        bound.update("tier", "t1", {"rate": 2})
        assert audit_rows(repo)[0]["actor_id"] == "u_admin"
        assert isinstance(bound, AuditedRepository)


class TestActorBinding:
    def test_actor_comes_from_the_context_never_from_the_row_data(self):
        repo, _, audited, _ = build()
        seed(repo, "dispute", "d1", {"status": "open"})
        audited.update(
            "dispute", "d1", {"status": "resolved", "actor_id": "u_evil", "actor": "root"},
            ctx=admin_ctx(actor="u_real"), action="dispute.resolve", reason="ok",
        )  # fmt: skip
        r = audit_rows(repo)[0]
        assert r["actor_id"] == "u_real"

    def test_context_requires_an_actor_for_user_and_admin_sources(self):
        with pytest.raises(ValueError):
            AuditContext.for_principal("", ["parent"], request_id="r")
        with pytest.raises(ValueError):
            AuditContext.for_principal("   ", ["parent"], request_id="r")

    def test_source_is_admin_for_staff_roles_and_user_otherwise(self):
        assert AuditContext.for_principal("u", ["parent"], request_id="r").source == "user"
        assert AuditContext.for_principal("u", ["teacher"], request_id="r").source == "user"
        for role in ("opsadmin", "curator", "safeguardinglead", "superadmin"):
            assert AuditContext.for_principal("u", [role], request_id="r").source == "admin"

    def test_unknown_roles_are_dropped_from_the_stored_roles(self):
        ctx = AuditContext.for_principal("u", ["opsadmin", "made_up", "Parent"], request_id="r")
        assert ctx.roles == ("opsadmin",)


class TestSystemActor:
    def test_system_job_rows_have_no_actor_and_name_the_job(self):
        repo, _, audited, _ = build()
        seed(repo, "tier", "t1", {"rate": 1})
        ctx = AuditContext.system("tier-evaluation", run_key="tier-evaluation:2027-03#chunk0")
        audited.update("tier", "t1", {"rate": 2}, ctx=ctx, action="tier.evaluate")
        r = audit_rows(repo)[0]
        assert r["actor_id"] is None and r["actor_roles"] == "system"
        m = meta(r)
        assert m["source"] == "system" and m["job"] == "tier-evaluation"

    def test_system_context_needs_a_job_name(self):
        with pytest.raises(ValueError):
            AuditContext.system("", run_key="k")

    def test_a_system_context_cannot_carry_an_actor(self):
        ctx = AuditContext.system("j", run_key="k")
        assert ctx.actor_id is None and ctx.source == "system"


class TestReasonRegistry:
    @pytest.mark.parametrize("action", sorted(REASON_REQUIRED))
    @pytest.mark.parametrize("reason", [None, "", "   ", "\n\t"])
    def test_missing_or_blank_reason_raises_before_anything_is_written(self, action, reason):
        repo, _, audited, _ = build()
        seed(repo, "tier", "t1", {"rate": 1})
        with pytest.raises(ReasonRequired) as e:
            audited.update("tier", "t1", {"rate": 2}, ctx=admin_ctx(), action=action, reason=reason)
        assert action in str(e.value)
        assert repo.get("tier", "t1")["rate"] == 1 and repo.list("audit_log") == []

    def test_registry_contains_the_actions_the_ticket_names(self):
        for a in (
            "dispute.resolve", "tier.override", "payment.refund", "user.suspend",
            "payout.approve", "setting.update", "ledger.manual_adjustment",
        ):  # fmt: skip
            assert a in REASON_REQUIRED

    def test_actions_outside_the_registry_do_not_need_a_reason(self):
        repo, _, audited, _ = build()
        seed(repo, "tier", "t1", {"rate": 1})
        audited.update("tier", "t1", {"rate": 2}, ctx=admin_ctx())
        assert audit_rows(repo)[0]["reason"] is None

    def test_default_action_of_the_settings_table_requires_a_reason(self):
        repo, _, audited, _ = build()
        seed(repo, "app_setting", "k", {"value": "1"})
        with pytest.raises(ReasonRequired):
            audited.update("app_setting", "k", {"value": "2"}, ctx=admin_ctx())

    def test_reason_is_scrubbed_and_capped_to_the_column_size(self):
        repo, _, audited, _ = build()
        seed(repo, "dispute", "d1", {"status": "open"})
        audited.update(
            "dispute", "d1", {"status": "resolved"}, ctx=admin_ctx(), action="dispute.resolve",
            reason="called +256700123456 " + "é" * 900,
        )  # fmt: skip
        r = audit_rows(repo)[0]["reason"]
        assert "256700123456" not in r and len(r) <= 500

    def test_run_change_for_non_table_actions_enforces_the_registry_too(self):
        _, service, *_ = build()
        with pytest.raises(ReasonRequired):
            service.run_change(
                admin_ctx(), action="user.suspend", entity="user", entity_id="u1", op="action",
                before={"status": "active"}, after={"status": "suspended"}, reason=" ",
                apply=lambda: None,
            )  # fmt: skip


class TestFailedBusinessWrite:
    def test_failed_write_leaves_the_entity_alone_and_marks_the_audit_row_failed(self):
        repo = FaultyRepository()
        _, _, audited, _ = build(repo)
        repo.create("tier", "t1", {"rate": 1})
        repo.arm(0)
        repo.plan = {2: RuntimeError("appwrite 500 with a secret hunter2")}  # write 2 = tier
        with pytest.raises(RuntimeError):
            audited.update("tier", "t1", {"rate": 2}, ctx=admin_ctx())
        repo.disarm()
        assert repo.get("tier", "t1")["rate"] == 1
        rows = audit_rows(repo)
        assert [meta(r)["status"] for r in rows] == ["intent", "failed"]
        failed = meta(rows[1])
        assert failed["ref"] == rows[0]["$id"] and failed["error"] == "RuntimeError"
        assert "hunter2" not in json.dumps(rows)  # class name only, never the message
        assert rows[1]["action"] == rows[0]["action"] and rows[1]["entity_id"] == "t1"

    def test_missing_entity_on_update_changes_nothing_and_writes_no_row(self):
        repo, _, audited, _ = build()
        with pytest.raises(NotFound):
            audited.update("tier", "ghost", {"rate": 2}, ctx=admin_ctx())
        assert repo.list("tier") == [] and repo.list("audit_log") == []

    def test_failed_outcome_write_failing_too_is_left_to_recovery(self):
        repo = FaultyRepository()
        _, _, audited, _ = build(repo)
        repo.create("tier", "t1", {"rate": 1})
        repo.arm(0)
        repo.plan = {2: RuntimeError("tier write failed"), 3: RuntimeError("audit down too")}
        with pytest.raises(RuntimeError, match="tier write failed"):
            audited.update("tier", "t1", {"rate": 2}, ctx=admin_ctx())
        repo.disarm()
        assert [meta(r)["status"] for r in audit_rows(repo)] == ["intent"]


class TestIdempotencyAndRetries:
    def test_same_request_retried_in_a_new_process_writes_no_second_row(self):
        repo, _, audited, clock = build()
        seed(repo, "tier", "t1", {"rate": 1})
        audited.update("tier", "t1", {"rate": 2}, ctx=admin_ctx(request_id="req-A"))
        # client retries the HTTP request: new context object, same request id
        _, _, audited2, _ = build(repo, clock)
        audited2.update("tier", "t1", {"rate": 2}, ctx=admin_ctx(request_id="req-A"))
        assert len(audit_rows(repo)) == 1
        assert repo.get("tier", "t1")["rate"] == 2

    def test_retry_of_a_create_that_already_landed_returns_the_row_with_one_audit_row(self):
        repo, _, audited, clock = build()
        data = {"status": "open"}
        audited.create("safeguarding_case", "c1", data, ctx=user_ctx(request_id="r1"))
        _, _, audited2, _ = build(repo, clock)
        row = audited2.create("safeguarding_case", "c1", data, ctx=user_ctx(request_id="r1"))
        assert row["$id"] == "c1" and len(audit_rows(repo)) == 1

    def test_a_genuine_duplicate_create_in_a_new_request_is_a_conflict_and_marked_failed(self):
        repo, _, audited, _ = build()
        audited.create("safeguarding_case", "c1", {"s": 1}, ctx=user_ctx(request_id="r1"))
        with pytest.raises(Conflict):
            audited.create("safeguarding_case", "c1", {"s": 2}, ctx=user_ctx(request_id="r2"))
        statuses = [meta(r)["status"] for r in audit_rows(repo)]
        assert statuses == ["intent", "intent", "failed"]

    def test_two_changes_to_one_entity_in_one_request_get_two_rows(self):
        repo, _, audited, _ = build()
        seed(repo, "tier", "t1", {"rate": 1})
        ctx = admin_ctx()
        audited.update("tier", "t1", {"rate": 2}, ctx=ctx)
        audited.update("tier", "t1", {"rate": 3}, ctx=ctx)
        rows = audit_rows(repo)
        assert len(rows) == 2 and len({r["$id"] for r in rows}) == 2

    def test_row_id_is_deterministic_and_column_safe(self):
        _, service, *_ = build()
        a = service.intent_row_id("req:tier:t1:0")
        assert a == service.intent_row_id("req:tier:t1:0") and len(a) <= 36
        assert a != service.intent_row_id("req:tier:t1:1")

    def test_a_failed_attempt_is_followed_by_a_new_attempt_not_a_reuse(self):
        repo = FaultyRepository()
        _, _, audited, _ = build(repo)
        repo.create("tier", "t1", {"rate": 1})
        repo.arm(0)
        repo.plan = {2: RuntimeError("blip")}
        ctx = admin_ctx(request_id="req-R")
        with pytest.raises(RuntimeError):
            audited.update("tier", "t1", {"rate": 2}, ctx=ctx)
        repo.disarm()
        audited.update("tier", "t1", {"rate": 2}, ctx=ctx)  # retry in the same request
        assert [meta(r)["status"] for r in audit_rows(repo)] == ["intent", "failed", "intent"]


class TestOrderingAndConcurrency:
    def test_sequence_per_entity_increments_and_is_independent_between_entities(self):
        repo, _, audited, _ = build()
        seed(repo, "tier", "t1", {"rate": 0})
        seed(repo, "tier", "t2", {"rate": 0})
        for i in range(1, 4):
            audited.update("tier", "t1", {"rate": i}, ctx=admin_ctx(request_id=f"a{i}"))
        audited.update("tier", "t2", {"rate": 9}, ctx=admin_ctx(request_id="b1"))
        t1 = [meta(r)["seq"] for r in audit_rows(repo, entity_id="t1")]
        t2 = [meta(r)["seq"] for r in audit_rows(repo, entity_id="t2")]
        assert t1 == [1, 2, 3] and t2 == [1]

    def test_concurrent_updates_to_one_entity_produce_a_consistent_ordered_chain(self):
        repo, _, audited, _ = build()
        seed(repo, "tier", "t1", {"rate": 0})
        n = 12
        barrier = threading.Barrier(n)

        def work(i):
            barrier.wait()
            audited.update("tier", "t1", {"rate": i + 100}, ctx=admin_ctx(request_id=f"c{i}"))

        threads = [threading.Thread(target=work, args=(i,)) for i in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        rows = sorted(audit_rows(repo, entity="tier"), key=lambda r: meta(r)["seq"])
        assert [meta(r)["seq"] for r in rows] == list(range(1, n + 1))
        # chain: each row's "before" is the previous row's "after"
        prev = 0
        for r in rows:
            b = json.loads(r["before"])["fields"].get("rate")
            assert b == prev
            prev = json.loads(r["after"])["fields"]["rate"]
        assert repo.get("tier", "t1")["rate"] == prev
        # occurred_at follows the sequence too
        assert [r["occurred_at"] for r in rows] == sorted(r["occurred_at"] for r in rows)


class TestPayloadThroughTheRepository:
    def test_huge_before_and_after_are_truncated_with_a_marker_and_full_hash(self):
        repo, _, audited, _ = build()
        seed(repo, "tier", "t1", {"criteria": "a" * 50_000})
        audited.update("tier", "t1", {"criteria": "b" * 80_000}, ctx=admin_ctx())
        r = audit_rows(repo)[0]
        for col in ("before", "after"):
            assert len(r[col]) <= 16_000
            d = json.loads(r[col])
            assert d["truncated"] is True and len(d["hash"]) == 64

    def test_unicode_values_roundtrip(self):
        repo, _, audited, _ = build()
        seed(repo, "tier", "t1", {"label": "Dourado"})
        audited.update("tier", "t1", {"label": "Ouro ❤ 日本語 🚀"}, ctx=admin_ctx())
        assert json.loads(audit_rows(repo)[0]["after"])["fields"]["label"] == "Ouro ❤ 日本語 🚀"

    def test_secrets_in_changed_rows_never_reach_the_audit_log(self):
        repo, _, audited, _ = build()
        seed(repo, "user_profile", "u1", {"status": "active", "phone": "+256700111222"})
        audited.update(
            "user_profile", "u1",
            {"phone": "+256700333444", "password": "hunter2", "api_key": "k-123456789012345",
             "otp": "123456", "lat_e6": 123, "status": "suspended"},
            ctx=admin_ctx(), action="user.suspend", reason="fraud review",
        )  # fmt: skip
        text = json.dumps(repo.list("audit_log"))
        for secret in ("256700111222", "256700333444", "hunter2", "k-123456789012345", "123456"):
            assert secret not in text

    def test_request_id_longer_than_the_column_is_shortened_but_still_dedupes(self):
        repo, _, audited, clock = build()
        seed(repo, "tier", "t1", {"rate": 0})
        rid = "r" * 64
        audited.update("tier", "t1", {"rate": 1}, ctx=admin_ctx(request_id=rid))
        assert len(audit_rows(repo)[0]["request_id"]) <= 36
        _, _, audited2, _ = build(repo, clock)
        audited2.update("tier", "t1", {"rate": 1}, ctx=admin_ctx(request_id=rid))
        assert len(audit_rows(repo)) == 1

    def test_ipv6_and_missing_ip(self):
        repo, _, audited, _ = build()
        seed(repo, "tier", "t1", {"rate": 0})
        ip6 = "2001:0db8:85a3:0000:0000:8a2e:0370:7334"
        audited.update("tier", "t1", {"rate": 1}, ctx=admin_ctx(ip=ip6))
        audited.update("tier", "t1", {"rate": 2}, ctx=admin_ctx(request_id="r2", ip=None))
        rows = audit_rows(repo)
        assert rows[0]["ip"] == ip6 and rows[1]["ip"] is None

    def test_permissions_follow_the_q12_flag(self):
        repo, _, audited, _ = build()
        seed(repo, "tier", "t1", {"rate": 0})
        audited.update("tier", "t1", {"rate": 1}, ctx=admin_ctx())
        perms = audit_rows(repo)[0]["$permissions"]
        assert 'read("label:opsadmin")' in perms and 'read("label:safeguardinglead")' in perms
        assert not any("update" in p or "delete" in p or "write" in p for p in perms)
        repo2, _, audited2, _ = build(lead_blanket_read=False)
        seed(repo2, "tier", "t1", {"rate": 0})
        audited2.update("tier", "t1", {"rate": 1}, ctx=admin_ctx())
        assert 'read("label:safeguardinglead")' not in audit_rows(repo2)[0]["$permissions"]


class TestNonTableAdminActions:
    def test_role_or_label_change_via_run_change_writes_one_row(self):
        repo, service, *_ = build()
        applied = []
        service.run_change(
            admin_ctx(), action="user.label.add", entity="user", entity_id="u9", op="action",
            before={"labels": ["parent"]}, after={"labels": ["parent", "curator"]},
            reason=None, apply=lambda: applied.append(1),
        )  # fmt: skip
        rows = audit_rows(repo)
        assert applied == [1] and len(rows) == 1 and rows[0]["entity"] == "user"
        assert json.loads(rows[0]["after"])["fields"] == {"labels": ["parent", "curator"]}

    def test_failed_admin_action_is_marked_failed(self):
        repo, service, *_ = build()

        def boom():
            raise RuntimeError("appwrite said no")

        with pytest.raises(RuntimeError):
            service.run_change(
                admin_ctx(), action="user.label.add", entity="user", entity_id="u9", op="action",
                before={}, after={"x": 1}, reason=None, apply=boom,
            )  # fmt: skip
        assert [meta(r)["status"] for r in audit_rows(repo)] == ["intent", "failed"]

    def test_record_writes_a_single_idempotent_row(self):
        repo, service, *_ = build()
        ctx = admin_ctx(request_id="rq")
        service.record(ctx, action="export.run", entity="report", entity_id="r1", reason="audit")
        rows = audit_rows(repo)
        assert len(rows) == 1 and meta(rows[0])["status"] == "recorded"
        service2 = AuditService(repo, clock=StepClock())
        service2.record(
            admin_ctx(request_id="rq"), action="export.run", entity="report", entity_id="r1"
        )
        assert len(audit_rows(repo)) == 1


def test_in_memory_repository_unavailable_during_intent_is_audit_unavailable():
    repo = InMemoryRepository()
    _, _, audited, _ = build(repo)
    repo.create("tier", "t1", {"rate": 1})
    repo.available = False
    with pytest.raises(Exception):  # noqa: B017 - get() of the before-state fails first
        audited.update("tier", "t1", {"rate": 2}, ctx=admin_ctx())
    repo.available = True
    assert repo.get("tier", "t1")["rate"] == 1
