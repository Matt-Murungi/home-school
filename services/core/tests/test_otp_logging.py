"""A01 (#43): the F08 scrubber masks OTP codes in free text and keys; the flow logs no secrets."""

import json
import logging

import pytest

from hs_core.logging_setup import JsonFormatter
from hs_core.observability.scrub import scrub_data, scrub_event, scrub_text
from otp_helpers import PHONE, Env


@pytest.mark.parametrize(
    "text",
    [
        "your code is 123456",
        "Your verification code: 123456.",
        "OTP 123456 sent",
        "otp=123456",
        "code=123456&x=1",
        "<#> Home School: your code is 123456. It expires in 5 minutes.",
        "pin 1234",
        "passcode is 98765432",
    ],
)
def test_scrubber_masks_codes_in_free_text(text):
    out = scrub_text(text)
    assert not any(d in out for d in ("123456", "98765432", " 1234")), out


@pytest.mark.parametrize(
    "key", ["otp", "otp_code", "code_hash", "salt", "verification_code", "passcode", "sms_body"]
)
def test_scrubber_masks_otp_keys(key):
    assert scrub_data({key: "123456"})[key] != "123456"


def test_scrubber_leaves_ordinary_numbers_and_status_codes_alone():
    assert scrub_text("status=201 count 123456789 UGX 50000 port 8080 postcode 256") == (
        "status=201 count 123456789 UGX 50000 port 8080 postcode 256"
    )


def test_scrubber_on_sentry_event_with_sms_body():
    ev = {"message": "send failed: your code is 654321", "extra": {"body": "your code is 654321"}}
    out = json.dumps(scrub_event(ev))
    assert "654321" not in out


def test_full_flow_log_output_has_no_code_or_full_number(caplog):
    caplog.set_level(logging.DEBUG)
    e = Env()
    e.request()
    code = e.last_code()
    with pytest.raises(Exception):  # noqa: B017
        e.verify(e.wrong_code())
    e.verify(code)
    fmt = JsonFormatter()
    lines = "\n".join(fmt.format(r) for r in caplog.records)
    raw = "\n".join(r.getMessage() + repr(r.__dict__) for r in caplog.records)
    for blob in (lines, raw):
        assert code not in blob
        assert PHONE not in blob and "772123456" not in blob
    assert "otp" in lines.lower()  # events are logged (masked), not silent


def test_failure_logs_do_not_leak_either(caplog):
    from hs_core.auth_otp.sms import SmsUnavailable

    caplog.set_level(logging.DEBUG)
    e = Env()
    e.sms.fail_next(SmsUnavailable("provider said: body=your code is 123456 to +256772123456"))
    with pytest.raises(Exception):  # noqa: B017
        e.request()
    fmt = JsonFormatter()
    lines = "\n".join(fmt.format(r) for r in caplog.records)
    assert "123456" not in lines and "772123456" not in lines


@pytest.mark.parametrize(
    "payload",
    ["code" * 60000, "code " * 50000, "code" + "x" * 250000, "pin 1" * 50000, "otp" + " " * 250000],
)
def test_otp_text_pattern_is_linear_on_adversarial_input(payload):
    import time

    t = time.perf_counter()
    scrub_text(payload)
    assert time.perf_counter() - t < 3
