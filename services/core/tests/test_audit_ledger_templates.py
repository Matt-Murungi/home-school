"""M06 round 3: the REAL ledger and template store audited through the adapters (no duck-typed
copies), the registry decision for the ledger tables, and the app wiring."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from audit_helpers import FaultyRepository, StepClock, audit_rows
from hs_core.audit import AuditService, AuditUnavailable, registry, tripwire
from hs_core.audit.adapters import LedgerAuditAdapter, TemplateAuditAdapter
from hs_core.auth import FakeTokenVerifier
from hs_core.ledger import AppwriteLedgerStore, LedgerService
from hs_core.ledger.chart import CASH_MTN, RECEIVABLES
from hs_core.ledger.errors import AuditFailed
from hs_core.ledger.model import Kind, TransactionRequest, credit, debit
from hs_core.main import create_app
from hs_core.notifications import engine
from hs_core.notifications.templates import Actor, Channel, TemplateStore
from hs_core.repository import InMemoryRepository
from hs_core.settings import Settings

SRC = Path(__file__).resolve().parents[1] / "src" / "hs_core"


def meta(row):
    return json.loads(row["after"])["meta"]


def ledger(repo=None):
    repo = repo if repo is not None else InMemoryRepository()
    clock = StepClock()
    audit = AuditService(repo, clock=clock)
    svc = LedgerService(AppwriteLedgerStore(repo), audit=LedgerAuditAdapter(audit))
    return repo, audit, svc


def pay(key="payment:p1", amount=10_000):
    return TransactionRequest(
        idempotency_key=key, kind=Kind.PAYMENT, reference_type="payment", reference_id="p1",
        lines=(debit(CASH_MTN, amount), credit(RECEIVABLES, amount)),
    )  # fmt: skip


def posts(repo):
    return [r for r in audit_rows(repo) if r["action"] == "ledger.post"]


def accounts(repo):
    return [r for r in audit_rows(repo) if r["action"] == "ledger.account_create"]


class TestRegistryDecision:
    @pytest.mark.parametrize("t", ["ledger_account", "ledger_transaction", "ledger_entry"])
    def test_ledger_tables_are_audited_by_summary_events_not_per_row(self, t):
        assert t not in registry.AUDITED_TABLES

    def test_the_tripwire_stays_honest_and_clean_on_the_real_tree(self):
        assert tripwire.find_unaudited_writes(SRC) == []

    def test_leaving_a_ledger_table_audited_without_an_audited_repo_fails_the_tripwire(
        self, monkeypatch
    ):
        monkeypatch.setattr(tripwire, "AUDITED_TABLES", registry.AUDITED_TABLES | {"ledger_entry"})
        found = tripwire.find_unaudited_writes(SRC)
        assert any("ledger/appwrite_store.py" in f for f in found)


class TestRealLedger:
    def test_a_post_and_its_replay_are_one_audit_row_and_no_collision(self):
        repo, audit, svc = ledger()
        svc.ensure_chart_of_accounts()
        a = svc.post(pay())
        b = svc.post(pay())
        assert a.id == b.id
        (row,) = posts(repo)
        assert meta(row)["key"].startswith(f"ledger:{a.id}:")
        assert audit.metrics["key_collision"] == 0

    def test_two_different_postings_are_two_rows(self):
        repo, _, svc = ledger()
        svc.ensure_chart_of_accounts()
        svc.post(pay("payment:p1"))
        svc.post(pay("payment:p2"))
        assert len(posts(repo)) == 2

    def test_chart_creation_is_audited_once_per_account_and_idempotent(self):
        repo, audit, svc = ledger()
        chart = svc.ensure_chart_of_accounts()
        n = len(accounts(repo))
        assert n == len(chart) > 0
        svc.ensure_chart_of_accounts()
        assert len(accounts(repo)) == n and audit.metrics["key_collision"] == 0
        assert all(meta(r)["key"].startswith("ledger-account:") for r in accounts(repo))

    def test_teacher_payable_creation_is_audited_once(self):
        repo, audit, svc = ledger()
        svc.ensure_teacher_payable("t1")
        svc.ensure_teacher_payable("t1")
        svc.ensure_teacher_payable("t2")
        assert len(accounts(repo)) == 2 and audit.metrics["key_collision"] == 0

    def test_account_events_carry_no_free_text_or_stamps(self):
        repo, _, svc = ledger()
        svc.ensure_teacher_payable("t1")
        text = json.dumps(accounts(repo))
        assert "created_at" not in text

    def test_audit_failure_after_the_seal_raises_AuditFailed_and_a_retry_repairs_it(self):
        repo = FaultyRepository()
        _, audit, svc = ledger(repo)
        svc.ensure_chart_of_accounts()
        down = {"on": True}
        real_create = repo.create

        def create(table, row_id, data, permissions=None):
            if table == "audit_log" and down["on"]:
                raise RuntimeError("audit store down")
            return real_create(table, row_id, data, permissions)

        repo.create = create
        with pytest.raises(AuditFailed):
            svc.post(pay())
        assert posts(repo) == []  # sealed, not audited yet
        down["on"] = False
        again = svc.post(pay())  # the ledger's own contract: repeat the same call
        (row,) = posts(repo)
        assert again.replayed and row["entity_id"] == again.id

    def test_a_replay_with_other_content_under_the_same_key_is_a_recorded_collision(self):
        repo, audit, svc = ledger()
        svc.ensure_chart_of_accounts()
        tx = svc.post(pay())
        adapter = LedgerAuditAdapter(audit)
        from hs_core.ledger.audit import AuditEvent

        adapter.record(AuditEvent(key=f"ledger:{tx.id}", action="ledger.post",
                                  entity_type="ledger_transaction", entity_id=tx.id,
                                  actor_id=None, detail={"total_amount": 1}))  # fmt: skip
        assert len(posts(repo)) == 2 and audit.metrics["key_collision"] == 1


VAR_TIME = engine.VarSpec("lesson_time", "datetime")
VAR_REASON = engine.VarSpec("reason_label", "str")


class TestRealTemplateStore:
    def publish(self, store, **kw):
        args = dict(
            key="session_cancelled_parent", channel=Channel.PUSH, locale="en", title="Cancelled",
            body="Lesson {{lesson_time}} cancelled: {{reason_label}}.",
            variables=(VAR_TIME, VAR_REASON), category="operational",
        )  # fmt: skip
        args.update(kw)
        return store.publish(Actor("u_admin", ("superadmin",)), **args)

    def test_publish_writes_one_ids_only_row_per_version(self):
        repo = InMemoryRepository()
        audit = AuditService(repo, clock=StepClock())
        store = TemplateStore(repo, audit=TemplateAuditAdapter(audit))
        v = self.publish(store)
        self.publish(store, title="Cancelled again")
        rows = [r for r in audit_rows(repo) if r["action"] == "notification_template.publish"]
        assert len(rows) == 2 and rows[0]["actor_id"] == "u_admin"
        assert rows[0]["entity_id"].endswith(f":{v.version}")
        assert "Lesson" not in json.dumps(rows)  # ids only, never copy text
        assert audit.metrics["key_collision"] == 0

    def test_audit_failure_is_loud(self):
        repo = FaultyRepository()
        audit = AuditService(repo, clock=StepClock())
        store = TemplateStore(repo, audit=TemplateAuditAdapter(audit))
        real_create = repo.create
        repo.create = lambda t, i, d, p=None: (
            (_ for _ in ()).throw(RuntimeError("down"))
            if t == "audit_log"
            else real_create(t, i, d, p)
        )
        with pytest.raises(AuditUnavailable):
            self.publish(store)


class TestAppWiring:
    def make(self):
        raw = InMemoryRepository()
        v = FakeTokenVerifier()
        app = create_app(
            Settings(_env_file=None, hs_env="dev", otp_pepper="x" * 40),
            repository=raw, verifier=v,
        )  # fmt: skip
        return app, raw

    def test_the_app_ledger_is_audited_through_the_adapter(self):
        app, raw = self.make()
        app.state.ledger.ensure_chart_of_accounts()
        app.state.ledger.post(pay())
        app.state.ledger.post(pay())
        assert len(posts(raw)) == 1 and len(accounts(raw)) > 0
        TestClient(app)  # app builds

    def test_the_app_template_store_is_audited_through_the_adapter(self):
        app, raw = self.make()
        TestRealTemplateStore().publish(app.state.notifications.store)
        rows = [r for r in audit_rows(raw) if r["action"] == "notification_template.publish"]
        assert len(rows) == 1
