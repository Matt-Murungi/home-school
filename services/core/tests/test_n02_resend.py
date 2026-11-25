"""N02 (#127): ResendEmailSender (send side). Mock transport only; UNVERIFIED vs real Resend."""

import logging

import httpx
import pytest

from hs_core.notifications.channels import (
    EmailChannel,
    EmailError,
    EmailMessage,
    SimulatedCrash,
)
from hs_core.notifications.httpsafe import build_client
from hs_core.notifications.resend import ResendEmailSender
from n02_helpers import (
    API_KEY,
    CANARY_EMAIL,
    NID,
    NOW,
    WEBHOOK_SECRET,
    Transport,
    json_response,
)
from notify_helpers import LESSON, PARENT, Rig


def sender(handler, **kw):
    t = Transport(handler)
    kw.setdefault("from_address", "no-reply@mail.example.org")
    s = ResendEmailSender(API_KEY, WEBHOOK_SECRET, client=t.client, clock=lambda: NOW, **kw)
    return s, t


def msg(**kw):
    base = dict(
        to=CANARY_EMAIL,
        subject="Payment failed",
        html="<p>Hi &amp; bye</p>",
        text="Hi & bye",
        tags={"template": "payment_failed_parent", "notification_id": NID},
    )
    base.update(kw)
    return EmailMessage(**base)


def test_request_shape_matches_the_public_docs():
    s, t = sender(lambda r: json_response("resend_send_ok"), reply_to="support@example.org")
    res = s.send(msg(), idempotency_key="row1:0")
    assert res.accepted and res.provider_message_id == "49a3999c-0ce1-4ea6-ab68-afcd6dc2e794"
    (req,) = t.requests
    assert req.method == "POST" and str(req.url) == "https://api.resend.com/emails"
    assert req.headers["authorization"] == f"Bearer {API_KEY}"
    assert req.headers["idempotency-key"] == "row1:0"
    body = t.bodies[0]
    assert body["from"] == "Home School <no-reply@mail.example.org>"
    assert body["to"] == [CANARY_EMAIL] and body["reply_to"] == ["support@example.org"]
    assert body["html"] == "<p>Hi &amp; bye</p>" and body["text"] == "Hi & bye"
    assert "attachments" not in body
    assert {"name": "notification_id", "value": NID} in body["tags"]


def test_tags_are_sanitised_to_resend_rules():
    s, t = sender(lambda r: json_response("resend_send_ok"))
    s.send(msg(tags={"a b": "x@y.z", "k" * 300: "v" * 300}), idempotency_key="k:0")
    for tag in t.bodies[0]["tags"]:
        for part in tag.values():
            assert part and len(part) <= 256 and all(c.isalnum() or c in "_-" for c in part)


def test_idempotency_key_limits():
    s, t = sender(lambda r: json_response("resend_send_ok"))
    assert not s.send(msg(), idempotency_key="").accepted
    assert not s.send(msg(), idempotency_key="x" * 257).accepted
    assert not s.send(msg(), idempotency_key="a b").accepted
    assert not s.send(msg(), idempotency_key="a\r\nX: y").accepted
    assert t.requests == []  # refused before any network call
    assert s.send(msg(), idempotency_key="x" * 256).accepted


@pytest.mark.parametrize(
    "status,fixture_name,error,retry",
    [
        (422, "resend_err_validation", EmailError.INVALID_ADDRESS, None),
        (429, "resend_err_rate", EmailError.RATE_LIMITED, None),
        (429, "resend_err_quota", EmailError.RATE_LIMITED, 3600.0),
        (409, "resend_err_concurrent", EmailError.PROVIDER_DOWN, None),
        (409, "resend_err_idem_mismatch", EmailError.REJECTED, None),
        (500, "resend_err_500", EmailError.PROVIDER_DOWN, None),
        (401, "resend_err_500", EmailError.REJECTED, None),
        (403, "resend_err_500", EmailError.REJECTED, None),
        (400, "resend_err_500", EmailError.REJECTED, None),
        (503, "resend_err_500", EmailError.PROVIDER_DOWN, None),
    ],
)
def test_error_mapping(status, fixture_name, error, retry):
    s, _ = sender(lambda r: json_response(fixture_name, status))
    res = s.send(msg(), idempotency_key="k:0")
    assert not res.accepted and res.error is error and res.retry_after_s == retry


@pytest.mark.parametrize(
    "header,expected",
    [("7", 7.0), ("-5", 0.0), ("9999999", 3600.0), ("nan", None), ("junk", None)],
)
def test_retry_after_on_429_is_clamped(header, expected):
    s, _ = sender(lambda r: json_response("resend_err_rate", 429, {"retry-after": header}))
    assert s.send(msg(), idempotency_key="k:0").retry_after_s == expected


@pytest.mark.parametrize("bad", [{}, {"id": 5}, {"id": ""}, [], "text"])
def test_unreadable_200_is_a_retryable_failure_not_a_success(bad):
    s, _ = sender(lambda r: httpx.Response(200, json=bad))
    res = s.send(msg(), idempotency_key="k:0")
    assert not res.accepted and res.error is EmailError.PROVIDER_DOWN


def test_non_json_200():
    s, _ = sender(lambda r: httpx.Response(200, content=b"<html>"))
    assert s.send(msg(), idempotency_key="k:0").error is EmailError.PROVIDER_DOWN


@pytest.mark.parametrize("exc", [httpx.ConnectTimeout, httpx.ReadTimeout, httpx.ConnectError])
def test_transport_errors_are_transient_and_do_not_log_the_address(exc, caplog):
    def boom(r):
        raise exc(f"cannot reach {CANARY_EMAIL}")

    s, _ = sender(boom)
    with caplog.at_level(logging.DEBUG):
        res = s.send(msg(), idempotency_key="k:0")
    assert res.error is EmailError.PROVIDER_DOWN
    assert CANARY_EMAIL not in caplog.text and API_KEY not in caplog.text


def test_redirect_is_refused_with_a_real_client():
    from test_n02_http import _serve

    target, origin = _serve(), None
    origin = _serve(f"http://127.0.0.1:{target.server_port}/steal")
    try:
        s = ResendEmailSender(
            API_KEY,
            WEBHOOK_SECRET,
            "no-reply@mail.example.org",
            base_url=f"http://127.0.0.1:{origin.server_port}",
            client=build_client(),
        )
        res = s.send(msg(), idempotency_key="k:0")
        assert not res.accepted and res.error is EmailError.REJECTED
        assert target.seen == [] and len(origin.seen) == 1
    finally:
        for srv in (origin, target):
            srv.shutdown()
            srv.server_close()


def test_secrets_never_in_repr_or_logs(caplog):
    s, _ = sender(lambda r: json_response("resend_err_validation", 422))
    with caplog.at_level(logging.DEBUG):
        s.send(msg(), idempotency_key="k:0")
    for text in (repr(s), str(s), caplog.text, repr(msg())):
        assert API_KEY not in text and WEBHOOK_SECRET not in text and CANARY_EMAIL not in text


@pytest.mark.parametrize(
    "kw,err",
    [
        ({"api_key": ""}, "RESEND_API_KEY"),
        ({"api_key": "re_a b"}, "RESEND_API_KEY"),
        ({"api_key": "re_a\nb"}, "RESEND_API_KEY"),
        ({"webhook_secret": "nope"}, "RESEND_WEBHOOK_SECRET"),
        ({"webhook_secret": "whsec_!!!"}, "RESEND_WEBHOOK_SECRET"),
        ({"webhook_secret": "whsec_YQ=="}, "RESEND_WEBHOOK_SECRET"),
        ({"from_address": "Name <a@b.co>"}, "EMAIL_FROM"),
        ({"from_address": "a@b.co\nBcc: x@y.co"}, "EMAIL_FROM"),
        ({"reply_to": "a@b.co, c@d.co"}, "EMAIL_REPLY_TO"),
    ],
)
def test_constructor_validates_configuration(kw, err):
    args = dict(
        api_key=API_KEY, webhook_secret=WEBHOOK_SECRET, from_address="no-reply@mail.example.org"
    )
    args.update(kw)
    with pytest.raises(ValueError, match=err):
        ResendEmailSender(**args)


def test_from_name_cannot_inject_headers_or_address_lists():
    s, t = sender(
        lambda r: json_response("resend_send_ok"), from_name='Evil" <x@y.co>\r\nBcc: z@y.co'
    )
    s.send(msg(), idempotency_key="k:0")
    sent_from = t.bodies[0]["from"]
    assert "\r" not in sent_from and "\n" not in sent_from and sent_from.count("<") == 1


# ------------------------------------------------------------------ through the service
def _service_with_resend(handler):
    rig = Rig()
    rig.user(PARENT, email=CANARY_EMAIL)
    s, t = sender(handler)
    rig.svc.email = EmailChannel(s, "", None, None)
    return rig, t


def test_provider_key_is_stable_across_a_crash_and_the_provider_dedupes():
    seen: dict[str, str] = {}
    crash = {"once": True}

    def provider(req):
        key = req.headers["idempotency-key"]
        mid = seen.setdefault(key, f"mid-{len(seen) + 1}")
        if crash["once"]:
            crash["once"] = False
            raise SimulatedCrash  # the worker dies right after Resend accepted
        return httpx.Response(200, json={"id": mid})

    rig, t = _service_with_resend(provider)
    with pytest.raises(SimulatedCrash):
        rig.svc.send("teacher_assigned_parent", PARENT, _vars(), "k-resend-crash")
    rig.clock.advance(seconds=rig.config.lease_s + 5)
    rig.svc.send("teacher_assigned_parent", PARENT, _vars(), "k-resend-crash")
    keys = [r.headers["idempotency-key"] for r in t.requests]
    assert len(keys) == 2 and keys[0] == keys[1], keys  # same key: not random per attempt
    assert len(seen) == 1  # the provider saw ONE distinct message
    assert rig.row("email")["status"] == "sent"


def test_a_definite_transient_failure_moves_to_a_new_attempt_group():
    answers = [httpx.Response(500, json={"name": "application_error"}), None]

    def provider(req):
        a = answers.pop(0)
        return a or httpx.Response(200, json={"id": "mid-1"})

    rig, t = _service_with_resend(provider)
    rig.svc.send("teacher_assigned_parent", PARENT, _vars(), "k-resend-retry")
    rig.clock.advance(seconds=rig.config.retry_base_s + 1)
    rig.svc.dispatch_due()
    keys = [r.headers["idempotency-key"] for r in t.requests]
    assert len(keys) == 2 and keys[0] != keys[1] and keys[0].split(":")[0] == keys[1].split(":")[0]
    assert rig.row("email")["status"] == "sent"


def test_retry_after_from_the_provider_delays_the_next_attempt():
    answers = [
        httpx.Response(429, json={"name": "rate_limit_exceeded"}, headers={"retry-after": "900"})
    ]

    def provider(req):
        return answers.pop(0) if answers else httpx.Response(200, json={"id": "m"})

    rig, _ = _service_with_resend(provider)
    rig.svc.send("teacher_assigned_parent", PARENT, _vars(), "k-resend-429")
    row = rig.row("email")
    assert row["status"] == "queued"
    wait = (
        __import__("datetime").datetime.fromisoformat(row["scheduled_at"]) - rig.clock.now
    ).total_seconds()
    assert wait == 900  # base is 60s: the provider's wait wins


def _vars():
    return {"teacher_first_name": "Ann", "first_lesson": LESSON}
