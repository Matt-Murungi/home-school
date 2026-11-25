"""M06 (#41): mutation tests. Each deliberately broken variant of the audit code must FAIL the
contract below (the same contract the real implementation passes). If someone weakens the
implementation in one of these ways, the suite goes red."""

import json
import uuid

import pytest

from audit_helpers import FaultyRepository, admin_ctx, audit_rows, build
from hs_core.audit import (
    AuditedRepository,
    AuditService,
    AuditUnavailable,
    NotWritten,
    ReasonRequired,
    payload,
    registry,
)
from hs_core.audit.recover import recover_audit
from hs_core.repository import InMemoryRepository


def contract(make):
    """`make()` returns (repo, service, audited, clock). Raises AssertionError on a defect."""
    # 1. every update writes exactly one audit row (the update path is audited)
    repo, _, audited, clock = make()
    repo.create("tier", "t1", {"rate": 1})
    audited.update("tier", "t1", {"rate": 2}, ctx=admin_ctx("m1"))
    assert len(audit_rows(repo)) == 1, "update path wrote no (or several) audit rows"

    # 2. a retry of the same request writes no second row
    _, _, audited2, _ = make(repo, clock)
    audited2.update("tier", "t1", {"rate": 2}, ctx=admin_ctx("m1"))
    assert len(audit_rows(repo)) == 1, "retry double-wrote the audit row"

    # 3. secrets never reach the log
    repo3, _, audited3, _ = make()
    repo3.create("user_profile", "u1", {"status": "active", "phone": "+256700555666"})
    audited3.update(
        "user_profile", "u1", {"phone": "+256700777888", "password": "hunter2-canary"},
        ctx=admin_ctx("m3"),
    )  # fmt: skip
    text = json.dumps(repo3.list("audit_log"))
    assert "hunter2-canary" not in text and "256700777888" not in text, "secret logged"

    # 4. a failed change is marked failed
    repo4 = FaultyRepository()
    _, _, audited4, _ = make(repo4)
    repo4.create("tier", "t1", {"rate": 1})
    repo4.arm(2, "before", error=NotWritten("x"))  # proves nothing was written
    with pytest.raises(NotWritten):
        audited4.update("tier", "t1", {"rate": 2}, ctx=admin_ctx("m4"))
    statuses = [json.loads(r["after"])["meta"]["status"] for r in audit_rows(repo4)]
    assert statuses == ["intent", "failed"], "failed change not marked"

    # 5. required reason is enforced
    repo5, _, audited5, _ = make()
    repo5.create("tier", "t1", {"rate": 1})
    try:
        audited5.update("tier", "t1", {"rate": 2}, ctx=admin_ctx("m5"), action="tier.override")
    except ReasonRequired:
        pass
    else:
        raise AssertionError("missing reason accepted")

    # 6. the intent is written BEFORE the business write
    repo6 = FaultyRepository()
    _, _, audited6, _ = make(repo6)
    repo6.create("tier", "t1", {"rate": 1})
    repo6.arm(1, "before", error=RuntimeError("audit store down"))
    try:
        audited6.update("tier", "t1", {"rate": 2}, ctx=admin_ctx("m6"))
    except Exception:  # noqa: BLE001
        pass
    assert repo6.get("tier", "t1")["rate"] == 1, "business change landed without an audit row"

    # 7. oversize values are cut to the column limit
    repo7, _, audited7, _ = make()
    repo7.create("tier", "t1", {"criteria": "a"})
    audited7.update("tier", "t1", {"criteria": "b" * 90_000}, ctx=admin_ctx("m7"))
    assert max(len(r["after"]) for r in audit_rows(repo7)) <= 16_000, "no truncation"

    # 8. recovery settles a crashed change
    repo8 = FaultyRepository()
    _, svc8, audited8, clock8 = make(repo8)
    repo8.create("tier", "t1", {"rate": 1})
    repo8.arm(1, "after", error=BaseException("crash"))
    try:
        audited8.update("tier", "t1", {"rate": 2}, ctx=admin_ctx("m8"))
    except BaseException:  # noqa: BLE001
        pass
    repo8.disarm()
    clock8.advance(minutes=30)
    from datetime import timedelta

    recover_audit(repo8, svc8, now=clock8(), grace=timedelta(minutes=5),
                  lookback=timedelta(hours=6))  # fmt: skip
    statuses = [json.loads(r["after"])["meta"]["status"] for r in audit_rows(repo8)]
    assert statuses == ["intent", "not_applied"], "crash not settled"

    # 9. a client-chosen/reused key cannot suppress a DIFFERENT change (round 2, blocker 1)
    repo9, _, audited9, _ = make()
    repo9.create("tier", "t1", {"status": "a"})
    audited9.update("tier", "t1", {"status": "b"}, ctx=admin_ctx("same-rid"))
    audited9.update("tier", "t1", {"status": "c"}, ctx=admin_ctx("same-rid"))
    assert len(audit_rows(repo9)) == 2, "shared key suppressed a different change"

    # 10. an ambiguous error (the write may have landed) is NOT recorded as failed (blocker 2)
    repo10 = FaultyRepository()
    _, _, audited10, _ = make(repo10)
    repo10.create("tier", "t1", {"rate": 1})
    repo10.arm(2, "after", error=TimeoutError("landed, then timed out"))
    with pytest.raises(TimeoutError):
        audited10.update("tier", "t1", {"rate": 2}, ctx=admin_ctx("m10"))
    statuses10 = [json.loads(r["after"])["meta"]["status"] for r in audit_rows(repo10)]
    assert statuses10 == ["intent"], "ambiguous error marked failed"

    # 11. a reason made of invisible characters is refused (blocker 3)
    repo11, _, audited11, _ = make()
    repo11.create("tier", "t1", {"rate": 1})
    try:
        audited11.update("tier", "t1", {"rate": 2}, ctx=admin_ctx("m11"),
                         action="tier.override", reason="\u200b\ufeff\u2800")  # fmt: skip
    except ReasonRequired:
        pass
    else:
        raise AssertionError("invisible reason accepted")


def real_make(repo=None, clock=None):
    return build(repo, clock)


def test_the_real_implementation_passes_the_contract():
    contract(real_make)


class TestMutationsFailTheContract:
    def test_mutation_skip_audit_on_the_update_path(self, monkeypatch):
        orig = AuditedRepository.update

        def update_without_audit(self, table, row_id, data, permissions=None, **kw):
            if table in registry.AUDITED_TABLES:
                return self._inner.update(table, row_id, data, permissions)
            return orig(self, table, row_id, data, permissions, **kw)

        monkeypatch.setattr(AuditedRepository, "update", update_without_audit)
        with pytest.raises(AssertionError, match="update path"):
            contract(real_make)

    def test_mutation_double_write_on_retry(self, monkeypatch):
        monkeypatch.setattr(AuditService, "intent_row_id", lambda self, key: uuid.uuid4().hex[:36])
        with pytest.raises(AssertionError, match="double-wrote"):
            contract(real_make)

    def test_mutation_log_secrets(self, monkeypatch):
        monkeypatch.setattr(payload, "is_redacted", lambda entity, key: False)
        with pytest.raises(AssertionError, match="secret logged"):
            contract(real_make)

    def test_mutation_failed_change_not_marked(self, monkeypatch):
        monkeypatch.setattr(AuditService, "_mark_failed", lambda self, *a, **k: None)
        with pytest.raises(AssertionError, match="not marked"):
            contract(real_make)

    def test_mutation_reason_not_enforced(self, monkeypatch):
        monkeypatch.setattr(registry, "REASON_REQUIRED", frozenset())
        with pytest.raises(AssertionError, match="missing reason accepted"):
            contract(real_make)

    def test_mutation_audit_after_the_business_write(self, monkeypatch):
        """business-then-audit: a failing audit store lets the change land unaudited."""
        orig = AuditService.run_change

        def run_change_late(self, ctx, *, apply, **kw):
            result = apply()
            orig(self, ctx, apply=lambda: result, **kw)
            return result

        monkeypatch.setattr(AuditService, "run_change", run_change_late)
        with pytest.raises((AssertionError, AuditUnavailable)):
            contract(real_make)

    def test_mutation_no_truncation(self, monkeypatch):
        monkeypatch.setattr(payload, "MAX_SIDE_CHARS", 10**9)
        with pytest.raises(AssertionError):
            contract(real_make)

    def test_mutation_recovery_does_nothing(self, monkeypatch):
        import hs_core.audit.recover as rec

        class Noop:
            def __getattr__(self, name):
                return 0

        monkeypatch.setattr(rec, "recover_audit", lambda *a, **k: Noop())
        # contract imported recover_audit by name: patch that reference too
        monkeypatch.setitem(contract.__globals__, "recover_audit", lambda *a, **k: Noop())
        with pytest.raises(AssertionError, match="crash not settled"):
            contract(real_make)

    def test_mutation_client_controlled_key(self, monkeypatch):
        """the pre-fix behaviour: any row under the key is a replay."""
        monkeypatch.setattr(AuditService, "_same_change", staticmethod(lambda *a, **k: True))
        with pytest.raises(AssertionError, match="suppressed"):
            contract(real_make)

    def test_mutation_ambiguous_error_marked_failed(self, monkeypatch):
        from hs_core.audit import service as svc

        monkeypatch.setattr(svc, "PROVEN_NOT_WRITTEN", (Exception,))
        with pytest.raises(AssertionError, match="ambiguous error marked failed"):
            contract(real_make)

    def test_mutation_zero_width_reason_accepted(self, monkeypatch):
        from hs_core.audit import service as svc

        monkeypatch.setattr(svc, "reason_is_enough", lambda r: bool((r or "").strip()))
        with pytest.raises(AssertionError, match="invisible reason accepted"):
            contract(real_make)

    def test_mutation_audit_log_updated_in_place_is_refused_by_the_wrapper(self):
        """Not a contract mutation: the append-only wrapper itself (M04) must still bite."""
        from hs_core.append_only import AppendOnlyViolation

        repo = InMemoryRepository()
        svc = AuditService(repo)
        with pytest.raises(AppendOnlyViolation):
            svc._log.update("audit_log", "x", {})
