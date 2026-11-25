"""M06 (#41) reviewer round 1 fixes: client-controlled keys, ambiguous errors, invisible reasons,
admin audit events and the keyed `record_external` adapter."""

import json
import unicodedata
from datetime import timedelta

import pytest
from fastapi import Depends, Request
from fastapi.testclient import TestClient

from audit_helpers import FaultyRepository, StepClock, admin_ctx, audit_rows, build, user_ctx
from hs_core.audit import (
    AuditContext,
    AuditedRepository,
    AuditService,
    ReasonRequired,
    read_audited,
)
from hs_core.audit.api import audit_context
from hs_core.audit.recover import recover_audit
from hs_core.auth import FakeTokenVerifier
from hs_core.main import create_app
from hs_core.repository import (
    Conflict,
    InMemoryRepository,
    NotFound,
    RepositoryUnavailable,
)
from hs_core.settings import Settings


def meta(row):
    return json.loads(row["after"])["meta"]


def statuses(repo):
    return [meta(r)["status"] for r in audit_rows(repo)]


# ---------------------------------------------------------------- BLOCKER 1: client-chosen keys
class TestSameRequestIdCannotSuppressRows:
    def test_two_different_updates_under_one_request_id_write_two_rows(self):
        repo, _, audited, _ = build()
        repo.create("tier", "t1", {"status": "active"})
        audited.update("tier", "t1", {"status": "paused"}, ctx=admin_ctx("same-rid"))
        audited.update("tier", "t1", {"status": "closed"}, ctx=admin_ctx("same-rid"))
        assert repo.get("tier", "t1")["status"] == "closed"
        rows = audit_rows(repo)
        assert len(rows) == 2
        assert [json.loads(r["after"])["fields"]["status"] for r in rows] == ["paused", "closed"]
        assert meta(rows[0]).get("key_collision") is None
        assert meta(rows[1])["key_collision"] is True

    def test_two_actors_reading_one_case_under_one_request_id_write_two_read_rows(self):
        repo, _, audited, _ = build()
        repo.create("safeguarding_case", "c1", {"status": "open"})
        read_audited(audited, admin_ctx("same-rid", actor="u_a"), "safeguarding_case", "c1")
        read_audited(audited, admin_ctx("same-rid", actor="u_b"), "safeguarding_case", "c1")
        rows = audit_rows(repo)
        assert sorted(r["actor_id"] for r in rows) == ["u_a", "u_b"]

    def test_two_different_actions_by_one_actor_under_one_request_id_write_two_rows(self):
        repo, service, *_ = build()
        ctx_a, ctx_b = admin_ctx("same-rid"), admin_ctx("same-rid")
        service.record(ctx_a, action="export.run", entity="report", entity_id="r1")
        service.record(ctx_b, action="export.download", entity="report", entity_id="r1")
        assert len(audit_rows(repo)) == 2

    def test_different_reads_by_one_actor_under_one_key_write_two_rows(self):
        repo, service, *_ = build()
        service.record(admin_ctx("k"), action="x.list", entity="x", entity_id="list",
                       after={"ids": ["a"]}, op="read")  # fmt: skip
        service.record(admin_ctx("k"), action="x.list", entity="x", entity_id="list",
                       after={"ids": ["b"]}, op="read")  # fmt: skip
        assert len(audit_rows(repo)) == 2

    def test_a_third_collision_gets_its_own_row_too(self):
        repo, _, audited, _ = build()
        repo.create("tier", "t1", {"status": "a"})
        for s in ("b", "c", "d"):
            audited.update("tier", "t1", {"status": s}, ctx=admin_ctx("same"))
        rows = audit_rows(repo)
        assert len(rows) == 3 and len({r["$id"] for r in rows}) == 3

    def test_collision_raises_a_counter_and_an_alert(self):
        alerts = []
        repo = InMemoryRepository()
        service = AuditService(
            repo, clock=StepClock(), alert=lambda kind, info: alerts.append(kind)
        )
        audited = AuditedRepository(repo, service)
        repo.create("tier", "t1", {"status": "a"})
        audited.update("tier", "t1", {"status": "b"}, ctx=admin_ctx("same"))
        audited.update("tier", "t1", {"status": "c"}, ctx=admin_ctx("same"))
        assert service.metrics["key_collision"] == 1 and alerts == ["audit.key_collision"]


class TestReplaysStillDedupe:
    def test_identical_change_retried_with_the_same_key_writes_one_row(self):
        repo, _, audited, _ = build()
        repo.create("tier", "t1", {"status": "a"})
        audited.update("tier", "t1", {"status": "b"}, ctx=admin_ctx("r"))
        audited.update("tier", "t1", {"status": "b"}, ctx=admin_ctx("r"))  # retried request
        rows = audit_rows(repo)
        assert len(rows) == 1 and meta(rows[0]).get("key_collision") is None

    def test_identical_read_by_the_same_actor_under_one_key_is_one_row(self):
        repo, _, audited, _ = build()
        repo.create("safeguarding_case", "c1", {"status": "open"})
        for _ in range(2):
            read_audited(audited, admin_ctx("r", actor="u_a"), "safeguarding_case", "c1")
        assert len(audit_rows(repo)) == 1

    def test_job_run_keys_stay_deterministic(self):
        repo, _, audited, _ = build()
        repo.create("tier", "t1", {"status": "a"})
        for _ in range(2):
            ctx = AuditContext.system("billing", run_key="run-7")
            audited.update("tier", "t1", {"status": "b"}, ctx=ctx)
        assert len(audit_rows(repo)) == 1
        audited.update(
            "tier", "t1", {"status": "c"}, ctx=AuditContext.system("billing", run_key="run-8")
        )
        rows = audit_rows(repo)
        assert len(rows) == 2 and all(r["actor_id"] is None for r in rows)

    def test_job_changing_one_entity_two_ways_in_one_run_writes_two_rows(self):
        repo, _, audited, _ = build()
        repo.create("tier", "t1", {"status": "a"})
        ctx = AuditContext.system("billing", run_key="run-7")
        audited.update("tier", "t1", {"status": "b"}, ctx=ctx)
        audited.update("tier", "t1", {"status": "c"}, ctx=ctx)
        assert len(audit_rows(repo)) == 2


class TestServerGeneratedKey:
    def make_app(self):
        raw = InMemoryRepository()
        v = FakeTokenVerifier()
        v.add("a", "u_a", ["opsadmin"])
        v.add("b", "u_b", ["opsadmin"])
        app = create_app(Settings(_env_file=None, hs_env="dev", otp_pepper="x" * 40),
                         repository=raw, verifier=v)  # fmt: skip
        raw.create("tier", "t1", {"status": "active"})

        @app.put("/test/tier/{tid}")
        def put_tier(
            tid: str,
            body: dict,
            request: Request,
            ctx: AuditContext = Depends(audit_context),  # noqa: B008
        ):
            return request.app.state.repository.update("tier", tid, body, ctx=ctx)

        return app, raw

    def test_a_spoofed_fixed_header_cannot_suppress_rows(self):
        app, raw = self.make_app()
        c = TestClient(app)
        h = lambda t: {"Authorization": f"Bearer {t}", "X-Request-ID": "fixed-id"}  # noqa: E731
        assert c.put("/test/tier/t1", json={"status": "paused"}, headers=h("a")).status_code == 200
        assert c.put("/test/tier/t1", json={"status": "closed"}, headers=h("b")).status_code == 200
        rows = audit_rows(raw)
        assert len(rows) == 2
        assert {r["actor_id"] for r in rows} == {"u_a", "u_b"}
        assert {r["request_id"] for r in rows} == {"fixed-id"}  # correlation field only
        assert "key_collision" not in json.dumps([meta(r) for r in rows])  # keys differ at source

    def test_the_audit_key_is_not_the_client_header(self):
        app, raw = self.make_app()
        c = TestClient(app)
        c.put("/test/tier/t1", json={"status": "x"},
              headers={"Authorization": "Bearer a", "X-Request-ID": "client-chosen"})  # fmt: skip
        assert "client-chosen" not in meta(audit_rows(raw)[0])["key"]

    def test_a_context_built_inside_a_request_uses_the_server_id(self):
        from hs_core.audit.context import server_request_key_var

        tok = server_request_key_var.set("srv-123")
        try:
            ctx = AuditContext.for_principal("u1", ["opsadmin"], request_id="client")
        finally:
            server_request_key_var.reset(tok)
        assert ctx.key_base == "srv-123" and ctx.request_id == "client"


class TestRecordExternal:
    """Deterministic caller-chosen id (ledger:<tx id>, template:<id>:<version>)."""

    def test_replay_of_the_same_content_is_one_row(self):
        repo, service, *_ = build()
        for _ in range(2):
            service.record_external("ledger:tx1", content_hash="h1", action="ledger.post",
                                    entity="ledger_transaction", entity_id="tx1",
                                    after={"amount": 5})  # fmt: skip
        rows = audit_rows(repo)
        assert len(rows) == 1 and meta(rows[0])["key"].startswith("ledger:tx1")

    def test_same_key_with_different_content_is_a_collision_not_a_dedupe(self):
        repo, service, *_ = build()
        service.record_external("ledger:tx1", content_hash="h1", action="ledger.post",
                                entity="ledger_transaction", entity_id="tx1")  # fmt: skip
        service.record_external("ledger:tx1", content_hash="h2", action="ledger.post",
                                entity="ledger_transaction", entity_id="tx1")  # fmt: skip
        rows = audit_rows(repo)
        assert len(rows) == 2 and meta(rows[1])["key_collision"] is True


# ---------------------------------------------------------------- BLOCKER 2: ambiguous errors
AMBIGUOUS = [
    TimeoutError("read timed out"),
    ConnectionError("reset"),
    OSError("socket"),
    RepositoryUnavailable("503"),
    RuntimeError("appwrite 500"),
    ValueError("who knows"),
]


class TestAmbiguousErrorsAreNotMarkedFailed:
    @pytest.mark.parametrize("err", AMBIGUOUS, ids=lambda e: type(e).__name__)
    def test_write_applied_then_raise_leaves_the_intent_and_no_failed_row(self, err):
        repo = FaultyRepository()
        _, service, audited, clock = build(repo)
        repo.create("tier", "t1", {"rate": 1})
        repo.arm(2, "after", error=err)  # write 2 = the tier update: it LANDS, then raises
        with pytest.raises(type(err)):
            audited.update("tier", "t1", {"rate": 2}, ctx=admin_ctx("r"))
        repo.disarm()
        assert repo.get("tier", "t1")["rate"] == 2
        assert statuses(repo) == ["intent"]  # never "failed": the change landed

    @pytest.mark.parametrize("err", AMBIGUOUS, ids=lambda e: type(e).__name__)
    def test_write_not_applied_and_raise_is_left_for_recovery(self, err):
        repo = FaultyRepository()
        _, service, audited, clock = build(repo)
        repo.create("tier", "t1", {"rate": 1})
        repo.arm(2, "before", error=err)
        with pytest.raises(type(err)):
            audited.update("tier", "t1", {"rate": 2}, ctx=admin_ctx("r"))
        repo.disarm()
        assert statuses(repo) == ["intent"]
        clock.advance(hours=1)
        res = recover_audit(repo, service, now=clock(), grace=timedelta(minutes=5),
                            lookback=timedelta(hours=6))  # fmt: skip
        assert res.not_applied == 1
        assert statuses(repo) == ["intent", "not_applied"]

    def test_recovery_confirms_a_landed_change_as_applied(self):
        repo = FaultyRepository()
        _, service, audited, clock = build(repo)
        repo.create("tier", "t1", {"rate": 1})
        repo.arm(2, "after", error=TimeoutError("t"))
        with pytest.raises(TimeoutError):
            audited.update("tier", "t1", {"rate": 2}, ctx=admin_ctx("r"))
        repo.disarm()
        clock.advance(hours=1)
        res = recover_audit(repo, service, now=clock(), grace=timedelta(minutes=5),
                            lookback=timedelta(hours=6))  # fmt: skip
        assert (res.applied, res.not_applied) == (1, 0)
        assert statuses(repo) == ["intent"]

    def test_create_that_lands_then_raises_is_not_failed(self):
        repo = FaultyRepository()
        _, _, audited, _ = build(repo)
        repo.arm(2, "after", error=TimeoutError("t"))
        with pytest.raises(TimeoutError):
            audited.create("tier", "t9", {"rate": 1}, ctx=admin_ctx("r"))
        repo.disarm()
        assert repo.get("tier", "t9")["rate"] == 1 and statuses(repo) == ["intent"]

    def test_delete_that_lands_then_raises_is_not_failed(self):
        repo = FaultyRepository()
        _, _, audited, _ = build(repo)
        repo.create("tier", "t1", {"rate": 1})
        repo.arm(2, "after", error=TimeoutError("t"))
        with pytest.raises(TimeoutError):
            audited.delete("tier", "t1", ctx=admin_ctx("r"))
        repo.disarm()
        assert statuses(repo) == ["intent"]

    def test_run_change_for_a_non_table_action_leaves_an_ambiguous_error_unmarked(self):
        repo, service, *_ = build()

        def boom():
            raise TimeoutError("applied remotely?")

        with pytest.raises(TimeoutError):
            service.run_change(admin_ctx("r"), action="user.label.add", entity="user",
                               entity_id="u9", op="action", before={}, after={"x": 1},
                               apply=boom)  # fmt: skip
        assert statuses(repo) == ["intent"]


class TestProvenNotWrittenErrorsAreMarkedFailed:
    def test_conflict_on_create_is_failed(self):
        repo, _, audited, _ = build()
        repo.create("tier", "t1", {"rate": 1})
        with pytest.raises(Conflict):
            audited.create("tier", "t1", {"rate": 2}, ctx=admin_ctx("r"))
        assert statuses(repo) == ["intent", "failed"]

    def test_not_written_marker_errors_are_failed(self):
        from hs_core.audit import NotWritten

        repo, service, *_ = build()

        def refuse():
            raise NotWritten("validation failed before the call")

        with pytest.raises(NotWritten):
            service.run_change(admin_ctx("r"), action="user.label.add", entity="user",
                               entity_id="u9", op="action", before={}, after={"x": 1},
                               apply=refuse)  # fmt: skip
        assert statuses(repo) == ["intent", "failed"]

    def test_not_found_inside_the_write_is_failed(self):
        repo, service, *_ = build()

        def gone():
            raise NotFound("404")

        with pytest.raises(NotFound):
            service.run_change(admin_ctx("r"), action="user.label.add", entity="user",
                               entity_id="u9", op="action", before={}, after={"x": 1},
                               apply=gone)  # fmt: skip
        assert statuses(repo) == ["intent", "failed"]


# ---------------------------------------------------------------- BLOCKER 3: invisible reasons
INVISIBLE = {
    "zwsp": "​",
    "zwsp x3": "​​​",
    "bom": "﻿",
    "braille blank": "⠀",
    "braille x5": "⠀" * 5,
    "zwnj": "‌",
    "zwj": "‍",
    "word joiner": "⁠",
    "soft hyphen": "­",
    "nbsp": " ",
    "ideographic space": "　",
    "line separator": " ",
    "paragraph separator": " ",
    "control": "\x00\x01\x07",
    "rlo": "‮",
    "mixed": " ​﻿⠀\t­⁠ ",
    "hangul filler": "ㅤ",
    "halfwidth hangul filler": "ﾠ",
    "combining grapheme joiner": "͏",
    "variation selector": "️",
    "lone combining mark": "́́́",
    "mongolian vowel separator": "᠎",
}


class TestInvisibleReasons:
    @pytest.mark.parametrize("text", INVISIBLE.values(), ids=INVISIBLE.keys())
    @pytest.mark.parametrize("action", ["tier.override", "dispute.resolve", "payment.refund"])
    def test_a_reason_without_visible_characters_is_refused_and_nothing_written(self, text, action):
        repo, _, audited, _ = build()
        repo.create("tier", "t1", {"rate": 1})
        with pytest.raises(ReasonRequired):
            audited.update("tier", "t1", {"rate": 2}, ctx=admin_ctx(), action=action, reason=text)
        assert repo.get("tier", "t1")["rate"] == 1 and repo.list("audit_log") == []

    @pytest.mark.parametrize("text", ["ab", "a​b", "​a​b​", "  x y  "])
    def test_fewer_than_three_visible_characters_is_refused(self, text):
        repo, _, audited, _ = build()
        repo.create("tier", "t1", {"rate": 1})
        with pytest.raises(ReasonRequired):
            audited.update("tier", "t1", {"rate": 2}, ctx=admin_ctx(), action="tier.override",
                           reason=text)  # fmt: skip

    def test_three_visible_characters_pass_and_the_stored_text_is_clean(self):
        repo, _, audited, _ = build()
        repo.create("tier", "t1", {"rate": 1})
        audited.update("tier", "t1", {"rate": 2}, ctx=admin_ctx(), action="tier.override",
                       reason="​a­b‍c﻿")  # fmt: skip
        assert audit_rows(repo)[0]["reason"] == "abc"

    def test_stored_reason_is_nfkc_normalised_without_invisible_or_control_characters(self):
        repo, _, audited, _ = build()
        repo.create("tier", "t1", {"rate": 1})
        audited.update("tier", "t1", {"rate": 2}, ctx=admin_ctx(), action="tier.override",
                       reason="ＡＢＣ board ok \x07 ‮done")  # fmt: skip
        stored = audit_rows(repo)[0]["reason"]
        assert stored == "ABC board ok done"
        assert not [c for c in stored if unicodedata.category(c) in ("Cf", "Cc", "Zl", "Zp")]

    def test_arabic_and_cjk_reasons_are_visible_text(self):
        repo, _, audited, _ = build()
        repo.create("tier", "t1", {"rate": 1})
        audited.update("tier", "t1", {"rate": 2}, ctx=admin_ctx(), action="tier.override",
                       reason="قرار المجلس")  # fmt: skip
        audited.update("tier", "t1", {"rate": 3}, ctx=admin_ctx("r2"), action="tier.override",
                       reason="理事会决定")  # fmt: skip
        assert [r["reason"] for r in audit_rows(repo)] == ["قرار المجلس", "理事会决定"]

    def test_run_change_and_record_enforce_it_too(self):
        _, service, *_ = build()
        with pytest.raises(ReasonRequired):
            service.record(admin_ctx(), action="user.suspend", entity="user", entity_id="u",
                           reason="​​​")  # fmt: skip

    def test_a_long_reason_is_capped_at_500_and_the_truncation_is_recorded(self):
        repo, _, audited, _ = build()
        repo.create("tier", "t1", {"rate": 1})
        audited.update("tier", "t1", {"rate": 2}, ctx=admin_ctx(), action="tier.override",
                       reason="reason " * 200)  # fmt: skip
        r = audit_rows(repo)[0]
        assert len(r["reason"]) <= 500
        assert meta(r)["reason_truncated"] is True and meta(r)["reason_chars"] == 1399

    def test_a_short_reason_is_not_flagged_truncated(self):
        repo, _, audited, _ = build()
        repo.create("tier", "t1", {"rate": 1})
        audited.update("tier", "t1", {"rate": 2}, ctx=admin_ctx(), action="tier.override",
                       reason="board decision")  # fmt: skip
        assert "reason_truncated" not in meta(audit_rows(repo)[0])


def test_user_ctx_helper_still_importable():
    assert user_ctx().actor_id == "u_parent"


# ---------------------------------------------------------------- non-blocking items
class TestUnverifiableIntentsAreSurfaced:
    def crashed_non_table_action(self):
        repo = FaultyRepository()
        alerts = []
        service = AuditService(
            repo, clock=StepClock(), alert=lambda kind, info: alerts.append((kind, dict(info)))
        )
        from audit_helpers import Crash

        repo.arm(1, "after")  # crash right after the intent row landed
        with pytest.raises(Crash):
            service.run_change(admin_ctx("r"), action="user.label.add", entity="user",
                               entity_id="u1", op="action", before={"l": []},
                               after={"l": ["x"]}, apply=lambda: None)  # fmt: skip
        repo.disarm()
        return repo, service, alerts

    def test_an_unverifiable_intent_raises_a_metric_and_an_alert(self):
        repo, service, alerts = self.crashed_non_table_action()
        service._clock.advance(hours=1)
        res = recover_audit(repo, service, now=service._clock(), grace=timedelta(minutes=5),
                            lookback=timedelta(hours=6))  # fmt: skip
        assert res.unverifiable == 1
        assert service.metrics["unverifiable_intent"] == 1
        assert [a[0] for a in alerts] == ["audit.unverifiable_intent"]
        assert alerts[0][1]["intent"] == res.unverifiable_ids[0]

    def test_the_job_report_lists_the_unverifiable_ids(self):
        from hs_core.audit.recover import report

        repo, service, _ = self.crashed_non_table_action()
        service._clock.advance(hours=1)
        res = recover_audit(repo, service, now=service._clock(), grace=timedelta(minutes=5),
                            lookback=timedelta(hours=6))  # fmt: skip
        rep = report(res)
        assert rep["unverifiable"] == 1 and rep["unverifiable_ids"] == res.unverifiable_ids

    def test_a_rerun_alerts_again_only_per_sweep_not_per_row_write(self):
        repo, service, alerts = self.crashed_non_table_action()
        service._clock.advance(hours=1)
        for _ in range(2):
            recover_audit(repo, service, now=service._clock(), grace=timedelta(minutes=5),
                          lookback=timedelta(hours=6))  # fmt: skip
        assert len(audit_rows(repo)) == 1  # nothing is written for an unverifiable intent
        assert len(alerts) == 2  # it keeps being reported until an operator settles it


class TestAuditReadsAreRecorded:
    def test_list_audit_with_a_context_records_one_ids_only_read_row(self):
        from hs_core.audit import list_audit

        repo, service, audited, _ = build()
        repo.create("tier", "t1", {"rate": 1})
        audited.update("tier", "t1", {"rate": 2}, ctx=admin_ctx("a"))
        page = list_audit(repo, roles=["opsadmin"], entity="tier", entity_id="t1",
                          audit=service, ctx=admin_ctx("b", actor="u_reader"))  # fmt: skip
        assert len(page.items) == 1
        reads = [r for r in audit_rows(repo) if r["action"] == "audit.read"]
        assert len(reads) == 1 and reads[0]["actor_id"] == "u_reader"
        fields = json.loads(reads[0]["after"])["fields"]
        assert fields == {"count": 1, "filters": ["entity", "entity_id"]}

    def test_list_audit_fails_closed_when_the_read_cannot_be_recorded(self):
        from hs_core.audit import AuditUnavailable, list_audit

        repo = FaultyRepository()
        _, service, *_ = build(repo)
        repo.arm(1, "before", error=RuntimeError("down"))
        with pytest.raises(AuditUnavailable):
            list_audit(repo, roles=["opsadmin"], audit=service, ctx=admin_ctx("b"))

    def test_a_forbidden_read_writes_nothing(self):
        from hs_core.audit import AuditForbidden, list_audit

        repo, service, *_ = build()
        with pytest.raises(AuditForbidden):
            list_audit(repo, roles=["parent"], audit=service, ctx=user_ctx())
        assert repo.list("audit_log") == []

    def test_a_context_without_a_service_is_a_programming_error(self):
        from hs_core.audit import list_audit

        repo, service, *_ = build()
        with pytest.raises(ValueError):
            list_audit(repo, roles=["opsadmin"], audit=service)


class TestRedactionOfGenericKeys:
    @pytest.mark.parametrize(
        "key",
        ["coordinates", "location", "pickup", "pin", "card_pan", "nin", "guardian", "comment",
         "address", "note", "description", "lat", "lng", "pickup_address", "guardian_name",
         "national_id_number"],
    )  # fmt: skip
    def test_the_key_is_redacted_in_any_audited_row_at_any_depth(self, key):
        repo, _, audited, _ = build()
        repo.create("session", "s1", {"status": "a"})
        audited.update("session", "s1", {key: "CANARY-VALUE", "nested": {key: "CANARY-NESTED"}},
                       ctx=admin_ctx("g"))  # fmt: skip
        assert "CANARY" not in json.dumps(repo.list("audit_log"))

    def test_title_is_decided_as_content_not_personal_data(self):
        """`title` (topic/question titles, curriculum) is catalogue text: kept (scrubbed)."""
        repo, _, audited, _ = build()
        repo.create("plan", "p1", {"title": "Basic"})
        audited.update("plan", "p1", {"title": "Premium"}, ctx=admin_ctx("t"))
        assert "Premium" in json.dumps(repo.list("audit_log"))

    @pytest.mark.parametrize(
        ("entity", "key"),
        [("subscription", "cancel_reason"), ("assignment", "end_reason"), ("earning", "note")],
    )
    def test_free_text_columns_of_money_and_plan_rows_are_redacted(self, entity, key):
        repo, _, audited, _ = build()
        repo.create(entity, "r1", {"status": "a"})
        audited.update(entity, "r1", {key: "CANARY the parent said her child is ill"},
                       ctx=admin_ctx("g"))  # fmt: skip
        assert "CANARY" not in json.dumps(repo.list("audit_log"))


# ---------------------------------------------------------------- round 3: failed rows re-checked
class TestFailedRowsAreRecheckedByRecovery:
    def landed_then_proof_error(self, op):
        """Conflict/NotFound raised AFTER the write landed (a driver that retried internally)."""
        repo = FaultyRepository()
        _, service, audited, clock = build(repo)
        if op != "create":
            repo.create("tier", "t1", {"rate": 1})
        err = Conflict("409") if op == "create" else NotFound("404")
        repo.arm(2, "after", error=err)
        with pytest.raises(type(err)):
            if op == "create":
                audited.create("tier", "t1", {"rate": 2}, ctx=admin_ctx("r"))
            elif op == "update":
                audited.update("tier", "t1", {"rate": 2}, ctx=admin_ctx("r"))
            else:
                audited.delete("tier", "t1", ctx=admin_ctx("r"))
        repo.disarm()
        assert statuses(repo) == ["intent", "failed"]  # the wrong row we must correct
        clock.advance(hours=1)
        return repo, service, clock

    def sweep(self, repo, service, clock):
        return recover_audit(repo, service, now=clock(), grace=timedelta(minutes=5),
                             lookback=timedelta(hours=6))  # fmt: skip

    @pytest.mark.parametrize("op", ["create", "update", "delete"])
    def test_a_failed_row_for_a_change_that_landed_gets_a_corrective_row(self, op):
        repo, service, clock = self.landed_then_proof_error(op)
        res = self.sweep(repo, service, clock)
        assert res.failed_but_applied == 1
        assert statuses(repo) == ["intent", "failed", "applied_after_failed"]
        assert service.metrics["failed_but_applied"] == 1
        corrective = audit_rows(repo)[-1]
        assert meta(corrective)["ref"] == audit_rows(repo)[0]["$id"]

    @pytest.mark.parametrize("op", ["create", "update", "delete"])
    def test_the_correction_is_idempotent_across_sweeps(self, op):
        repo, service, clock = self.landed_then_proof_error(op)
        for _ in range(3):
            self.sweep(repo, service, clock)
        assert statuses(repo).count("applied_after_failed") == 1

    def test_a_genuinely_failed_change_is_not_corrected(self):
        repo = FaultyRepository()
        _, service, audited, clock = build(repo)
        repo.create("tier", "t1", {"rate": 1})
        repo.arm(2, "before", error=NotFound("404"))  # nothing was written
        with pytest.raises(NotFound):
            audited.update("tier", "t1", {"rate": 2}, ctx=admin_ctx("r"))
        repo.disarm()
        clock.advance(hours=1)
        res = self.sweep(repo, service, clock)
        assert res.failed_but_applied == 0 and statuses(repo) == ["intent", "failed"]

    def test_a_failed_row_outside_the_lookback_is_left_alone(self):
        repo, service, clock = self.landed_then_proof_error("update")
        clock.advance(hours=24)
        assert self.sweep(repo, service, clock).failed_but_applied == 0
