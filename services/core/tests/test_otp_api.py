"""A01 (#43): POST /v1/auth/otp/request and /verify over HTTP (FastAPI TestClient, fakes only)."""

import pytest
from fastapi.testclient import TestClient

from hs_core.auth_otp.accounts import FakeUserDirectory
from hs_core.auth_otp.sms import FakeSmsSender
from hs_core.main import create_app
from hs_core.repository import InMemoryRepository
from hs_core.settings import Settings

REQ, VER = "/v1/auth/otp/request", "/v1/auth/otp/verify"


@pytest.fixture
def stack():
    sms, users, repo = FakeSmsSender(), FakeUserDirectory(), InMemoryRepository()
    s = Settings(_env_file=None, hs_env="dev", otp_pepper="x" * 40)
    app = create_app(settings=s, repository=repo, sms_sender=sms, user_directory=users)
    return TestClient(app), sms, users, repo


def code_of(sms):
    import re

    return re.search(r"(?<!\d)(\d{6})(?!\d)", sms.sent[-1].body).group(1)


def test_request_returns_200_with_timings_and_no_secret_material(stack):
    c, sms, _, _ = stack
    r = c.post(REQ, json={"phone": "0772123456", "app": "parent"})
    assert r.status_code == 200
    assert r.json() == {"status": "sent", "resend_after_s": 30, "expires_in_s": 300}
    assert code_of(sms) not in r.text


def test_verify_returns_token_triplet(stack):
    c, sms, _, _ = stack
    c.post(REQ, json={"phone": "0772123456", "app": "parent"})
    r = c.post(
        VER,
        json={
            "phone": "0772123456",
            "code": code_of(sms),
            "app": "parent",
            "device": {"platform": "android", "app_version": "1.0.0"},
        },
    )
    assert r.status_code == 200
    b = r.json()
    assert set(b) == {"user_id", "secret", "expire", "is_new_user", "dormant"}
    assert b["is_new_user"] is True and b["secret"]
    assert r.headers["cache-control"] == "no-store"


def test_wrong_code_is_400_with_attempts_left(stack):
    c, sms, _, _ = stack
    c.post(REQ, json={"phone": "0772123456", "app": "parent"})
    r = c.post(VER, json={"phone": "0772123456", "code": "000000", "app": "parent"})
    if code_of(sms) == "000000":
        pytest.skip("1-in-a-million collision")
    assert r.status_code == 400
    e = r.json()["error"]
    assert e["code"] == "invalid_code" and e["attempts_left"] == 4 and e["request_id"]


def test_resend_too_soon_is_429_with_retry_after_header(stack):
    c, *_ = stack
    c.post(REQ, json={"phone": "0772123456", "app": "parent"})
    r = c.post(REQ, json={"phone": "0772123456", "app": "parent"})
    assert r.status_code == 429
    assert r.json()["error"]["code"] == "resend_too_soon"
    assert 1 <= int(r.headers["retry-after"]) <= 30
    assert r.json()["error"]["retry_after_s"] == int(r.headers["retry-after"])


def test_invalid_phone_is_422(stack):
    c, *_ = stack
    r = c.post(REQ, json={"phone": "12345", "app": "parent"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_phone"


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"phone": "0772123456"},
        {"app": "parent"},
        {"phone": 772123456, "app": "parent"},
        {"phone": "0772123456", "app": "parent", "extra": 1},
        {"phone": "0" * 5000, "app": "parent"},
    ],
)
def test_request_validation_errors(stack, body):
    c, sms, *_ = stack
    r = c.post(REQ, json=body)
    assert r.status_code == 422 and not sms.sent


@pytest.mark.parametrize("code", [123456, None, "1" * 100])
def test_verify_validation_errors(stack, code):
    c, *_ = stack
    r = c.post(VER, json={"phone": "0772123456", "code": code, "app": "parent"})
    assert r.status_code == 422


def test_oversized_body_is_rejected(stack):
    c, *_ = stack
    r = c.post(
        REQ, content=b"{" + b" " * 70000 + b"}", headers={"content-type": "application/json"}
    )
    assert r.status_code in (413, 422)


def test_sms_outage_is_503_and_retriable(stack):
    from hs_core.auth_otp.sms import SmsUnavailable

    c, sms, *_ = stack
    sms.fail_next(SmsUnavailable("down"))
    r = c.post(REQ, json={"phone": "0772123456", "app": "parent"})
    assert r.status_code == 503 and r.json()["error"]["code"] == "sms_unavailable"
    assert c.post(REQ, json={"phone": "0772123456", "app": "parent"}).status_code == 200


def test_known_and_unknown_numbers_get_byte_identical_request_responses(stack):
    c, _, users, _ = stack
    users.add("+256701234567", labels=["parent"])
    a = c.post(REQ, json={"phone": "0701234567", "app": "parent"})
    b = c.post(REQ, json={"phone": "0772123456", "app": "parent"})
    assert (a.status_code, a.json()) == (b.status_code, b.json())


def test_per_ip_limit_uses_fly_client_ip_only_outside_dev(stack):
    c, *_ = stack
    # dev: client address is the socket peer, a spoofed header is ignored
    for i in range(25):
        r = c.post(
            REQ,
            json={"phone": f"07721234{i:02d}", "app": "parent"},
            headers={"Fly-Client-IP": f"198.51.100.{i}"},
        )
    assert r.status_code == 429 and r.json()["error"]["code"] == "rate_limited"


def test_deployed_env_trusts_fly_client_ip():
    sms, users, repo = FakeSmsSender(), FakeUserDirectory(), InMemoryRepository()
    s = Settings(_env_file=None, hs_env="dev", otp_pepper="x" * 40, otp_ip_requests_per_hour=2)
    app = create_app(
        settings=s, repository=repo, sms_sender=sms, user_directory=users, trust_proxy_ip=True
    )
    c = TestClient(app)
    for i in range(2):
        assert (
            c.post(
                REQ,
                json={"phone": f"07721234{i:02d}", "app": "parent"},
                headers={"Fly-Client-IP": "198.51.100.1"},
            ).status_code
            == 200
        )
    r = c.post(
        REQ,
        json={"phone": "0772123499", "app": "parent"},
        headers={"Fly-Client-IP": "198.51.100.1"},
    )
    assert r.status_code == 429
    assert (
        c.post(
            REQ,
            json={"phone": "0772123499", "app": "parent"},
            headers={"Fly-Client-IP": "198.51.100.2"},
        ).status_code
        == 200
    )


def test_responses_and_logs_never_contain_the_code_or_full_number(stack, caplog):
    import logging

    caplog.set_level(logging.DEBUG)
    c, sms, *_ = stack
    c.post(REQ, json={"phone": "+256 772 123 456", "app": "parent"})
    code = code_of(sms)
    r = c.post(VER, json={"phone": "+256772123456", "code": code, "app": "parent"})
    blob = " ".join(f"{rec.getMessage()} {rec.__dict__}" for rec in caplog.records)
    for secret in (code, "772123456", "+256772123456", r.json()["secret"]):
        assert secret not in blob, secret


def test_dev_without_a_provider_is_503_not_a_crash_and_not_a_fake_success():
    s = Settings(_env_file=None, hs_env="dev", otp_pepper="x" * 40)
    c = TestClient(create_app(settings=s, repository=InMemoryRepository()))
    r = c.post(REQ, json={"phone": "0772123456", "app": "parent"})
    assert r.status_code == 503 and r.json()["error"]["code"] == "sms_unavailable"
