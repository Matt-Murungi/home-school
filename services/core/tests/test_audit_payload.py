"""M06 (#41): what goes into audit_log.before/after: redaction, minimal diff, stable hash,
truncation at the text limit, unicode. Pure functions, no repository."""

import json
from pathlib import Path

import pytest

from hs_core.audit.payload import (
    MAX_SIDE_CHARS,
    REDACTED,
    _tokens,
    is_redacted,
    make_sides,
    normalize,
    redact_state,
    stable_hash,
)
from hs_core.audit.registry import AUDITED_TABLES, ENTITY_REDACT, PLANNED_TABLES, TEXT_ALLOWED

SCHEMA = Path(__file__).resolve().parents[3] / "appwrite" / "schema"

CANARIES = {
    "password": "hunter2-canary",
    "api_key": "key-canary-123",
    "access_token": "tok-canary-456",
    "otp": "482913",
    "code_hash": "c0ffee-canary",
    "phone": "+256700123456",
    "msisdn": "256700123456",
    "email": "kid.parent@example.com",
    "lat_e6": 323456,
    "lng_e6": 32123456,
    "address": "Plot 5 Kira Road",
    "directions": "blue gate behind the church",
    "answer_key": "B",
    "file_id": "file-canary-789",
    "raw_callback": '{"secret":"cb-canary"}',
    "name": "Amina Nakato",
    "child_name": "Brian Okello",
}


def _flat(text: str) -> str:
    return text if isinstance(text, str) else json.dumps(text)


class TestRedaction:
    @pytest.mark.parametrize("key", sorted(CANARIES))
    def test_sensitive_key_value_never_appears(self, key):
        row = {"$id": "r1", "status": "open", key: CANARIES[key]}
        out = redact_state("some_table", row)
        assert out[key] == REDACTED
        assert str(CANARIES[key]) not in json.dumps(out)
        assert out["status"] == "open"

    def test_ids_are_kept_not_masked_unlike_the_log_scrubber(self):
        row = {"session_id": "s1", "teacher_id": "t1", "location_id": "l1", "zone_id": "z1"}
        assert redact_state("session_checkin", row) == row

    def test_nested_and_list_values_are_redacted(self):
        row = {
            "detail": {"contact": {"phone": "+256700000001", "kept": 1}},
            "list": [{"token": "x"}],
        }
        out = redact_state("x", row)
        assert out["detail"]["contact"]["phone"] == REDACTED
        assert out["detail"]["contact"]["kept"] == 1
        assert out["list"][0]["token"] == REDACTED

    def test_key_variants_are_normalised(self):
        for k in ("Password", "API-KEY", "refreshToken", "Phone_Number", "latE6"):
            assert is_redacted("x", k), k

    def test_string_values_pass_through_the_f08_scrubber(self):
        row = {
            "resolution_note_x": "call +256700123456 or a@b.co, Bearer abcdefghijklmnop.qrstuvwx"
        }
        s = json.dumps(redact_state("x", row))
        assert "256700123456" not in s and "a@b.co" not in s and "abcdefghijklmnop" not in s

    def test_per_entity_rules(self):
        assert is_redacted("home_location", "directions")
        assert is_redacted("teacher_document", "file_id")
        assert is_redacted("safeguarding_case", "description")
        assert is_redacted("session_log", "note")
        assert not is_redacted("dispute", "status")
        assert not is_redacted("tier", "criteria")  # money rules must stay reviewable

    def test_dollar_keys_dropped_but_permissions_kept_sorted(self):
        row = {
            "$id": "1",
            "$createdAt": "t",
            "$updatedAt": "t",
            "$sequence": 4,
            "a": 1,
            "$permissions": ['read("user:b")', 'read("user:a")'],
        }
        assert normalize(row) == {"a": 1, "_permissions": ['read("user:a")', 'read("user:b")']}

    def test_rows_carry_ids_not_names(self):
        out = redact_state("child", {"id_x": "c1", "name": "Brian", "parent_id": "p1"})
        assert out["name"] == REDACTED and out["parent_id"] == "p1"

    def test_every_audited_table_text_column_is_redacted_or_allowlisted(self):
        """Schema-driven: a new free-text column on an audited table must be decided on."""
        import glob

        missing = []
        for f in glob.glob(str(SCHEMA / "*.json")):
            for t in json.load(open(f))["tables"]:
                if t["$id"] not in AUDITED_TABLES:
                    continue
                for c in t["columns"]:
                    big = c["type"] in ("text", "mediumtext", "longtext") or (
                        c["type"] == "varchar" and c.get("size", 0) >= 400
                    )
                    sens = bool(
                        set(_tokens(c["key"]))
                        & {"phone", "email", "lat", "lng", "address", "token", "secret", "password"}
                    )
                    if (big or sens) and not is_redacted(t["$id"], c["key"]):
                        if (t["$id"], c["key"]) not in TEXT_ALLOWED:
                            missing.append((t["$id"], c["key"]))
        assert not missing, f"decide redaction or allow-list for: {missing}"

    def test_allowlist_and_rules_reference_real_things(self):
        for table in ENTITY_REDACT:
            assert table in AUDITED_TABLES | PLANNED_TABLES, table
        for table, _col in TEXT_ALLOWED:
            assert table in AUDITED_TABLES


class TestDiff:
    def test_update_stores_only_changed_fields(self):
        b, a, bs, as_ = make_sides(
            "tier", {"name": "Gold", "rate": 100, "x": 1}, {"name": "Gold", "rate": 120, "x": 1}
        )
        assert json.loads(b)["fields"] == {"rate": 100}
        assert json.loads(a)["fields"] == {"rate": 120}
        assert bs != as_

    def test_create_has_no_before_and_all_fields_after(self):
        b, a, bs, _ = make_sides("tier", None, {"name": "Gold", "rate": 100, "$id": "t1"})
        assert b is None and bs == "none"
        assert json.loads(a)["fields"] == {"name": "Gold", "rate": 100}

    def test_delete_has_no_after_state(self):
        b, a, _, as_ = make_sides("tier", {"name": "Gold"}, None)
        assert json.loads(b)["fields"] == {"name": "Gold"}
        assert as_ == "none"

    def test_redacted_field_change_is_visible_as_changed_without_values(self):
        b, a, *_ = make_sides(
            "user_profile", {"phone": "+256700000001", "s": 1}, {"phone": "+256700000002", "s": 1}
        )
        assert json.loads(b)["fields"] == {"phone": REDACTED}
        assert json.loads(a)["fields"] == {"phone": REDACTED}
        assert "2567000000" not in (b + a)

    def test_no_change_gives_empty_fields(self):
        b, a, bs, as_ = make_sides("tier", {"a": 1}, {"a": 1})
        assert json.loads(b)["fields"] == {} and json.loads(a)["fields"] == {}
        assert bs == as_

    def test_hash_is_stable_across_key_order_and_dollar_keys(self):
        h1 = stable_hash({"a": 1, "b": [1, 2]})
        h2 = stable_hash({"b": [1, 2], "a": 1})
        assert h1 == h2 and len(h1) == 64
        assert stable_hash({"a": 2}) != h1
        _, a1, *_ = make_sides("t", None, {"$id": "1", "a": 1, "b": 2})
        _, a2, *_ = make_sides("t", None, {"b": 2, "a": 1, "$id": "2"})
        assert json.loads(a1)["hash"] == json.loads(a2)["hash"]

    def test_meta_is_embedded_in_after_only(self):
        b, a, *_ = make_sides("tier", {"a": 1}, {"a": 2}, meta={"status": "intent", "seq": 3})
        assert "meta" not in json.loads(b)
        assert json.loads(a)["meta"] == {"status": "intent", "seq": 3}


class TestTruncation:
    def test_huge_value_is_cut_marked_and_hash_covers_the_full_value(self):
        big = "x" * 100_000
        small_b, small_a, *_ = make_sides("tier", {"criteria": "a"}, {"criteria": big})
        a = json.loads(small_a)
        assert a["truncated"] is True and len(small_a) <= MAX_SIDE_CHARS
        full_hash = stable_hash({"criteria": big})
        assert a["hash"] == full_hash  # hash of the full (untruncated) changed-field value
        assert json.loads(small_b)["truncated"] is False

    def test_not_truncated_when_it_fits(self):
        _, a, *_ = make_sides("tier", None, {"criteria": "x" * 500})
        assert json.loads(a)["truncated"] is False

    def test_many_huge_fields_stay_valid_json_under_the_limit(self):
        row = {f"f{i}": "y" * 5000 for i in range(60)}
        _, a, *_ = make_sides("tier", None, row, meta={"status": "intent", "seq": 1})
        assert len(a) <= MAX_SIDE_CHARS
        d = json.loads(a)
        assert d["truncated"] is True and d["meta"]["seq"] == 1
        assert d["hash"] == stable_hash(row)

    def test_thousands_of_field_names_still_fit(self):
        row = {f"field_{i:05d}": "v" * 50 for i in range(5000)}
        _, a, *_ = make_sides("tier", None, row)
        assert len(a) <= MAX_SIDE_CHARS and json.loads(a)["truncated"] is True

    def test_limit_is_under_the_column_limit(self):
        assert MAX_SIDE_CHARS <= 16_000

    def test_unicode_roundtrips_and_is_counted_in_characters(self):
        value = "Naïve café 日本語 🚀" * 10
        _, a, *_ = make_sides("tier", None, {"label": value})
        assert json.loads(a)["fields"]["label"] == value
        assert "\\u" not in a  # not escaped, so the character budget is real

    def test_truncation_never_splits_a_character(self):
        row = {"label": "日本語🚀" * 20_000}
        _, a, *_ = make_sides("tier", None, row)
        json.loads(a)  # valid JSON: no half-cut escapes or surrogates
        assert len(a) <= MAX_SIDE_CHARS
        a.encode("utf-8")  # no lone surrogates

    def test_non_json_types_are_stringified(self):
        from datetime import UTC, datetime
        from decimal import Decimal

        _, a, *_ = make_sides(
            "tier", None, {"at": datetime(2027, 1, 1, tzinfo=UTC), "d": Decimal("1.5"), "b": b"xyz"}
        )
        f = json.loads(a)["fields"]
        assert f["at"].startswith("2027-01-01") and f["d"] == "1.5" and f["b"] == "[bytes:3]"
