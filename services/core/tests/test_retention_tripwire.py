"""Who may hard-delete, and the append-only exception path (C03).

M01-M05 rule: "the retention job is the ONLY allowed hard-deleter" of soft, immutable and
append-only rows. These tests are TRIPWIRES, not proof: they scan for literal and computed
delete call sites; see docs/ops/retention.md "What the tripwire cannot see"."""

import ast
import json
from dataclasses import replace
from pathlib import Path

import pytest

from hs_core.append_only import (
    APPEND_ONLY_TABLES,
    AppendOnlyRepository,
    AppendOnlyViolation,
    _string_constants,
    find_append_only_violations,
)
from hs_core.audit.tripwire import find_unaudited_writes
from hs_core.retention.periods import Period
from hs_core.retention.policy import POLICY, Action
from retention_helpers import RecRepo, ago, make_purger, purge_table

SRC = Path(__file__).resolve().parents[1] / "src" / "hs_core"
ROOT = Path(__file__).resolve().parents[3]
DOMAINS = [json.loads(f.read_text()) for f in sorted((ROOT / "appwrite" / "schema").glob("*.json"))]
HARD = {t for d in DOMAINS for t, m in d["meta"].items() if m["deletion"] == "hard"}

# files outside retention/ that may call `.delete(` at all, and why
ALLOWED_DELETE_FILES = {
    "repository.py": "the Repository adapters themselves",
    "audit/repository.py": "AuditedRepository: pass-through, audited per row",
    "permissions/fake.py": "test double",
    "auth_otp/jobs.py": "otp-cleanup: otp_challenge is a hard table",
    "auth_otp/store.py": "otp_challenge is a hard table",
}


def delete_calls(root: Path):
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text())
        consts = _string_constants(tree)
        for n in ast.walk(tree):
            if (
                isinstance(n, ast.Call)
                and isinstance(n.func, ast.Attribute)
                and n.func.attr in ("delete", "delete_row", "delete_rows")
            ):
                first = n.args[0] if n.args else None
                name = None
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    name = first.value
                elif isinstance(first, ast.Name):
                    name = consts.get(first.id)
                yield path.relative_to(SRC).as_posix(), n.lineno, name, ast.unparse(n.func.value)


def test_no_code_outside_the_allowlist_deletes_rows():
    found = {
        f
        for f, _, _, recv in delete_calls(SRC)
        if not f.startswith("retention/") and recv != "super()" and not f.endswith("append_only.py")
    }
    assert found <= set(ALLOWED_DELETE_FILES), sorted(found - set(ALLOWED_DELETE_FILES))


def test_literal_deletes_outside_retention_only_name_hard_tables():
    for f, line, name, _ in delete_calls(SRC):
        if f.startswith("retention/") or name is None:
            continue
        assert name in HARD, f"{f}:{line} deletes from {name}, which is not a hard-delete table"


def test_inside_the_retention_package_exactly_one_place_deletes_table_rows():
    sites = [
        (f, line) for f, line, _, recv in delete_calls(SRC / "retention") if "files" not in recv
    ]
    assert [f for f, _ in sites] == ["retention/purger.py"], sites  # RetentionPurger.erase_row


def test_the_m04_and_m06_tripwires_still_pass_over_the_whole_source():
    assert find_append_only_violations(SRC) == []
    assert find_unaudited_writes(SRC) == []


def test_tripwire_honesty_it_does_not_see_the_purgers_own_computed_delete(tmp_path):
    """The purger deletes with a table name taken from the policy: the AST tripwire cannot see
    that, and that is documented. A literal delete of a ledger table IS seen."""
    bad = tmp_path / "bad.py"
    bad.write_text('def f(repo):\n    repo.delete("ledger_entry", "x")\n')
    assert find_append_only_violations(tmp_path)
    computed = tmp_path / "bad.py"
    computed.write_text("def f(repo, table):\n    repo.delete(table, 'x')\n")
    assert find_append_only_violations(tmp_path) == []  # missed on purpose: documented limit
    doc = (ROOT / "docs" / "ops" / "retention.md").read_text()
    assert "What the tripwire cannot see" in doc and "exception path" in doc


# ------------------------------------------------------------------ the append-only wrapper is untouched
@pytest.mark.parametrize("table", sorted(APPEND_ONLY_TABLES))
def test_the_append_only_wrapper_still_refuses_delete_for_everyone_including_retention(table):
    w = AppendOnlyRepository(RecRepo())
    with pytest.raises(AppendOnlyViolation):
        w.delete(table, "x")
    with pytest.raises(AppendOnlyViolation):
        w.delete_rows(table)


def test_the_purger_never_receives_the_wrapper():
    import inspect

    from hs_core.retention import purger, wiring

    assert "AppendOnlyRepository" not in inspect.getsource(purger)
    assert "AppendOnlyRepository" not in inspect.getsource(wiring)


# ------------------------------------------------------------------ the exception path
def case(repo, acted_at, closed=15000):
    repo.create("safeguarding_case", "c", {"child_id": "ch", "raised_by_id": "u", "status": "closed", "description": "x", "category": "o", "raised_at": ago(closed), "closed_at": ago(closed)})  # fmt: skip
    repo.create("safeguarding_action", "a1", {"case_id": "c", "action": "note", "by_id": "u", "acted_at": acted_at})  # fmt: skip
    repo.create("case_status", "cs", {"case_id": "c", "reporter_id": "u", "status": "closed", "raised_at": ago(closed)})  # fmt: skip


def test_default_nothing_append_only_is_ever_purged_and_the_case_waits():
    repo = RecRepo()
    case(repo, ago(15000))
    st = purge_table(make_purger(repo), "safeguarding_case")
    assert set(repo.rows("safeguarding_action")) == {"a1"} and "c" in repo.rows("safeguarding_case")
    assert st["blocked_reasons"] == {"append_only": 1}


def test_with_the_owner_switch_an_old_case_goes_with_its_actions_audited_by_count():
    repo = RecRepo()
    case(repo, ago(15000))
    st = purge_table(make_purger(repo, allow_append_only=True), "safeguarding_case")
    assert repo.rows("safeguarding_action") == {} and repo.rows("safeguarding_case") == {}
    assert repo.rows("case_status") == {}
    assert st["append_only_purged"] == 1 and st["deleted"] == 1


def test_even_with_the_switch_an_action_younger_than_the_floor_keeps_the_whole_case():
    repo = RecRepo()
    case(repo, ago(100))  # an action added recently: the case is not purgeable yet
    purge_table(make_purger(repo, allow_append_only=True), "safeguarding_case")
    assert "a1" in repo.rows("safeguarding_action") and "c" in repo.rows("safeguarding_case")


def test_a_hold_beats_the_exception_path():
    repo = RecRepo()
    case(repo, ago(15000))
    repo.create("retention_hold", "h", {"subject_kind": "case", "subject_id": "c", "reason": "regulator", "placed_by_id": "s", "placed_at": ago(1)})  # fmt: skip
    purge_table(make_purger(repo, allow_append_only=True), "safeguarding_case")
    assert "a1" in repo.rows("safeguarding_action") and "c" in repo.rows("safeguarding_case")


def test_audit_log_has_no_age_rule_so_it_is_never_purged_a_lawyer_period_needs_the_switch_and_the_floor():
    assert POLICY["audit_log"].action is Action.KEEP and POLICY["audit_log"].period is None
    pol = dict(POLICY)
    pol["audit_log"] = replace(pol["audit_log"], action=Action.HARD_DELETE, period=Period(years=12), clock="occurred_at")  # fmt: skip
    repo = RecRepo()
    for rid, age in (("a", 4000), ("b", 2000), ("c", 500)):  # 10.9y, 5.5y, 1.4y
        repo.create("audit_log", rid, {"action": "x", "entity": "e", "occurred_at": ago(age)})
    off = make_purger(repo, policy=pol)
    assert off.skip_reason(pol["audit_log"]) == "append_only"
    with pytest.raises(Exception, match="not purged by age"):
        purge_table(off, "audit_log")
    on = make_purger(repo, policy=pol, allow_append_only=True)
    assert purge_table(on, "audit_log")["deleted"] == 0  # 12 years: nothing due yet
    pol["audit_log"] = replace(pol["audit_log"], period=Period(years=4))  # 4y: a and b are due...
    st = purge_table(make_purger(repo, policy=pol, allow_append_only=True), "audit_log")
    # ...but the 7 year floor is checked per row: b (5.5y) stays
    assert set(repo.rows("audit_log")) == {"b", "c"} and st["append_only_purged"] == 1


def test_ledger_tables_cannot_be_purged_even_with_a_hand_made_policy_and_the_switch():
    repo = RecRepo()
    for t in ("ledger_entry", "ledger_transaction"):
        repo.create(t, "x", {"posted_at": "2001-01-01T00:00:00+00:00"})
    pol = dict(POLICY)
    for t in ("ledger_entry", "ledger_transaction"):
        pol[t] = replace(pol[t], never_purge=False, action=Action.HARD_DELETE, period=Period(years=7), clock="posted_at")  # fmt: skip
        p = make_purger(repo, policy=pol, allow_append_only=True)
        st = purge_table(p, t)
        assert "x" in repo.rows(t) and st["blocked"] == 1


def test_the_exception_path_is_narrow_its_documentation_lists_every_lock():
    doc = (ROOT / "docs" / "ops" / "retention.md").read_text()
    for lock in ("RETENTION_ALLOW_APPEND_ONLY_PURGE", "never_purge", "floor", "per row", "hold"):
        assert lock in doc


def test_the_case_read_exemption_is_one_file_and_it_reads_status_columns_only():
    from hs_core.audit.tripwire import CASE_READ_EXEMPT, find_unaudited_case_reads

    assert CASE_READ_EXEMPT == {"retention/holds.py"}
    src = (SRC / "retention" / "holds.py").read_text()
    assert 'Query.select(["$id", "child_id", "teacher_id", "status"])' in src
    for text in ("description", "outcome", "narrative"):
        assert text not in src.split("def _open_cases")[1]
    assert find_unaudited_case_reads(SRC) == []
