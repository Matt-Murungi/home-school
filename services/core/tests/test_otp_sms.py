"""A01 (#43): SmsSender interface, fake, and the Africa's Talking adapter (mock transport only)."""

import httpx
import pytest

from hs_core.auth_otp.sms import (
    AfricasTalkingSender,
    FakeSmsSender,
    SmsRejected,
    SmsUnavailable,
    UnconfiguredSmsSender,
    render_otp_message,
)

KEY = "atsk_" + "k" * 40  # secret-scan: allow
BODY = "Home School: 123456"


def sender(handler, username="sandbox", sender_id=""):
    return AfricasTalkingSender(
        api_key=KEY,
        username=username,
        sender_id=sender_id,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


def ok(status=101, number="+256772123456"):
    return httpx.Response(
        201,
        json={
            "SMSMessageData": {
                "Message": "Sent to 1/1",
                "Recipients": [
                    {"statusCode": status, "number": number, "status": "Success", "cost": "UGX 30"}
                ],
            }
        },
    )


def test_message_template_is_external_and_has_app_name_code_and_hash_placeholder():
    msg = render_otp_message(code="123456", ttl_minutes=5, app_hash="FA+9qCX9VSu")
    assert "Home School" in msg and "123456" in msg and "5 minutes" in msg
    assert msg.startswith("<#>") and msg.rstrip().endswith("FA+9qCX9VSu")
    plain = render_otp_message(code="123456", ttl_minutes=5, app_hash="")
    assert not plain.startswith("<#>") and "123456" in plain


def test_message_template_comes_from_a_file_not_code():
    from hs_core.auth_otp import sms

    assert sms.TEMPLATE_FILE.name.endswith(".txt") and sms.TEMPLATE_FILE.exists()


def test_sandbox_and_live_endpoints_are_chosen_by_username():
    seen = []

    def handler(req):
        seen.append(req)
        return ok()

    sender(handler, username="sandbox").send("+256772123456", BODY)
    sender(handler, username="homeschool").send("+256772123456", BODY)
    assert str(seen[0].url) == "https://api.sandbox.africastalking.com/version1/messaging"
    assert str(seen[1].url) == "https://api.africastalking.com/version1/messaging"


def test_request_shape_headers_and_form_fields():
    seen = []

    def handler(req):
        seen.append(req)
        return ok()

    sender(handler, username="homeschool", sender_id="HOMESCHOOL").send("+256772123456", BODY)
    r = seen[0]
    assert r.headers["apiKey"] == KEY and r.headers["Accept"] == "application/json"
    form = dict(x.split("=", 1) for x in r.content.decode().split("&"))
    assert form["username"] == "homeschool" and form["to"] == "%2B256772123456"
    assert form["from"] == "HOMESCHOOL"
    assert "message=" in r.content.decode()


def test_no_sender_id_means_no_from_field():
    seen = []
    sender(lambda r: (seen.append(r), ok())[1]).send("+256772123456", BODY)
    assert b"from=" not in seen[0].content


@pytest.mark.parametrize("code", [100, 101, 102])
def test_accepted_status_codes(code):
    sender(lambda r: ok(code)).send("+256772123456", BODY)


@pytest.mark.parametrize("code", [401, 402, 405, 407, 500, 501, 502])
def test_provider_side_failures_are_retriable_unavailable(code):
    with pytest.raises(SmsUnavailable):
        sender(lambda r: ok(code)).send("+256772123456", BODY)


@pytest.mark.parametrize("code", [403, 404, 406])
def test_bad_number_failures_are_permanent_rejections(code):
    with pytest.raises(SmsRejected):
        sender(lambda r: ok(code)).send("+256772123456", BODY)


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
def test_http_errors_are_retriable(status):
    with pytest.raises(SmsUnavailable):
        sender(lambda r: httpx.Response(status, text="boom")).send("+256772123456", BODY)


def test_auth_failure_is_unavailable_not_a_user_error():
    with pytest.raises(SmsUnavailable):
        sender(lambda r: httpx.Response(401, text="bad key")).send("+256772123456", BODY)


@pytest.mark.parametrize(
    "exc", [httpx.ConnectTimeout("t"), httpx.ReadTimeout("t"), httpx.ConnectError("c")]
)
def test_network_failures_are_retriable(exc):
    def handler(req):
        raise exc

    with pytest.raises(SmsUnavailable):
        sender(handler).send("+256772123456", BODY)


@pytest.mark.parametrize(
    "payload", [{}, {"SMSMessageData": {}}, {"SMSMessageData": {"Recipients": []}}]
)
def test_malformed_provider_reply_is_unavailable(payload):
    with pytest.raises(SmsUnavailable):
        sender(lambda r: httpx.Response(201, json=payload)).send("+256772123456", BODY)


def test_non_json_reply_is_unavailable():
    with pytest.raises(SmsUnavailable):
        sender(lambda r: httpx.Response(201, text="<html>")).send("+256772123456", BODY)


def test_errors_never_contain_the_api_key_the_code_or_the_number():
    for resp in (httpx.Response(500, text=f"{KEY} 123456 +256772123456"), ok(500)):
        with pytest.raises(SmsUnavailable) as e:
            sender(lambda r, resp=resp: resp).send("+256772123456", BODY)
        text = f"{e.value!s} {e.value!r}"
        assert KEY not in text and "123456" not in text and "772123456" not in text


def test_adapter_repr_hides_the_key():
    assert KEY not in repr(sender(lambda r: ok()))


def test_client_has_timeouts():
    s = AfricasTalkingSender(api_key=KEY, username="sandbox")
    t = s._client.timeout  # noqa: SLF001
    assert t.connect is not None and t.read is not None


def test_fake_records_and_can_fail():
    f = FakeSmsSender()
    f.send("+256772123456", BODY)
    assert f.sent[0].to == "+256772123456"
    f.fail_next(SmsUnavailable("down"))
    with pytest.raises(SmsUnavailable):
        f.send("+256772123456", BODY)
    f.send("+256772123456", BODY)
    assert len(f.sent) == 2


def test_unconfigured_sender_is_unavailable():
    with pytest.raises(SmsUnavailable):
        UnconfiguredSmsSender().send("+256772123456", BODY)
