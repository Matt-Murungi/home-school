"""M06 (#41): unit tests of the ledger/template adapters (end to end with the real services:
test_audit_ledger_templates.py)."""

import json

import pytest

from audit_helpers import audit_rows, build
from hs_core.audit.adapters import LedgerAuditAdapter, TemplateAuditAdapter
from hs_core.ledger.audit import AuditEvent  # the REAL event of the merged ledger


def meta(row):
    return json.loads(row["after"])["meta"]


def ev(**kw):
    base = dict(key="ledger:tx1", action="ledger.post", entity_type="ledger_transaction",
                entity_id="tx1", actor_id="u_ops",
                detail={"kind": "payment", "entry_count": 2, "total_amount": 45000})  # fmt: skip
    return AuditEvent(**{**base, **kw})


class TestLedgerAdapter:
    def test_a_post_writes_one_row_under_the_deterministic_key(self):
        repo, service, *_ = build()
        LedgerAuditAdapter(service).record(ev())
        (row,) = audit_rows(repo)
        assert (row["action"], row["entity"], row["entity_id"]) == (
            "ledger.post", "ledger_transaction", "tx1")  # fmt: skip
        assert row["actor_id"] == "u_ops" and meta(row)["key"].startswith("ledger:tx1:")
        assert json.loads(row["after"])["fields"]["total_amount"] == 45000

    def test_replaying_the_same_transaction_any_number_of_times_is_one_row(self):
        repo, service, *_ = build()
        sink = LedgerAuditAdapter(service)
        for _ in range(3):
            sink.record(ev())
        assert len(audit_rows(repo)) == 1

    def test_a_replay_in_a_new_process_is_still_one_row(self):
        repo, service, *_ = build()
        LedgerAuditAdapter(service).record(ev())
        _, service2, *_ = build(repo)
        LedgerAuditAdapter(service2).record(ev())
        assert len(audit_rows(repo)) == 1

    def test_same_key_with_different_content_is_a_collision_not_a_dedupe(self):
        repo, service, *_ = build()
        sink = LedgerAuditAdapter(service)
        sink.record(ev())
        sink.record(ev(detail={"kind": "payment", "entry_count": 2, "total_amount": 1}))
        rows = audit_rows(repo)
        assert len(rows) == 2 and meta(rows[1])["key_collision"] is True
        assert service.metrics["key_collision"] == 1

    def test_same_key_with_a_different_actor_is_a_collision(self):
        repo, service, *_ = build()
        sink = LedgerAuditAdapter(service)
        sink.record(ev())
        sink.record(ev(actor_id="u_other"))
        assert len(audit_rows(repo)) == 2

    def test_a_system_posting_has_no_actor(self):
        repo, service, *_ = build()
        LedgerAuditAdapter(service).record(ev(actor_id=None))
        assert audit_rows(repo)[0]["actor_id"] is None

    def test_an_unavailable_audit_store_raises_so_the_ledger_can_report_AuditFailed(self):
        from audit_helpers import FaultyRepository
        from hs_core.audit import AuditUnavailable

        repo = FaultyRepository()
        _, service, *_ = build(repo)
        repo.arm(1, "before", error=RuntimeError("down"))
        with pytest.raises(AuditUnavailable):
            LedgerAuditAdapter(service).record(ev())

    def test_secrets_and_free_text_in_the_detail_are_redacted(self):
        repo, service, *_ = build()
        detail = {"note": "CANARY parent story", "token": "CANARY-T", "amount": 5}
        LedgerAuditAdapter(service).record(ev(detail=detail))
        assert "CANARY" not in json.dumps(repo.list("audit_log"))


def publish(**kw):
    base = {"action": "notification_template.publish", "actor_id": "u_super"}
    base |= {"key": "payment_failed", "channel": "email", "locale": "en", "version": 3}
    return {**base, **kw}


class TestTemplateAdapter:
    def test_a_publish_writes_one_row_with_ids_only(self):
        repo, service, *_ = build()
        TemplateAuditAdapter(service)(publish())
        (row,) = audit_rows(repo)
        assert row["action"] == "notification_template.publish" and row["actor_id"] == "u_super"
        assert row["entity"] == "notification_template"
        assert row["entity_id"] == "payment_failed:email:en:3"
        assert meta(row)["key"].startswith("template:payment_failed:email:en:3")

    def test_a_replay_is_one_row_and_new_content_for_the_same_version_is_a_collision(self):
        repo, service, *_ = build()
        hook = TemplateAuditAdapter(service)
        hook(publish())
        hook(publish())
        assert len(audit_rows(repo)) == 1
        hook(publish(actor_id="u_else"))
        assert len(audit_rows(repo)) == 2

    def test_a_new_version_is_a_new_row(self):
        repo, service, *_ = build()
        hook = TemplateAuditAdapter(service)
        hook(publish())
        hook(publish(version=4))
        assert len(audit_rows(repo)) == 2

    def test_a_payload_that_is_not_a_publish_event_is_refused(self):
        _, service, *_ = build()
        with pytest.raises(ValueError):
            TemplateAuditAdapter(service)({"action": "x"})
