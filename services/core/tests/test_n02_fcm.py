"""N02 (#127): FcmPushChannel. Mock transports and fixtures only; UNVERIFIED against real FCM."""

import base64
import json
import logging
from datetime import datetime, timedelta

import httpx
import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding

from hs_core.notifications.channels import FailureKind, PushMessage, send_each
from hs_core.notifications.fcm import (
    MAX_BATCH,
    FcmPushChannel,
    parse_service_account,
)
from hs_core.notifications.httpsafe import build_client
from n02_helpers import (
    CANARY_TOKEN,
    NOW,
    Transport,
    json_response,
    public_key,
    service_account_json,
)
from notify_helpers import CANCEL_VARS, LESSON, PARENT, TEACHER, Rig

SEND_URL = "https://fcm.googleapis.com/v1/projects/hs-test-project/messages:send"
TOKEN_URL = "https://oauth2.googleapis.com/token"


class Clock:
    def __init__(self, now=NOW):
        self.now = now

    def __call__(self):
        return self.now


def msg(token=CANARY_TOKEN, **kw):
    base = dict(
        token=token,
        title="Session cancelled",
        body="Tomorrow's lesson is cancelled",
        data={"notification_id": "n1", "deep_link": "hs://sessions/s1"},
        idempotency_key="n1:0:abc",
    )
    base.update(kw)
    return PushMessage(**base)


def make(fcm_answers=None, clock=None, **kw):
    """Channel over a mock transport. `fcm_answers`: callable(request) -> Response (send URL)."""
    state = {"tokens_issued": 0}

    def handler(req: httpx.Request):
        if str(req.url) == TOKEN_URL:
            state["tokens_issued"] += 1
            return json_response("google_token_ok")
        return fcm_answers(req) if fcm_answers else json_response("fcm_ok")

    t = Transport(handler)
    ch = FcmPushChannel(
        parse_service_account(service_account_json()),
        client=t.client,
        clock=clock or Clock(),
        **kw,
    )
    return ch, t, state


def sends(t):
    return [r for r in t.requests if str(r.url) == SEND_URL]


# ------------------------------------------------------------------ OAuth
def test_oauth_jwt_is_a_valid_rs256_assertion_with_pinned_audience_and_endpoint():
    ch, t, _ = make()
    assert ch.send(msg()).accepted
    tok = next(r for r in t.requests if str(r.url) == TOKEN_URL)  # NOT the evil token_uri
    form = dict(x.split("=", 1) for x in tok.content.decode().split("&"))
    assert form["grant_type"].replace("%3A", ":") == "urn:ietf:params:oauth:grant-type:jwt-bearer"
    h, c, sig = form["assertion"].split(".")
    pad = lambda s: s + "=" * (-len(s) % 4)  # noqa: E731
    header, claims = (
        json.loads(base64.urlsafe_b64decode(pad(h))),
        json.loads(base64.urlsafe_b64decode(pad(c))),
    )
    assert header == {"alg": "RS256", "typ": "JWT", "kid": "kid123"}
    assert claims["iss"].endswith("iam.gserviceaccount.com")
    assert claims["scope"] == "https://www.googleapis.com/auth/firebase.messaging"
    assert claims["aud"] == TOKEN_URL
    assert claims["iat"] == int(NOW.timestamp()) and claims["exp"] - claims["iat"] == 3600
    public_key().verify(
        base64.urlsafe_b64decode(pad(sig)), f"{h}.{c}".encode(), padding.PKCS1v15(), hashes.SHA256()
    )
    assert sends(t)[0].headers["authorization"] == "Bearer ya29.example-access-token-canary"


def test_access_token_is_cached_and_refreshed_before_expiry():
    clock = Clock()
    ch, _, state = make(clock=clock)
    for _ in range(3):
        ch.send(msg())
    assert state["tokens_issued"] == 1
    clock.now += timedelta(seconds=3599 - 299)  # inside the 5 minute refresh margin
    ch.send(msg())
    assert state["tokens_issued"] == 2


def test_a_401_refreshes_the_token_once_then_succeeds():
    seen = {"n": 0}

    def fcm(req):
        seen["n"] += 1
        return (
            json_response("fcm_unauthenticated", 401) if seen["n"] == 1 else json_response("fcm_ok")
        )

    ch, t, state = make(fcm)
    assert ch.send(msg()).accepted
    assert state["tokens_issued"] == 2 and len(sends(t)) == 2


def test_a_persistent_401_is_transient_not_a_loop():
    ch, t, _ = make(lambda r: json_response("fcm_unauthenticated", 401))
    res = ch.send(msg())
    assert res.failure is FailureKind.TRANSIENT and len(sends(t)) == 2


def test_third_party_auth_error_is_permanent_and_does_not_retry():
    ch, t, _ = make(lambda r: json_response("fcm_third_party_auth", 401))
    res = ch.send(msg())
    assert res.failure is FailureKind.PERMANENT and res.detail == "third_party_auth"
    assert len(sends(t)) == 1


@pytest.mark.parametrize(
    "status,kind",
    [(400, FailureKind.PERMANENT), (401, FailureKind.PERMANENT), (403, FailureKind.PERMANENT),
     (429, FailureKind.TRANSIENT), (500, FailureKind.TRANSIENT), (503, FailureKind.TRANSIENT)],
)  # fmt: skip
def test_token_endpoint_failures(status, kind):
    t = Transport(lambda r: json_response("google_token_invalid_grant", status))
    ch = FcmPushChannel(
        parse_service_account(service_account_json()), client=t.client, clock=Clock()
    )
    res = ch.send(msg())
    assert not res.accepted and res.failure is kind
    assert len(t.requests) == 1  # no FCM call without a token


@pytest.mark.parametrize("bad", [{}, {"access_token": ""}, {"access_token": 5}, []])
def test_unreadable_token_reply_is_transient(bad):
    t = Transport(lambda r: httpx.Response(200, json=bad))
    ch = FcmPushChannel(
        parse_service_account(service_account_json()), client=t.client, clock=Clock()
    )
    assert ch.send(msg()).failure is FailureKind.TRANSIENT


def test_token_transport_error():
    def boom(r):
        raise httpx.ConnectTimeout("t")

    ch = FcmPushChannel(
        parse_service_account(service_account_json()),
        client=Transport(boom).client,
        clock=Clock(),
    )
    assert ch.send(msg()).failure is FailureKind.TRANSIENT


# ------------------------------------------------------------------ payload
def test_notification_plus_data_with_android_and_apns_blocks():
    ch, t, _ = make()
    ch.send(msg(priority="critical", ttl_s=1800, channel="hs_critical"))
    m = json.loads(sends(t)[0].content)["message"]
    assert m["token"] == CANARY_TOKEN
    assert m["notification"] == {
        "title": "Session cancelled",
        "body": "Tomorrow's lesson is cancelled",
    }
    assert m["data"] == {"notification_id": "n1", "deep_link": "hs://sessions/s1"}
    assert m["android"]["priority"] == "HIGH" and m["android"]["ttl"] == "1800s"
    assert m["android"]["notification"] == {"channel_id": "hs_critical"}
    assert m["apns"]["headers"]["apns-priority"] == "10"
    assert m["apns"]["headers"]["apns-expiration"] == str(int(NOW.timestamp()) + 1800)
    assert "idempotency" not in json.dumps(m).lower()  # FCM has none: the key is NOT sent


@pytest.mark.parametrize(
    "prio,android,apns",
    [
        ("critical", "HIGH", "10"),
        ("operational", "NORMAL", "5"),
        ("marketing", "NORMAL", "5"),
        ("weird", "NORMAL", "5"),
    ],
)
def test_priority_mapping(prio, android, apns):
    ch, t, _ = make()
    ch.send(msg(priority=prio))
    m = json.loads(sends(t)[0].content)["message"]
    assert m["android"]["priority"] == android and m["apns"]["headers"]["apns-priority"] == apns


@pytest.mark.parametrize(
    "ttl,expect", [(None, "3600s"), (0, "0s"), (-50, "0s"), (10**9, f"{28 * 24 * 3600}s")]
)
def test_ttl_clamped_and_defaulted(ttl, expect):
    ch, t, _ = make()
    ch.send(msg(ttl_s=ttl))
    assert json.loads(sends(t)[0].content)["message"]["android"]["ttl"] == expect


def test_critical_default_ttl_is_a_day():
    ch, t, _ = make()
    ch.send(msg(priority="critical"))
    assert json.loads(sends(t)[0].content)["message"]["android"]["ttl"] == "86400s"


def test_collapse_key_and_bad_values():
    ch, t, _ = make()
    ch.send(msg(collapse_key="reminder-s1"))
    m = json.loads(sends(t)[0].content)["message"]
    assert m["android"]["collapse_key"] == "reminder-s1"
    assert m["apns"]["headers"]["apns-collapse-id"] == "reminder-s1"
    ch.send(msg(collapse_key="bad key!"))
    assert "collapse_key" not in json.loads(sends(t)[1].content)["message"]["android"]


def test_bad_android_channel_id_is_dropped():
    ch, t, _ = make()
    ch.send(msg(channel="../evil"))
    assert "notification" not in json.loads(sends(t)[0].content)["message"]["android"]


@pytest.mark.parametrize(
    "key",
    ["from", "notification", "message_type", "google.x", "gcm_y", "1bad", "a-b", "", "x" * 65],
)
def test_reserved_or_malformed_data_keys_are_refused_locally(key):
    ch, t, _ = make()
    res = ch.send(msg(data={key: "v"}))
    assert res.failure is FailureKind.PERMANENT and res.detail == "bad_data" and t.requests == []


def test_non_string_data_values_refused():
    ch, t, _ = make()
    assert ch.send(msg(data={"a": 5})).detail == "bad_data"  # type: ignore[dict-item]


def test_payload_over_4096_bytes_is_refused_locally_without_a_call():
    ch, t, _ = make()
    res = ch.send(msg(body="x" * 4200))
    assert res.failure is FailureKind.PERMANENT and res.detail == "payload_too_large"
    assert t.requests == []


def test_payload_at_the_limit_boundary():
    ch, t, _ = make()
    small = msg(body="x" * 10)
    overhead = (
        len(json.dumps(ch.build_payload(small), ensure_ascii=False).encode())
        - len(CANARY_TOKEN)
        - 10
    )
    ok = msg(body="x" * (4096 - overhead))
    assert ch.send(ok).accepted
    assert ch.send(msg(body="x" * (4096 - overhead + 1))).detail == "payload_too_large"


@pytest.mark.parametrize("token", ["", "x" * 1025, "a\nb", None])
def test_invalid_tokens_are_unregistered_without_a_call(token):
    ch, t, _ = make()
    res = ch.send(msg(token=token))  # type: ignore[arg-type]
    assert res.failure is FailureKind.UNREGISTERED and t.requests == []


# ------------------------------------------------------------------ errors
@pytest.mark.parametrize(
    "fixture_name,status,kind,detail",
    [
        ("fcm_unregistered", 404, FailureKind.UNREGISTERED, "unregistered"),
        ("fcm_invalid_argument_token", 400, FailureKind.UNREGISTERED, "invalid_token"),
        ("fcm_invalid_argument_payload", 400, FailureKind.PERMANENT, "invalid_argument"),
        ("fcm_sender_mismatch", 403, FailureKind.PERMANENT, "sender_id_mismatch"),
        ("fcm_quota", 429, FailureKind.TRANSIENT, "quota_exceeded"),
        ("fcm_unavailable", 503, FailureKind.TRANSIENT, "unavailable"),
        ("fcm_internal", 500, FailureKind.TRANSIENT, "unavailable"),
    ],
)
def test_error_mapping_from_recorded_shapes(fixture_name, status, kind, detail):
    ch, _, _ = make(lambda r: json_response(fixture_name, status))
    res = ch.send(msg())
    assert not res.accepted and res.failure is kind and res.detail == detail


def test_sender_id_mismatch_never_revokes_tokens():
    """A config problem must not wipe every device token."""
    ch, _, _ = make(lambda r: json_response("fcm_sender_mismatch", 403))
    assert ch.send(msg()).failure is not FailureKind.UNREGISTERED


@pytest.mark.parametrize("status", [404, 408, 418, 502])
def test_non_json_error_bodies(status):
    ch, _, _ = make(lambda r: httpx.Response(status, content=b"<html>"))
    res = ch.send(msg())
    assert res.failure in (FailureKind.UNREGISTERED, FailureKind.TRANSIENT, FailureKind.PERMANENT)
    assert res.failure is not FailureKind.UNREGISTERED or status == 404


def test_success_with_unreadable_body_is_still_accepted_never_risk_a_duplicate():
    ch, _, _ = make(lambda r: httpx.Response(200, content=b"garbage"))
    res = ch.send(msg())
    assert res.accepted and res.provider_message_id is None


def test_provider_message_id_is_kept():
    ch, _, _ = make()
    assert (
        ch.send(msg()).provider_message_id
        == "projects/hs-test-project/messages/0:1700000000000000%abcdef"
    )


@pytest.mark.parametrize(
    "header,expected",
    [
        ("30", 30.0),
        ("-4", 0.0),
        ("99999999", 3600.0),
        ("nan", None),
        ("bogus", None),
        ("Mon, 18 Jan 2027 09:01:00 GMT", 60.0),
    ],
)
def test_retry_after_matrix_on_503(header, expected):
    ch, _, _ = make(lambda r: json_response("fcm_unavailable", 503, {"retry-after": header}))
    assert ch.send(msg()).retry_after_s == expected


def test_quota_default_wait_when_no_header_is_one_minute():
    ch, _, _ = make(lambda r: json_response("fcm_quota", 429))
    assert ch.send(msg()).retry_after_s == 60.0


def test_transport_error_is_transient_and_logs_no_token(caplog):
    def fcm(req):
        raise httpx.ReadTimeout(f"timeout sending {CANARY_TOKEN}")

    ch, _, _ = make(fcm)
    with caplog.at_level(logging.DEBUG):
        res = ch.send(msg())
    assert res.failure is FailureKind.TRANSIENT and CANARY_TOKEN not in caplog.text


# ------------------------------------------------------------------ redirects (two servers)
def _two(status=302):
    from test_n02_http import _serve

    target = _serve()
    origin = _serve(f"http://127.0.0.1:{target.server_port}/steal", status)
    return origin, target


def _stop(*servers):
    for s in servers:
        s.shutdown()
        s.server_close()


@pytest.mark.parametrize("status", [301, 302, 307, 308])
def test_fcm_send_redirect_is_refused_and_the_bearer_never_reaches_the_target(status):
    origin, target = _two(status)
    try:
        ch = FcmPushChannel(
            parse_service_account(service_account_json()),
            base_url=f"http://127.0.0.1:{origin.server_port}",
            token_url=f"http://127.0.0.1:{origin.server_port}/token",
            client=build_client(),
            clock=Clock(),
        )
        res = ch.send(msg())
        assert not res.accepted
        assert target.seen == []  # neither the JWT assertion nor a bearer token leaked
    finally:
        _stop(origin, target)


def test_token_endpoint_redirect_is_refused_too():
    origin, target = _two()
    try:
        ch = FcmPushChannel(
            parse_service_account(service_account_json()),
            token_url=f"http://127.0.0.1:{origin.server_port}/token",
            client=build_client(),
            clock=Clock(),
        )
        assert not ch.send(msg()).accepted and target.seen == []
        assert len(origin.seen) == 1
    finally:
        _stop(origin, target)


# ------------------------------------------------------------------ batching / back-pressure
def test_send_each_returns_one_result_per_message_in_order():
    answers = iter(
        [json_response("fcm_ok"), json_response("fcm_unregistered", 404), json_response("fcm_ok")]
    )
    ch, _, _ = make(lambda r: next(answers))
    res = ch.send_each([msg(f"tok-{i}") for i in range(3)])
    assert [r.accepted for r in res] == [True, False, True]
    assert res[1].failure is FailureKind.UNREGISTERED


def test_one_message_raising_does_not_stop_the_others():
    calls = {"n": 0}

    def fcm(req):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError(f"bug with {CANARY_TOKEN}")
        return json_response("fcm_ok")

    ch, _, _ = make(fcm)
    res = ch.send_each([msg(f"t{i}") for i in range(3)])
    assert [r.accepted for r in res] == [True, False, True]
    assert res[1].detail == "exception:RuntimeError" and CANARY_TOKEN not in repr(res)


def test_1200_messages_are_processed_in_chunks_of_500_and_all_answered():
    ch, t, _ = make()
    res = ch.send_each([msg(f"tok-{i}") for i in range(1200)])
    assert len(res) == 1200 and all(r.accepted for r in res) and MAX_BATCH == 500
    assert len(sends(t)) == 1200


def test_quota_pushback_stops_the_rest_of_the_batch_without_calling():
    n = {"i": 0}

    def fcm(req):
        n["i"] += 1
        return (
            json_response("fcm_ok")
            if n["i"] <= 2
            else json_response("fcm_quota", 429, {"retry-after": "45"})
        )

    ch, t, _ = make(fcm)
    res = ch.send_each([msg(f"tok-{i}") for i in range(10)])
    assert [r.accepted for r in res[:2]] == [True, True]
    assert res[2].detail == "quota_exceeded" and all(r.detail == "backpressure" for r in res[3:])
    assert all(r.retry_after_s == 45.0 for r in res[2:])
    assert len(sends(t)) == 3  # no hammering of a throttled API


def test_module_send_each_falls_back_for_channels_without_batching():
    class One:
        def send(self, m):
            if m.token == "boom":
                raise ValueError("secret " + m.token)
            return type(self)._ok

    from hs_core.notifications.channels import OK

    One._ok = OK
    res = send_each(One(), [msg("a"), msg("boom"), msg("b")])  # type: ignore[arg-type]
    assert [r.accepted for r in res] == [True, False, True] and res[
        1
    ].detail == "exception:ValueError"


# ------------------------------------------------------------------ service account / secrets
@pytest.mark.parametrize(
    "mutate,needle",
    [
        (lambda d: d.update(type="authorized_user"), "service account"),
        (lambda d: d.update(client_email="not an email"), "client_email"),
        (lambda d: d.pop("client_email"), "client_email"),
        (
            lambda d: d.update(
                private_key="-----BEGIN "
                + "PRIVATE KEY-----\nAAAA\n-----END "
                + "PRIVATE KEY-----",
            ),
            "PEM",
        ),
        (lambda d: d.update(private_key=5), "private_key"),
        (lambda d: d.update(project_id="X"), "project id"),
        (lambda d: d.pop("project_id"), "project id"),
    ],
)
def test_service_account_validation_never_echoes_values(mutate, needle):
    doc = json.loads(service_account_json())
    mutate(doc)
    with pytest.raises(ValueError, match=needle) as e:
        parse_service_account(json.dumps(doc))
    assert "BEGIN" not in str(e.value) and "AAAA" not in str(e.value)


def test_service_account_not_json_and_project_override():
    with pytest.raises(ValueError, match="JSON"):
        parse_service_account("{{{")
    doc = json.loads(service_account_json())
    doc.pop("project_id")
    assert parse_service_account(json.dumps(doc), "my-override-1").project_id == "my-override-1"


def test_secrets_not_in_repr_or_logs(caplog):
    ch, _, _ = make(lambda r: json_response("fcm_unavailable", 503))
    with caplog.at_level(logging.DEBUG):
        ch.send(msg())
    acct = parse_service_account(service_account_json())
    for text in (repr(ch), str(ch), repr(acct), caplog.text, repr(msg())):
        assert "PRIVATE KEY" not in text and CANARY_TOKEN not in text
        assert "ya29.example-access-token-canary" not in text


# ------------------------------------------------------------------ through the service
def _rig_with(fcm_answers):
    rig = Rig()
    ch, t, _ = make(fcm_answers, clock=rig.clock)
    rig.svc.push = ch
    return rig, t


def test_user_on_several_devices_all_receive_the_push_and_expired_tokens_are_cleaned():
    def fcm(req):
        tok = json.loads(req.content)["message"]["token"]
        return (
            json_response("fcm_unregistered", 404) if tok == "tok-dead" else json_response("fcm_ok")
        )

    rig, t = _rig_with(fcm)
    for tok in ("tok-a", "tok-b", "tok-dead"):
        rig.devices.register("u_parent", "parent", "android", tok)
    rig.svc.send(
        "session_cancelled_parent", PARENT, {"lesson_time": LESSON, "reason_label": "x"}, "k-multi"
    )
    delivered = sorted(json.loads(r.content)["message"]["token"] for r in sends(t))
    assert delivered == ["tok-a", "tok-b", "tok-dead"]
    assert rig.row("push")["status"] == "sent"
    live = sorted(d.token for d in rig.devices.active_tokens("u_parent"))
    assert live == ["tok-a", "tok-b"]  # the unregistered token was revoked


def test_all_tokens_failing_permanently_makes_a_critical_event_fall_back_to_email():
    rig, _ = _rig_with(lambda r: json_response("fcm_unregistered", 404))
    rig.user(TEACHER, token="tok-x")
    rig.svc.send("session_cancelled_teacher", TEACHER, CANCEL_VARS, "k-fb")
    assert rig.row("push", "u_teacher")["status"] == "fallback"
    assert rig.row("email", "u_teacher")["status"] == "sent"


def test_quota_retry_after_delays_the_row_and_critical_priority_reaches_fcm():
    rig, t = _rig_with(lambda r: json_response("fcm_quota", 429, {"retry-after": "1200"}))
    rig.user(PARENT, token="tok-x")
    rig.svc.send(
        "session_cancelled_parent", PARENT, {"lesson_time": LESSON, "reason_label": "x"}, "k-q"
    )
    row = rig.row("push")
    assert row["status"] == "queued"
    wait = (datetime.fromisoformat(row["scheduled_at"]) - rig.clock.now).total_seconds()
    assert wait == 1200
    m = json.loads(sends(t)[0].content)["message"]
    assert (
        m["android"]["priority"] == "HIGH"
        and m["android"]["notification"]["channel_id"] == "hs_critical"
    )
    assert 0 < int(m["android"]["ttl"][:-1])


def test_at_least_once_a_crash_after_accept_duplicates_once_per_device():
    """Documented limitation (docs/ops/notifications.md): FCM has no idempotency key."""
    from hs_core.notifications.channels import SimulatedCrash

    crash = {"once": True}

    def fcm(req):
        if crash["once"]:
            crash["once"] = False
            raise SimulatedCrash
        return json_response("fcm_ok")

    rig, t = _rig_with(fcm)
    rig.user(PARENT, token="tok-x")
    with pytest.raises(SimulatedCrash):
        rig.svc.send("session_log_published_parent", PARENT, {"lesson_time": LESSON}, "k-alo")
    rig.clock.advance(seconds=rig.config.lease_s + 5)
    rig.svc.send("session_log_published_parent", PARENT, {"lesson_time": LESSON}, "k-alo")
    assert len(sends(t)) == 2 and rig.row("push")["status"] == "sent"  # one duplicate, no more
    rig.svc.send("session_log_published_parent", PARENT, {"lesson_time": LESSON}, "k-alo")
    assert len(sends(t)) == 2
