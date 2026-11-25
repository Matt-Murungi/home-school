"""A01 (#43): +256 numbers only, validated for Ugandan mobile formats (AC 1)."""

import json
from pathlib import Path

import pytest

from hs_core.auth_otp.phone import InvalidPhone, mask_phone, normalize_ug_phone

CASES = json.loads(
    (
        Path(__file__).resolve().parents[3]
        / "packages/flutter_shared/test/fixtures/ug_phone_cases.json"
    ).read_text()
)


@pytest.mark.parametrize(("raw", "expected"), CASES["valid"])
def test_valid_formats_normalise_to_e164(raw, expected):
    assert normalize_ug_phone(raw) == expected


@pytest.mark.parametrize("raw", CASES["invalid"])
def test_invalid_numbers_are_rejected(raw):
    with pytest.raises(InvalidPhone):
        normalize_ug_phone(raw)


@pytest.mark.parametrize("raw", [None, 772123456, b"+256772123456", ["0772123456"]])
def test_non_strings_are_rejected(raw):
    with pytest.raises(InvalidPhone):
        normalize_ug_phone(raw)


def test_huge_input_is_rejected_quickly():
    with pytest.raises(InvalidPhone):
        normalize_ug_phone("0" * 1_000_000)


def test_mask_never_shows_the_middle_digits():
    m = mask_phone("+256772123456")
    assert m.endswith("456") and "772123" not in m and "+256772123456" != m
