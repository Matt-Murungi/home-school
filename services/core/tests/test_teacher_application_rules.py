"""V01 (#56): server validation rules (mirrors the Dart rules via a shared fixture)."""

import json
from datetime import date
from pathlib import Path

import pytest

from application_helpers import deep, valid_content
from hs_core.teacher_application.ports import default_options
from hs_core.teacher_application.rules import (
    Context,
    clean_text,
    is_peak,
    normalise_content,
    validate_content,
)

ROOT = Path(__file__).resolve().parents[3]
FIXTURE = json.loads((ROOT / "apps/teacher/test/fixtures/application_cases.json").read_text())


def ctx(**kw):
    base = dict(
        today=date.fromisoformat(FIXTURE["today"]),
        catalogue={o.curriculum_id: o for o in default_options()},
        zone_ids=frozenset(FIXTURE["zone_ids"]),
        own_phone=FIXTURE["own_phone"],
        min_weekly_minutes=FIXTURE["min_weekly_hours"] * 60,
    )
    return Context(**{**base, **kw})


def merged(base, patch):
    out = deep(base)
    for k, v in patch.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = merged(out[k], v)
        else:
            out[k] = v
    return out


@pytest.mark.parametrize("case", FIXTURE["cases"], ids=lambda c: c["id"])
def test_shared_cases(case):
    content = merged(valid_content(), case["patch"]) if not case.get("replace") else case["patch"]
    for path in case.get("remove", []):
        node = content
        *head, last = path.split(".")
        for p in head:
            node = node[p]
        node.pop(last, None)
    got = set(validate_content(content, ctx(), final=case.get("final", True)))
    assert got == set(case["issues"]), (got, case["issues"])


def test_valid_content_has_no_issues():
    assert validate_content(valid_content(), ctx(), final=True) == []


def test_graduate_without_registration_number_is_valid():
    c = valid_content()
    c["qualifications"]["teacher_type"] = "graduate"
    del c["qualifications"]["registration_number"]
    assert validate_content(c, ctx(), final=True) == []


def test_registered_without_number_is_required():
    c = valid_content()
    del c["qualifications"]["registration_number"]
    assert validate_content(c, ctx(), final=True) == ["qualifications.registration_number:required"]


def test_draft_mode_only_reports_what_is_present():
    assert validate_content({}, ctx(), final=False) == []
    got = validate_content({"personal": {"date_of_birth": "2015-01-01"}}, ctx(), final=False)
    assert got == ["personal.date_of_birth:under_age"]


def test_empty_content_in_final_mode_lists_every_section():
    got = set(validate_content({}, ctx(), final=True))
    for need in (
        "personal.full_name:required",
        "qualifications.teacher_type:required",
        "curricula:required",
        "referees:wrong_count",
        "referee_consent:required",
        "zone_ids:required",
        "travel_radius_km:required",
        "availability:required",
        "personal.photo:required",
    ):
        assert need in got


@pytest.mark.parametrize(
    "dob,expected",
    [
        ("2008-11-25", []),
        ("2008-11-26", ["personal.date_of_birth:under_age"]),
        ("1946-11-25", []),
        ("1945-11-25", ["personal.date_of_birth:invalid"]),
        ("2027-01-01", ["personal.date_of_birth:invalid"]),
        ("1992-02-30", ["personal.date_of_birth:invalid"]),
        ("17/04/1992", ["personal.date_of_birth:invalid"]),
        ("１９９２-04-17", ["personal.date_of_birth:invalid"]),
    ],
)
def test_age_boundaries_use_the_kampala_date(dob, expected):
    c = valid_content()
    c["personal"]["date_of_birth"] = dob
    assert validate_content(c, ctx(), final=True) == expected


def test_overlap_adjacent_and_minimum_hours():
    c = valid_content()
    c["availability"] = [
        {"weekday": 1, "start_minute": 900, "end_minute": 1020},
        {"weekday": 1, "start_minute": 1020, "end_minute": 1200},  # adjacent: fine
        {"weekday": 2, "start_minute": 900, "end_minute": 1200},
    ]
    assert validate_content(c, ctx(), final=True) == []
    c["availability"][1]["start_minute"] = 990
    assert "availability[1]:overlap" in validate_content(c, ctx(), final=True)
    c["availability"] = [{"weekday": 1, "start_minute": 900, "end_minute": 1200}]
    assert validate_content(c, ctx(), final=True) == ["availability:too_few_hours"]
    assert validate_content(c, ctx(min_weekly_minutes=300), final=True) == []


def test_peak_windows_match_nfr_5():
    assert is_peak(1, 15 * 60, 16 * 60) and not is_peak(1, 9 * 60, 15 * 60)
    assert is_peak(6, 8 * 60, 9 * 60) and not is_peak(6, 18 * 60, 19 * 60)
    assert is_peak(1, 19 * 60, 21 * 60)


def test_text_cleaning_and_unicode():
    assert clean_text("  Ana  Maria\t\n ") == "Ana Maria"
    assert clean_text("é") == "é"  # NFC
    c = valid_content()
    c["personal"]["full_name"] = "Nakató Njéri-Ñ 王小明"
    assert validate_content(c, ctx(), final=True) == []
    c["personal"]["full_name"] = "Gra​ce"  # zero width space
    assert validate_content(c, ctx(), final=True) == ["personal.full_name:invalid"]
    c["personal"]["full_name"] = "12345"
    assert validate_content(c, ctx(), final=True) == ["personal.full_name:invalid"]
    c["personal"]["full_name"] = "A" * 101
    assert validate_content(c, ctx(), final=True) == ["personal.full_name:too_long"]


def test_normalise_content_cleans_text_and_phones():
    c = valid_content()
    c["personal"]["full_name"] = "  Grace   Nakato "
    out = normalise_content(c)
    assert out["personal"]["full_name"] == "Grace Nakato"
    assert out["referees"][0]["phone"] == "+256701234567"
    assert out["referees"][1]["phone"] == "+256755000111"


def test_referee_phone_normalisation_matches_a01_fixture():
    cases = json.loads(
        (ROOT / "packages/flutter_shared/test/fixtures/ug_phone_cases.json").read_text()
    )
    valid = cases.get("valid") or cases.get("accepted") or []
    assert valid, cases.keys()
    for item in valid[:12]:
        raw, want = (item["input"], item["e164"]) if isinstance(item, dict) else item
        c = valid_content()
        c["referees"][0]["phone"] = raw
        out = normalise_content(c)
        assert out["referees"][0]["phone"] == want
