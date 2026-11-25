"""The retention policy covers every schema table and agrees with the schema (C03)."""

import json
import sys
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from hs_core.append_only import APPEND_ONLY_TABLES
from hs_core.permissions.rules import FILE_BUCKETS
from hs_core.retention import render
from hs_core.retention.periods import Period
from hs_core.retention.policy import (
    APPEND_ONLY,
    DERIVED_KEYS,
    OWN_SUBJECT,
    POLICY,
    SOFT_TABLES,
    SUBJECT_COLUMNS,
    Action,
    OnParentPurge,
    PolicyError,
    effective_policy,
    parse_overrides,
    purge_order,
    undecided_tables,
    unknown_rule_tables,
)
from hs_core.retention.purger import quarantine_patch
from hs_core.settings import Settings

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "appwrite"))
import schema_build  # noqa: E402

DOMAINS = [json.loads(f.read_text()) for f in sorted((ROOT / "appwrite" / "schema").glob("*.json"))]
TABLES = {t["$id"]: t for d in DOMAINS for t in d["tables"]}
META = {k: v for d in DOMAINS for k, v in d["meta"].items()}
RELATIONS = {r["from"]: r["to"] for d in DOMAINS for r in d["relations"]}
COLS = {t: {c["key"]: c for c in tbl["columns"]} for t, tbl in TABLES.items()}


# ------------------------------------------------------------------ coverage
def test_every_schema_table_has_a_retention_decision():
    assert undecided_tables(set(TABLES)) == []
    assert unknown_rule_tables(set(TABLES)) == []


def test_coverage_check_fails_when_a_table_is_added_without_a_decision():
    assert undecided_tables(set(TABLES) | {"brand_new_table"}) == ["brand_new_table"]
    assert unknown_rule_tables(set(TABLES) - {"child"}) == ["child"]


def test_a_new_schema_file_with_a_new_table_would_be_caught(tmp_path):
    """The real check, end to end: a domain file that adds a table makes the coverage list non-empty."""
    extra = {
        "domain": "x",
        "tables": [{"$id": "fake_new_table", "columns": []}],
        "meta": {},
        "relations": [],
    }
    names = {t["$id"] for d in [*DOMAINS, extra] for t in d["tables"]}
    assert undecided_tables(names) == ["fake_new_table"]


def test_policy_has_no_duplicate_rules_and_every_class_is_titled():
    assert {r.data_class for r in POLICY.values()} <= set(render.CLASS_TITLES)


# ------------------------------------------------------------------ agreement with the schema
@pytest.mark.parametrize("table", sorted(POLICY))
def test_rule_agrees_with_the_schema(table):
    rule, cols = POLICY[table], COLS[table]
    if rule.clock:
        assert rule.clock in cols, f"{table}: clock column {rule.clock} missing"
        c = cols[rule.clock]
        assert c["type"] == "datetime" or (c["type"] == "varchar" and c.get("size") == 10), (
            table,
            c,
        )
    for col, kind in rule.subjects.items():
        assert col in cols and SUBJECT_COLUMNS[col] == kind
    # every subject-like column of the table must be declared (a forgotten one would escape holds)
    declared = set(rule.subjects)
    present = {c for c in cols if c in SUBJECT_COLUMNS}
    assert present <= declared | {link.column for link in rule.links}, (
        table, present - declared,
    )  # fmt: skip
    for link in rule.links:
        assert link.column in cols, (table, link.column)
        assert link.parent in TABLES, (table, link.parent)
        assert RELATIONS.get(f"{table}.{link.column}") == link.parent, (table, link)
    for col, values in rule.where.items():
        assert col in cols
        if cols[col]["type"] == "string":
            assert set(values) <= set(cols[col]["elements"]), (table, col, values)
    for kind, mapping in rule.anonymise.items():
        assert kind == "*" or kind in SUBJECT_COLUMNS.values()
        for col in mapping:
            assert col in cols, (table, col)
    if rule.marker:
        col, prefix = rule.marker
        assert cols[col]["required"], f"{table}: marker {col} must be required (null never matches)"
        assert any(col in m for m in rule.anonymise.values())
    for f in rule.files:
        owner, fcols = FILE_BUCKETS[f.bucket]
        assert owner == table and f.column in fcols


def test_every_schema_table_with_files_has_its_files_declared():
    declared = {(r.table, f.bucket, f.column) for r in POLICY.values() for f in r.files}
    expected = {(t, b, c) for b, (t, cols) in FILE_BUCKETS.items() for c in cols}
    assert declared == expected


def test_the_policy_lists_file_columns_of_buckets_only_in_tables_that_are_purged():
    for r in POLICY.values():
        if r.files:
            assert r.action is Action.HARD_DELETE


def test_soft_tables_and_derived_keys_match_the_schema():
    soft = {t for t, m in META.items() if m["deletion"] == "soft"}
    assert SOFT_TABLES == soft
    derived = {
        t: tuple(sorted({c for i in TABLES[t]["indexes"] if i["type"] == "unique" for c in i["columns"] if c.endswith("_key")}))
        for t in soft
    }  # fmt: skip
    assert {t: tuple(sorted(v)) for t, v in DERIVED_KEYS.items()} == {
        t: v for t, v in derived.items() if v
    }


@pytest.mark.parametrize("table", sorted(SOFT_TABLES))
def test_quarantine_patch_is_exactly_m02_soft_delete_patch(table):
    now = datetime(2027, 1, 1, 9, 0, tzinfo=UTC)
    assert quarantine_patch(table, now) == schema_build.soft_delete_patch(
        TABLES[table], now.isoformat()
    )


def test_append_only_sets_agree_with_the_schema_and_the_wrapper():
    schema_ao = {t for t, m in META.items() if m.get("append_only")}
    assert APPEND_ONLY == schema_ao == set(APPEND_ONLY_TABLES)


def test_tombstones_fit_their_columns():
    for r in POLICY.values():
        for mapping in r.anonymise.values():
            for col, tomb in mapping.items():
                c = COLS[r.table][col]
                if tomb is None:
                    continue
                size = c.get("size", 10**6)
                assert len(tomb if tomb != "@pseudonym" else "anon-" + "0" * 30) <= size, (
                    r.table,
                    col,
                )


# ------------------------------------------------------------------ the decisions
def test_money_is_kept_per_tax_law_never_hard_deleted_and_personal_fields_minimised():
    for t in ("payment", "invoice", "earning", "payout"):
        r = POLICY[t]
        assert (
            r.action is Action.ANONYMISE and r.floor == Period(years=7) and r.basis.value == "legal"
        )
        assert r.dsr.value == "anonymise"
    assert {"raw_callback", "failure_reason"} <= set(POLICY["payment"].anonymise["*"])
    for t in ("ledger_entry", "ledger_transaction", "ledger_account"):
        assert POLICY[t].never_purge and POLICY[t].action is Action.KEEP


def test_audit_and_consent_are_kept_and_safeguarding_is_longer_than_everything_else():
    assert POLICY["audit_log"].action is Action.KEEP
    assert POLICY["consent_record"].action is Action.KEEP
    assert POLICY["policy_acceptance"].action is Action.KEEP
    longest = max(
        r.period.approx_days for r in POLICY.values() if r.period and r.table != "safeguarding_case"
    )
    assert POLICY["safeguarding_case"].period.approx_days > longest
    assert POLICY["safeguarding_case"].where == {"status": ("closed",)}


def test_every_default_period_is_a_marked_placeholder_unless_it_is_a_pure_mechanism():
    for r in POLICY.values():
        if r.period is not None and r.basis.value == "legal":
            assert r.pending, f"{r.table}: a legal period must be flagged as pending the lawyer"


def test_floors_are_not_longer_than_periods():
    for r in POLICY.values():
        if r.floor and r.period:
            assert not r.period.shorter_than(r.floor), r.table


def test_restrict_children_expire_no_later_than_their_parent():
    """A RESTRICT child that outlived its parent would block the parent's purge forever."""
    for r in POLICY.values():
        for link in r.links:
            parent = POLICY[link.parent]
            if link.on_parent_purge is OnParentPurge.RESTRICT and r.period and parent.period:
                assert not parent.period.shorter_than(r.period), (r.table, link.parent)


def test_purge_order_puts_children_before_parents():
    order = {t: i for i, t in enumerate(purge_order())}
    for r in POLICY.values():
        for link in r.links:
            if link.on_parent_purge in (OnParentPurge.RESTRICT, OnParentPurge.CASCADE):
                assert order[r.table] < order[link.parent], (r.table, link.parent)
    assert sorted(order) == sorted(POLICY)


def test_a_cycle_in_the_links_is_an_error():
    from hs_core.retention.policy import Link

    a = replace(POLICY["session_log"], links=(Link("child_id", "child"),))
    b = replace(POLICY["child"], links=(Link("parent_id", "session_log"),))
    with pytest.raises(PolicyError):
        purge_order({"session_log": a, "child": b})


def test_own_subject_tables_cover_every_subject_kind():
    assert set(OWN_SUBJECT.values()) == set(SUBJECT_COLUMNS.values())


# ------------------------------------------------------------------ configurable periods
def test_overrides_can_lengthen_a_period():
    pol = effective_policy(parse_overrides('{"child": "10y"}'))
    assert pol["child"].period == Period(years=10) and POLICY["child"].period == Period(years=3)


def test_overrides_below_the_legal_floor_are_refused():
    with pytest.raises(PolicyError, match="floor"):
        effective_policy(parse_overrides('{"payment": "6y"}'))
    effective_policy(parse_overrides('{"payment": "7y"}'))


@pytest.mark.parametrize(
    "text",
    [
        '{"nope": "1y"}',
        '{"audit_log": "1y"}',
        '{"ledger_entry": "9y"}',
        '{"child": "soon"}',
        "[1]",
        "not json",
        '{"child": 3}',
    ],
)
def test_bad_overrides_are_configuration_errors(text):
    with pytest.raises(PolicyError):
        effective_policy(parse_overrides(text))


def test_settings_reject_bad_retention_configuration_and_default_to_dry_run():
    s = Settings(_env_file=None)
    assert s.retention_enforce is False and s.retention_blast_override is False
    assert s.retention_allow_append_only_purge is False and s.problems() == []
    for env in ("staging", "production"):
        assert Settings(_env_file=None, hs_env=env).retention_enforce is False
    assert any(
        "RETENTION_PERIODS" in p
        for p in Settings(_env_file=None, retention_periods='{"payment":"1y"}').problems()
    )
    assert any(
        "RETENTION_MAX_DELETE_PCT" in p
        for p in Settings(_env_file=None, retention_max_delete_pct=0).problems()
    )
    assert any(
        "RETENTION_QUARANTINE_DAYS" in p
        for p in Settings(_env_file=None, retention_quarantine_days=91).problems()
    )


# ------------------------------------------------------------------ the rendered schedule
def test_retention_schedule_doc_is_generated_and_not_stale():
    assert render.OUT == ROOT / "docs" / "legal" / "retention-schedule.md"
    assert render.OUT.read_text() == render.render(), "run: python -m hs_core.retention.render"


def test_schedule_lists_every_table_and_says_placeholder():
    text = render.render()
    for t in TABLES:
        assert f"| `{t}` |" in text
    assert "DRAFT for the lawyer" in text and "placeholder" in text
    assert "RETENTION_ENFORCE" in text


def test_changing_a_period_changes_the_rendered_document():
    assert render.render(effective_policy({"child": Period(years=9)})) != render.render()


def test_retention_doc_exists_with_the_acceptance_content():
    doc = (ROOT / "docs" / "legal" / "retention.md").read_text()
    for word in (
        "retention-schedule.md",
        "RETENTION_ENFORCE",
        "legal hold",
        "payment",
        "pending lawyer",
    ):
        assert word in doc
