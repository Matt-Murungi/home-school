"""M06 (#41): reads of safeguarding cases are audited (`read_audited`), and the AST tripwires.

The tripwires are TRIPWIRES for literal table names, not coverage (same honesty as M04's
append-only scan); tests/test_audit_docs.py holds the wording.
"""

import json
import textwrap
from pathlib import Path

import pytest

from audit_helpers import FaultyRepository, admin_ctx, audit_rows, build, user_ctx
from hs_core.audit import AuditUnavailable, list_audited, read_audited
from hs_core.audit.tripwire import (
    find_unaudited_case_reads,
    find_unaudited_writes,
)
from hs_core.repository import NotFound

SRC = Path(__file__).resolve().parents[1] / "src" / "hs_core"


def meta(row):
    return json.loads(row["after"])["meta"]


class TestReadAudited:
    def test_reading_a_case_writes_one_read_row_with_the_actor_and_no_content(self):
        repo, _, audited, _ = build()
        repo.create("safeguarding_case", "c1", {"status": "open", "description": "secret story"})
        ctx = admin_ctx(actor="u_lead", request_id="rq-1")
        row = read_audited(audited, ctx, "safeguarding_case", "c1", purpose="triage")
        assert row["description"] == "secret story"
        rows = audit_rows(repo)
        assert len(rows) == 1
        r = rows[0]
        assert (r["action"], r["entity"], r["entity_id"]) == (
            "safeguarding_case.read", "safeguarding_case", "c1"
        )  # fmt: skip
        assert r["actor_id"] == "u_lead" and r["reason"] == "triage"
        assert meta(r)["op"] == "read"
        assert "secret story" not in json.dumps(r)

    def test_each_read_is_logged_even_by_the_same_actor_in_one_request(self):
        repo, _, audited, _ = build()
        repo.create("safeguarding_case", "c1", {"status": "open"})
        ctx = admin_ctx()
        read_audited(audited, ctx, "safeguarding_case", "c1")
        read_audited(audited, ctx, "safeguarding_case", "c1")
        assert len(audit_rows(repo)) == 2

    def test_a_retried_request_does_not_double_log(self):
        repo, _, audited, clock = build()
        repo.create("safeguarding_case", "c1", {"status": "open"})
        read_audited(audited, admin_ctx(request_id="same"), "safeguarding_case", "c1")
        _, _, audited2, _ = build(repo, clock)
        read_audited(audited2, admin_ctx(request_id="same"), "safeguarding_case", "c1")
        assert len(audit_rows(repo)) == 1

    def test_fails_closed_when_the_audit_row_cannot_be_written(self):
        repo = FaultyRepository()
        _, _, audited, _ = build(repo)
        repo.create("safeguarding_case", "c1", {"status": "open"})
        repo.arm(1, "before", error=RuntimeError("down"))
        with pytest.raises(AuditUnavailable):
            read_audited(audited, admin_ctx(), "safeguarding_case", "c1")

    def test_reading_a_missing_case_still_logs_the_attempt(self):
        repo, _, audited, _ = build()
        with pytest.raises(NotFound):
            read_audited(audited, admin_ctx(), "safeguarding_case", "nope")
        assert len(audit_rows(repo)) == 1

    def test_only_case_tables_are_accepted(self):
        _, _, audited, _ = build()
        with pytest.raises(ValueError):
            read_audited(audited, admin_ctx(), "invoice", "i1")

    def test_case_actions_are_covered_too(self):
        repo, _, audited, _ = build()
        repo.create("safeguarding_action", "a1", {"detail": "x"})
        read_audited(audited, admin_ctx(), "safeguarding_action", "a1")
        assert audit_rows(repo)[0]["entity"] == "safeguarding_action"

    def test_listing_logs_one_row_with_the_ids_returned_and_nothing_else(self):
        repo, _, audited, _ = build()
        for i in range(3):
            repo.create("safeguarding_case", f"c{i}", {"status": "open", "description": "story"})
        rows = list_audited(audited, admin_ctx(), "safeguarding_case")
        assert len(rows) == 3
        log = audit_rows(repo)
        assert len(log) == 1 and log[0]["action"] == "safeguarding_case.list"
        after = json.loads(log[0]["after"])["fields"]
        assert sorted(after["ids"]) == ["c0", "c1", "c2"] and after["count"] == 3
        assert "story" not in json.dumps(log[0])

    def test_a_teacher_opening_a_case_then_reading_status_is_not_a_case_read(self):
        """case_status is the reporter-visible table (ADR 0012): reading it is not audited here."""
        repo, _, audited, _ = build()
        repo.create("case_status", "c1", {"status": "open"})
        assert audited.get("case_status", "c1")["status"] == "open"
        assert audit_rows(repo) == []
        assert user_ctx()  # helper import used


class TestUnauditedWriteTripwire:
    def scan(self, tmp_path, code, name="mod.py"):
        (tmp_path / name).write_text(textwrap.dedent(code))
        return find_unaudited_writes(tmp_path)

    def test_the_real_source_tree_is_clean(self):
        assert find_unaudited_writes(SRC) == []
        assert find_unaudited_case_reads(SRC) == []

    @pytest.mark.parametrize(
        "code",
        [
            'def f(repo):\n    repo.update("invoice", "i1", {"a": 1})\n',
            'def f(repo):\n    repo.create("payment", "p1", {})\n',
            'def f(repo):\n    repo.delete("tier", "t1")\n',
            'T = "payout"\ndef f(repo):\n    repo.update(T, "p", {})\n',
            'def f(repo):\n    repo.update(table_id="dispute", row_id="d", data={})\n',
            'def f(c):\n    c.tables.update_row("session", "s", {})\n',
            'def f(c):\n    c.upsert_row("ledger_account", "s", {})\n',
            'async def f(repo):\n    await repo.update("app_setting", "k", {})\n',
            'X: str = "session_log"\ndef f(self):\n    self.repo.create(X, "s", {})\n',
        ],
    )
    def test_catches_an_unaudited_write_in_a_scratch_module(self, tmp_path, code):
        found = self.scan(tmp_path, code)
        assert len(found) == 1 and "mod.py" in found[0]

    @pytest.mark.parametrize(
        "code",
        [
            'def f(audited):\n    audited.update("invoice", "i1", {"a": 1}, ctx=c)\n',
            'def f(self):\n    self.audited_repo.create("payment", "p1", {}, ctx=c)\n',
            'def f(repo):\n    repo.update("job_run", "j", {})\n',  # not an audited table
            'def f(repo):\n    repo.get("invoice", "i1")\n',  # reads are not writes
            'def f(repo):\n    repo.list("invoice")\n',
        ],
    )
    def test_ignores_audited_receivers_other_tables_and_reads(self, tmp_path, code):
        assert self.scan(tmp_path, code) == []

    def test_known_misses_are_real_and_documented(self, tmp_path):
        """What a literal-name scan cannot see (the docs list these; this test keeps us honest)."""
        misses = [
            'def f(repo, t):\n    repo.update(t, "i", {})\n',  # parameter
            'def f(repo):\n    repo.update("inv" + "oice", "i", {})\n',  # computed
            'def f(repo):\n    getattr(repo, "update")("invoice", "i", {})\n',
            'def f(repo):\n    u = repo.update\n    u("invoice", "i", {})\n',
            'def f(repo):\n    repo.update(*["invoice", "i", {}])\n',
            'def f(audited):\n    audited = repo\n    audited.update("invoice","i",{})\n',
        ]
        for code in misses:
            assert self.scan(tmp_path, code) == [], code

    def test_audit_package_and_repository_modules_are_exempt(self, tmp_path):
        sub = tmp_path / "audit"
        sub.mkdir()
        (sub / "repo.py").write_text('def f(r):\n    r.update("invoice", "i", {})\n')
        (tmp_path / "repository.py").write_text('def f(r):\n    r.update("invoice", "i", {})\n')
        assert find_unaudited_writes(tmp_path) == []


class TestCaseReadTripwire:
    def scan(self, tmp_path, code):
        (tmp_path / "mod.py").write_text(textwrap.dedent(code))
        return find_unaudited_case_reads(tmp_path)

    @pytest.mark.parametrize(
        "code",
        [
            'def f(repo):\n    return repo.get("safeguarding_case", "c1")\n',
            'def f(repo):\n    return repo.list("safeguarding_case")\n',
            'def f(c):\n    return c.get_row(table_id="safeguarding_case", row_id="c")\n',
            'def f(c):\n    return c.list_rows("safeguarding_case")\n',
            'T = "safeguarding_case"\ndef f(r):\n    return r.get(T, "c")\n',
            'def f(r):\n    return r.get("safeguarding_action", "c")\n',
            'def f(audited):\n    return audited.get("safeguarding_case", "c")\n',
        ],
    )
    def test_a_module_reading_case_rows_outside_the_helper_fails(self, tmp_path, code):
        assert len(self.scan(tmp_path, code)) == 1

    def test_the_helper_call_and_other_tables_pass(self, tmp_path):
        code = """
        from hs_core.audit import read_audited
        def f(audited, ctx):
            read_audited(audited, ctx, "safeguarding_case", "c1")
            return audited.get("case_status", "c1")
        """
        assert self.scan(tmp_path, code) == []

    def test_computed_names_are_a_documented_miss(self, tmp_path):
        assert self.scan(tmp_path, 'def f(r, t):\n    return r.get(t, "c")\n') == []
