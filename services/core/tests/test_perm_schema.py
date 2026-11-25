"""Schema-level guarantees (issue #40 acceptance criteria 1 and 5) and the generated docs."""

import ast
import json
from pathlib import Path

import pytest

from hs_core.permissions import matrixdoc
from hs_core.permissions.fake import PermissionDenied, Principal
from hs_core.permissions.rules import (
    CLIENT_WRITABLE_TABLES,
    FILE_BUCKETS,
    SPECS,
    WRITE_PROTECTED,
)
from hs_core.permissions.strings import forbidden_in
from perm_world import World

ROOT = Path(__file__).resolve().parents[3]
DOMAINS = [json.loads(f.read_text()) for f in sorted((ROOT / "appwrite" / "schema").glob("*.json"))]
TABLES = {t["$id"]: t for d in DOMAINS for t in d["tables"]}
CONFIG = json.loads((ROOT / "appwrite" / "appwrite.config.json").read_text())


def test_row_security_on_every_table_and_no_table_level_permissions():
    for name, t in TABLES.items():
        assert t["rowSecurity"] is True, name
        assert t["$permissions"] == [], name  # default deny: access only from row permissions
    for t in CONFIG["tables"]:
        assert t["rowSecurity"] is True and t["$permissions"] == [], t["$id"]


def test_file_security_on_every_bucket_and_no_bucket_permissions():
    assert {b["$id"] for b in CONFIG["buckets"]} == set(FILE_BUCKETS)
    for b in CONFIG["buckets"]:
        assert b["fileSecurity"] is True and b["$permissions"] == [], b["$id"]
        assert b["encryption"] is True and b["antivirus"] is True, b["$id"]


def test_no_table_has_any_client_create_update_or_delete_anywhere():
    assert CLIENT_WRITABLE_TABLES == frozenset()
    w = World().seed()
    for t in TABLES:
        for r in w.repo.list(t):
            perms = r["$permissions"]
            assert not [p for p in perms if not p.startswith("read(")], (t, r["$id"], perms)
            assert not forbidden_in(perms, allow_users_read=True), (t, r["$id"])


def test_no_any_or_users_permission_on_user_data_tables_only_published_catalogue_has_users():
    w = World().seed()
    catalogue = {
        "curriculum",
        "catalogue_version",
        "level",
        "subject",
        "strand",
        "topic",
        "struggle_tag",
    }
    for t in TABLES:
        for r in w.repo.list(t):
            assert 'read("any")' not in r["$permissions"], t
            if 'read("users")' in r["$permissions"]:
                assert t in catalogue, t


@pytest.mark.parametrize("what,tables", sorted(WRITE_PROTECTED.items()))
def test_clients_cannot_write_money_tier_earning_payout_session_status_or_checkin_validity(
    what, tables
):
    w = World().seed()
    everyone = [
        Principal.of("uPA", ["parent"]),
        Principal.of("uGA", ["guardian"]),
        Principal.of("uT1", ["teacher"]),
        Principal.of("uOps", ["opsadmin"]),
        Principal.of("uCur", ["curator"]),
        Principal.of("uL1", ["safeguardinglead"]),
        Principal.of("uSup", ["superadmin"]),
        Principal.of(
            "uAll",
            [
                "parent",
                "guardian",
                "teacher",
                "opsadmin",
                "curator",
                "safeguardinglead",
                "superadmin",
            ],
        ),
    ]
    for table in tables:
        assert table in TABLES, table
        row = w.repo.list(table)[0]["$id"]
        for p in everyone:
            view = w.repo.as_principal(p)
            with pytest.raises(PermissionDenied):
                view.create(table, "x1", {"amount": 1})
            with pytest.raises(PermissionDenied):
                view.update(table, row, {"amount": 999_999, "status": "confirmed", "tier_id": "t"})
            with pytest.raises(PermissionDenied):
                view.delete(table, row)


def test_the_protected_list_names_real_tables_and_covers_the_ticket_wording():
    assert set(WRITE_PROTECTED) >= {
        "money",
        "tier",
        "earning",
        "payout",
        "session status",
        "check-in validity",
    }
    for tables in WRITE_PROTECTED.values():
        assert set(tables) <= set(TABLES)


def test_a_write_permission_smuggled_into_a_row_is_flagged_but_the_fake_would_honour_it():
    """Why the audit matters: a row that carries update("user:x") is writable by x."""
    w = World().seed()
    w.repo.update("invoice", "invA", {}, permissions=['read("user:uPA")', 'update("user:uPA")'])
    view = w.as_("uPA")
    view.update("invoice", "invA", {"amount": 1})  # allowed by the (bad) row permission
    assert forbidden_in(w.repo.get("invoice", "invA")["$permissions"]) == ['update("user:uPA")']


def test_the_permission_code_cannot_emit_a_non_read_permission():
    """No string literal in the permission package names a write action or `any`, outside the
    parser's own table of actions (strings.py)."""
    src = Path(__file__).resolve().parents[1] / "src" / "hs_core" / "permissions"
    offenders = []
    for f in src.glob("*.py"):
        if f.name in ("strings.py", "fake.py"):
            continue
        for node in ast.walk(ast.parse(f.read_text())):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                v = node.value
                if v in ("any", "guests") or v.startswith(
                    ("create(", "update(", "delete(", "write(")
                ):
                    offenders.append((f.name, v))
    assert offenders == []


def test_every_spec_documents_who_reads_it_and_service_only_tables_say_why():
    for name, s in SPECS.items():
        if s.service_only:
            assert s.note, name
        else:
            assert s.audience, name


# ------------------------------------------------------------------ the generated documentation
def test_the_matrix_in_the_docs_is_generated_from_the_code():
    assert matrixdoc.doc_is_current(), (
        "docs/ops/permissions.md is stale: run scripts/permissions_check.py --write-docs"
    )


def test_the_docs_matrix_lists_every_table_exactly_once():
    block = matrixdoc.render_matrix()
    for t in TABLES:
        rows = [
            ln
            for ln in block.splitlines()
            if ln.startswith(f"| `{t}` ") or ln.startswith(f"- `{t}`:")
        ]
        assert len(rows) == 1, (t, rows)


def test_changing_a_rule_makes_the_docs_check_fail(monkeypatch):
    import dataclasses

    old = SPECS["child"]
    monkeypatch.setitem(
        SPECS, "child", dataclasses.replace(old, audience={**old.audience, "teacher": "anything"})
    )
    assert not matrixdoc.doc_is_current()


def test_the_drift_script_reports_zero_differences_and_fails_on_drift(capsys, monkeypatch):
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "permissions_check",
        Path(__file__).resolve().parents[1] / "scripts" / "permissions_check.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    import io

    out = io.StringIO()
    assert mod.main([], out=out) == 0 and "zero differences" in out.getvalue()
    # inject drift into the seeded dataset: the script must fail
    real = mod.SampleWorld

    class Drifty(real):
        def seed(self):
            super().seed()
            self.repo.update("child", "b1", {}, permissions=['read("user:uT4")'])
            return self

    monkeypatch.setattr(mod, "SampleWorld", Drifty)
    out = io.StringIO()
    assert mod.main([], out=out) == 1 and "differ" in out.getvalue()
