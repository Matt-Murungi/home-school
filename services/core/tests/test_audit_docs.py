"""M06 (#41): docs/ops/audit.md must describe the protocol and stay honest about the limits
(lessons of M04 review rounds 1-3: no overclaiming)."""

import re
from pathlib import Path

import pytest

from hs_core.audit.registry import AUDITED_TABLES, ENTITY_REDACT, REASON_REQUIRED

ROOT = Path(__file__).resolve().parents[3]
DOC = ROOT / "docs" / "ops" / "audit.md"
SRC = Path(__file__).resolve().parents[1] / "src" / "hs_core" / "audit"


@pytest.fixture(scope="module")
def doc():
    return DOC.read_text()


def test_doc_exists_with_the_required_sections(doc):
    for heading in (
        "## Protocol", "## Crash recovery", "## What is audited", "## Redaction",
        "## Reasons", "## Reads of safeguarding cases", "## Read side", "## Limits",
        "## Tripwires",
    ):  # fmt: skip
        assert heading in doc, heading


def test_protocol_choice_is_stated_and_justified(doc):
    low = doc.lower()
    assert "audit-first" in low and "intent" in low and "not_applied" in low
    assert "why" in low and "business-then-audit" in low


def test_tripwires_are_labelled_honestly_and_list_what_they_miss(doc):
    low = doc.lower()
    assert "tripwire" in low and "literal" in low
    for miss in ("computed", "parameter", "getattr", "alias", "splat"):
        assert miss in low, miss
    assert "opt-in" in low or "convention" in low


def test_no_overclaiming_words(doc):
    for bad in ("only way", "guarantee", "impossible", "cannot be bypassed", "tamper-proof"):
        assert bad not in doc.lower(), bad
    for f in SRC.glob("*.py"):
        text = f.read_text().lower()
        for bad in ("only way", "guarantees", "tamper-proof"):
            assert bad not in text, (f.name, bad)


def test_documents_the_console_bypass_and_concurrency_limits(doc):
    low = doc.lower()
    assert "console" in low and "super admin" in low and "reviewed" in low
    assert "sequence" in low and ("more than one" in low or "multiple instances" in low)
    assert "unverifiable" in low


def test_every_reason_required_action_audited_table_and_redaction_is_listed(doc):
    for action in REASON_REQUIRED:
        assert f"`{action}`" in doc, action
    for table in AUDITED_TABLES:
        assert f"`{table}`" in doc, table
    for table in ENTITY_REDACT:
        assert f"`{table}`" in doc, table


def test_settings_and_job_are_documented(doc):
    for name in ("AUDIT_LEAD_BLANKET_READ", "AUDIT_RECOVER_LOOKBACK_HOURS",
                 "AUDIT_RECOVER_GRACE_S", "audit-recover"):  # fmt: skip
        assert name in doc, name


def test_answer_keys_decision_is_recorded(doc):
    assert re.search(r"answer.?key", doc, re.I)


def test_env_example_and_inventory_know_the_new_settings():
    env = (Path(__file__).resolve().parents[1] / ".env.example").read_text()
    inv = (ROOT / "docs" / "ops" / "secrets-inventory.json").read_text()
    for name in (
        "AUDIT_LEAD_BLANKET_READ",
        "AUDIT_RECOVER_LOOKBACK_HOURS",
        "AUDIT_RECOVER_GRACE_S",
    ):
        assert f"{name}=" in env and f'"{name}"' in inv, name


def test_round2_decisions_are_documented(doc):
    low = doc.lower()
    for needle in (
        "keys and collisions", "key_collision", "ambiguous", "notwritten", "min_reason_chars",
        "reason_truncated", "admin-ui events", "/v1/admin/audit/event", "unwiredauditsink",
        "ledgeraudit", "templateaudit", "tables that are not audited", "child", "consent_record",
        "audit.read", "unverifiable_intent", "cancel_reason", "end_reason", "earning.note",
    ):  # fmt: skip
        assert needle in low, needle
    assert "required before staging" in low
