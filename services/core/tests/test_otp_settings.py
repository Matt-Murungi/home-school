"""A01 (#43): new settings: validated, secrets masked, listed in .env.example and the inventory."""

import json
from pathlib import Path

import pytest
from pydantic import SecretStr

from hs_core.auth_otp.policy import OtpPolicy
from hs_core.settings import Settings

ROOT = Path(__file__).resolve().parents[3]
INV = json.loads((ROOT / "docs/ops/secrets-inventory.json").read_text())
CORE = {e["name"]: e for e in INV["entries"] if e["consumer"] == "core-service"}
NEW = [
    "africastalking_api_key",
    "africastalking_username",
    "africastalking_sender_id",
    "otp_pepper",
    "otp_app_hash",
    "otp_ttl_s",
    "otp_max_attempts",
    "otp_resend_cooldown_s",
    "otp_phone_requests_per_hour",
    "otp_phone_requests_per_day",
    "otp_ip_requests_per_hour",
    "otp_ip_requests_per_day",
    "otp_phone_failures_per_hour",
    "otp_ip_verifies_per_hour",
    "otp_dormant_days",
]


@pytest.mark.parametrize("name", NEW)
def test_new_setting_is_in_inventory_env_example_and_matches_kind(name):
    assert name in Settings.model_fields
    entry = CORE[name.upper()]
    is_masked = Settings.model_fields[name].annotation is SecretStr
    assert (entry["kind"] == "server_secret") == is_masked
    ex = (ROOT / "services/core/.env.example").read_text().splitlines()
    assert f"{name.upper()}=" in ex


def test_provider_key_and_pepper_are_secret_strings_and_never_in_repr():
    s = Settings(_env_file=None, africastalking_api_key="A" * 40, otp_pepper="B" * 40)
    assert "A" * 40 not in repr(s) and "B" * 40 not in repr(s)


def test_defaults_match_the_issue_and_spec():
    p = OtpPolicy.from_settings(Settings(_env_file=None))
    assert (p.ttl.total_seconds(), p.max_attempts, p.resend_cooldown_s) == (300, 5, 30)
    assert p.phone_requests_per_hour == 5


def _deployed(**kw):
    base = {
        "hs_env": "staging",
        "appwrite_endpoint": "https://fra.cloud.appwrite.io/v1",
        "appwrite_project_id": "proj",
        "appwrite_api_key": "k" * 64,
        "appwrite_database_id": "main",
        "job_tick_secret": "t" * 64,
        "otp_pepper": "p" * 64,
    }
    return Settings(_env_file=None, **{**base, **kw})


def test_deployed_requires_a_strong_pepper():
    assert _deployed().problems() == []
    assert any("OTP_PEPPER" in p for p in _deployed(otp_pepper="").problems())
    assert any("OTP_PEPPER" in p for p in _deployed(otp_pepper="short").problems())
    assert any("OTP_PEPPER" in p for p in _deployed(otp_pepper="changeme").problems())


@pytest.mark.parametrize(
    ("field", "bad"),
    [
        ("otp_ttl_s", 10),
        ("otp_ttl_s", 100000),
        ("otp_max_attempts", 0),
        ("otp_max_attempts", 50),
        ("otp_resend_cooldown_s", 0),
        ("otp_phone_requests_per_hour", 0),
        ("otp_phone_requests_per_hour", 1000),
    ],
)
def test_out_of_range_limits_are_rejected(field, bad):
    probs = _deployed(**{field: bad}).problems()
    assert any(field.upper() in p for p in probs)


def test_missing_provider_in_deployed_is_allowed_but_reports_unavailable_at_runtime():
    assert _deployed().problems() == []  # the provider account is owner-blocked
