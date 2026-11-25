"""M04 (#39): append-only tables are insert-only in the core service.

ledger_entry, ledger_transaction, audit_log and safeguarding_action: AppendOnlyRepository
exposes create/get/list; update/delete raise. A CI-style scan fails if any code path in
src/hs_core calls update/delete on these tables, and the append-only set equals the
tables marked append_only in the schema.
"""

import ast
import json
import re
import textwrap
from pathlib import Path

import pytest

from hs_core.append_only import (
    APPEND_ONLY_TABLES,
    WRITE_METHODS,
    AppendOnlyRepository,
    AppendOnlyViolation,
    find_append_only_violations,
)
from hs_core.repository import Conflict, InMemoryRepository, NotFound

SRC = Path(__file__).resolve().parents[1] / "src" / "hs_core"
SCHEMA = Path(__file__).resolve().parents[3] / "appwrite" / "schema"


@pytest.fixture
def guarded():
    inner = InMemoryRepository()
    return AppendOnlyRepository(inner), inner


def test_insert_get_list_work(guarded):
    g, _ = guarded
    row = g.create("ledger_entry", "e1", {"amount": 5000}, permissions=['read("user:a")'])
    assert row["$id"] == "e1"
    assert g.get("ledger_entry", "e1")["amount"] == 5000
    assert [r["$id"] for r in g.list("ledger_entry")] == ["e1"]


@pytest.mark.parametrize("table", sorted(APPEND_ONLY_TABLES))
def test_update_and_delete_raise_and_leave_row_unchanged(guarded, table):
    g, inner = guarded
    g.create(table, "r1", {"amount": 1})
    with pytest.raises(AppendOnlyViolation):
        g.update(table, "r1", {"amount": 2})
    with pytest.raises(AppendOnlyViolation):
        g.delete(table, "r1")
    assert inner.get(table, "r1")["amount"] == 1


def test_duplicate_create_is_still_a_conflict(guarded):
    g, _ = guarded
    g.create("audit_log", "a1", {})
    with pytest.raises(Conflict):
        g.create("audit_log", "a1", {})


def test_other_tables_are_refused(guarded):
    g, _ = guarded
    with pytest.raises(ValueError):
        g.create("child", "c1", {})
    with pytest.raises(ValueError):
        g.get("child", "c1")


def test_missing_row_is_not_found(guarded):
    g, _ = guarded
    with pytest.raises(NotFound):
        g.get("ledger_entry", "nope")


def test_append_only_set_matches_schema_meta():
    marked = set()
    for p in SCHEMA.glob("*.json"):
        d = json.loads(p.read_text())
        for table, m in d.get("meta", {}).items():
            if m.get("append_only"):
                assert m["deletion"] == "immutable", table
                marked.add(table)
    assert marked == set(APPEND_ONLY_TABLES)
    assert {"ledger_entry", "audit_log", "safeguarding_action"} <= marked


def test_no_service_code_updates_or_deletes_append_only_tables():
    assert find_append_only_violations(SRC) == []


@pytest.mark.parametrize(
    "line",
    [
        'repo.update("ledger_entry", "x", {"amount": 1})',
        'repo.delete("audit_log", "x")',
        'repo.update(table="safeguarding_action", row_id="x", data={})',
        'self._tables.update_row(database_id="d", table_id="ledger_entry", row_id="x", data={})',
        'T = "ledger_transaction"\nrepo.delete(T, "x")',
        'T: str = "ledger_entry"\nrepo.update(T, "x", {})',
        'repo.upsert_row("ledger_entry", "x", {})',
        'repo.update_rows("audit_log", {})',
        'repo.delete_rows(table_id="audit_log")',
        'repo.upsert_rows("safeguarding_action", [])',
        'repo.increment_row_column(table_id="ledger_entry", row_id="x", column="amount")',
        'repo.decrement_row_column("ledger_entry", "x", "amount")',
        'tables.create_operations(table="ledger_entry")',
    ],
)
def test_mutation_scan_catches_a_code_path_that_updates_a_ledger_table(tmp_path, line):
    (tmp_path / "bad.py").write_text(textwrap.dedent(line) + "\n")
    found = find_append_only_violations(tmp_path)
    assert found and "bad.py" in found[0]


def test_scan_ignores_other_tables_and_reads(tmp_path):
    (tmp_path / "ok.py").write_text(
        'repo.update("child", "x", {})\nrepo.get("ledger_entry", "x")\n'
        'repo.create("ledger_entry", "y", {})\n'
    )
    assert find_append_only_violations(tmp_path) == []


@pytest.mark.parametrize(
    "name",
    ["update_row", "update_rows", "delete_row", "delete_rows", "upsert_row", "upsert_rows"]
    + ["increment_row_column", "decrement_row_column", "create_operations"],
)
def test_write_method_list_covers_sdk_write_style_methods(name):
    assert name in WRITE_METHODS


def test_every_sdk_write_method_is_in_the_list():
    from appwrite.services.tables_db import TablesDB

    sdk = {
        m
        for m in dir(TablesDB)
        if re.match(r"(update|delete|upsert|increment|decrement)_(row|rows)", m)
        or m == "create_operations"
    }
    assert sdk and sdk <= WRITE_METHODS, sdk - WRITE_METHODS


@pytest.mark.parametrize(
    "method",
    [
        "update_rows",
        "delete_rows",
        "upsert_row",
        "upsert_rows",
        "increment_row_column",
        "decrement_row_column",
    ],
)
def test_wrapper_refuses_bulk_and_upsert_variants_at_runtime(guarded, method):
    g, inner = guarded
    g.create("ledger_entry", "r1", {"amount": 1})
    with pytest.raises(AppendOnlyViolation):
        getattr(g, method)("ledger_entry", {"amount": 2})
    assert inner.get("ledger_entry", "r1")["amount"] == 1


def test_docs_say_tripwire_and_opt_in_and_never_claim_only_way():
    readme = (SRC.parents[3] / "appwrite" / "README.md").read_text()
    doc = (SRC / "append_only.py").read_text()
    for text in (readme, doc):
        low = text.lower()
        assert "tripwire" in low and "opt-in" in low
        assert "only way" not in low
    for text in (readme, doc):
        low = text.lower()
        assert "used by the ledger service" in low and "opt-in elsewhere" in low
        assert "nothing uses it yet" not in low


def test_raw_sdk_row_writes_exist_only_in_repository_module():
    """Raw SDK write-style calls are only made from repository.py (the generic adapter)."""
    offenders = []
    for p in SRC.rglob("*.py"):
        if p.name == "repository.py":
            continue
        for n in ast.walk(ast.parse(p.read_text())):
            if isinstance(n, ast.Attribute) and n.attr in WRITE_METHODS - {"update", "delete"}:
                offenders.append(str(p))
    assert offenders == []


def test_docs_list_the_extra_scan_gaps_and_function_local_constants():
    readme = (SRC.parents[3] / "appwrite" / "README.md").read_text().lower()
    doc = (SRC / "append_only.py").read_text().lower()
    for text in (readme, doc):
        assert "reassigned" in text and "two scopes" in text and "per file" in text
        assert "module-level string constant" not in text
        assert "function-local" in text


def test_scan_catches_function_local_constants(tmp_path):
    (tmp_path / "f.py").write_text('def f(repo):\n    t = "audit_log"\n    repo.delete(t, "x")\n')
    assert find_append_only_violations(tmp_path)


def test_reassigned_constant_hides_the_earlier_value(tmp_path):
    (tmp_path / "g.py").write_text('T = "audit_log"\nT = "child"\nrepo.delete(T, "x")\n')
    assert find_append_only_violations(tmp_path) == []  # documented miss
