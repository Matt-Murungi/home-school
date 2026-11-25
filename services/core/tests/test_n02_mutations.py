"""N02 (#127): mutation tests. Each scenario passes on the real code; with ONE guard removed
(monkeypatched) the scenario must fail. If a mutant survives, a safety property is untested."""

import json
import uuid

import httpx
import pytest

import hs_core.notifications.fcm as fcm_mod
import hs_core.notifications.httpsafe as httpsafe
import hs_core.notifications.resend as resend_mod
import hs_core.notifications.webhooks as webhooks_mod
from hs_core.jobs.alerts import FakeAlertSink
from hs_core.notifications import build_notification_service
from hs_core.notifications.channels import (
    OK,
    EmailChannel,
    PushMessage,
    SimulatedCrash,
    UnconfiguredChannel,
    WebhookRejected,
)
from hs_core.notifications.fcm import FcmPushChannel, parse_service_account
from hs_core.notifications.resend import ResendEmailSender
from hs_core.notifications.suppression import SuppressionList
from hs_core.notifications.webhooks import WebhookProcessor
from hs_core.repository import InMemoryRepository
from hs_core.settings import Settings
from n02_helpers import (
    API_KEY,
    CANARY_EMAIL,
    NOW,
    WEBHOOK_SECRET,
    Transport,
    fixture,
    json_response,
    service_account_json,
    svix_headers,
)
from notify_helpers import CANCEL_VARS, LESSON, PARENT, TEACHER, Rig


def _sender(client=None, **kw):
    return ResendEmailSender(
        API_KEY,
        WEBHOOK_SECRET,
        "no-reply@mail.example.org",
        client=client,
        clock=lambda: NOW,
        **kw,
    )


# ----------------------------------------------------------------------------- scenarios
def check_key_is_stable_across_a_crash():
    seen, crash = {}, {"once": True}

    def provider(req):
        seen.setdefault(req.headers["idempotency-key"], 1)
        if crash["once"]:
            crash["once"] = False
            raise SimulatedCrash
        return httpx.Response(200, json={"id": "m1"})

    t = Transport(provider)
    rig = Rig()
    rig.user(PARENT, email=CANARY_EMAIL)
    rig.svc.email = EmailChannel(_sender(t.client), "", None, None)
    v = {"teacher_first_name": "A", "first_lesson": LESSON}
    try:
        rig.svc.send("teacher_assigned_parent", PARENT, v, "k")
    except SimulatedCrash:
        pass
    rig.clock.advance(seconds=rig.config.lease_s + 5)
    rig.svc.send("teacher_assigned_parent", PARENT, v, "k")
    assert len(seen) == 1, f"provider saw {len(seen)} distinct idempotency keys"


def check_signature_is_enforced():
    s = _sender()
    body = json.dumps(fixture("resend_delivered")).encode()
    bad = svix_headers(body, key=b"w" * 32)
    try:
        s.parse_webhook(bad, body)
    except WebhookRejected:
        pass
    else:
        raise AssertionError("a webhook signed with the wrong secret was accepted")
    try:
        s.parse_webhook(svix_headers(body, ts=int(NOW.timestamp()) - 10_000), body)
    except WebhookRejected:
        pass
    else:
        raise AssertionError("a stale webhook was accepted")


def check_redirects_are_not_followed():
    from test_n02_http import _serve

    target = _serve()
    origin = _serve(f"http://127.0.0.1:{target.server_port}/steal")
    try:
        ch = FcmPushChannel(
            parse_service_account(service_account_json()),
            base_url=f"http://127.0.0.1:{origin.server_port}",
            token_url=f"http://127.0.0.1:{origin.server_port}/t",
            client=httpsafe.build_client(),
            clock=lambda: NOW,
        )
        ch.send(PushMessage("tok", "t", "b", {}, "k"))
        assert target.seen == [], "the redirect target was contacted"
    finally:
        for s in (origin, target):
            s.shutdown()
            s.server_close()


def check_deployed_without_credentials_does_not_fake_success():
    s = Settings(
        _env_file=None,
        hs_env="production",
        appwrite_endpoint="https://a.example.org/v1",
        appwrite_project_id="p",
        appwrite_database_id="d",
        appwrite_api_key="k" * 40,
        job_tick_secret="t" * 40,
        otp_pepper="p" * 40,
    )
    repo = InMemoryRepository()
    svc = build_notification_service(s, repo, FakeAlertSink(), clock=lambda: NOW)
    from hs_core.notifications.templates import TemplateStore

    TemplateStore(repo, clock=lambda: NOW).seed_defaults()
    svc.devices.register("u_teacher", "teacher", "android", "tok")
    svc.send("session_cancelled_teacher", TEACHER, CANCEL_VARS, "k")
    ok = [r for r in repo.list("notification") if r["channel"] in ("push", "email")]
    assert not any(r["status"] in ("sent", "delivered") for r in ok), "fake success in production"


def check_suppression_blocks_sending():
    rig = Rig()
    rig.user(PARENT, email=CANARY_EMAIL)
    supp = SuppressionList(rig.repo, rig.clock)
    supp.add(CANARY_EMAIL, "hard_bounce")
    rig.svc.email = EmailChannel(rig.email, "", None, supp)
    rig.svc.send(
        "teacher_assigned_parent", PARENT, {"teacher_first_name": "A", "first_lesson": LESSON}, "k"
    )
    assert rig.email.calls == [], "a suppressed address was emailed"


def check_replayed_delivery_is_acknowledged_without_reprocessing():
    rig = Rig()
    rig.user(PARENT, email=CANARY_EMAIL)
    supp = SuppressionList(rig.repo, rig.clock)
    proc = WebhookProcessor(_sender(), rig.svc, supp, rig.repo, rig.clock)
    body = json.dumps(fixture("resend_complained")).encode()
    h = svix_headers(body, msg_id="msg_r")
    assert not proc.handle(h, body).duplicate
    assert proc.handle(h, body).duplicate, "replay was processed again"


def check_retry_after_is_clamped():
    t = Transport(
        lambda r: (
            json_response("fcm_quota", 429, {"retry-after": "99999999"})
            if str(r.url).endswith("messages:send")
            else json_response("google_token_ok")
        )
    )
    ch = FcmPushChannel(
        parse_service_account(service_account_json()), client=t.client, clock=lambda: NOW
    )
    res = ch.send(PushMessage("tok", "t", "b", {}, "k"))
    assert res.retry_after_s is not None and res.retry_after_s <= 3600


def check_hard_bounce_suppresses():
    rig = Rig()
    rig.user(PARENT, email=CANARY_EMAIL)
    supp = SuppressionList(rig.repo, rig.clock)
    proc = WebhookProcessor(_sender(), rig.svc, supp, rig.repo, rig.clock)
    body = json.dumps(fixture("resend_bounced_permanent")).encode()
    proc.handle(svix_headers(body, msg_id="msg_b"), body)
    assert supp.is_suppressed(CANARY_EMAIL), "a hard bounce did not suppress the address"


def check_soft_bounce_does_not_suppress():
    rig = Rig()
    supp = SuppressionList(rig.repo, rig.clock)
    proc = WebhookProcessor(_sender(), rig.svc, supp, rig.repo, rig.clock)
    body = json.dumps(fixture("resend_bounced_temporary")).encode()
    proc.handle(svix_headers(body, msg_id="msg_s"), body)
    assert not supp.is_suppressed(CANARY_EMAIL), "a soft bounce suppressed the address"


def check_unregistered_token_is_revoked_but_sender_mismatch_is_not():
    def fcm(req):
        if str(req.url).endswith("messages:send"):
            tok = json.loads(req.content)["message"]["token"]
            return (
                json_response("fcm_unregistered", 404)
                if tok == "dead"
                else json_response("fcm_sender_mismatch", 403)
            )
        return json_response("google_token_ok")

    rig = Rig()
    rig.svc.push = FcmPushChannel(
        parse_service_account(service_account_json()),
        client=Transport(fcm).client,
        clock=lambda: NOW,
    )
    rig.devices.register("u_parent", "parent", "android", "dead")
    rig.devices.register("u_parent", "parent", "android", "other")
    rig.svc.send("session_log_published_parent", PARENT, {"lesson_time": LESSON}, "k")
    live = sorted(d.token for d in rig.devices.active_tokens("u_parent"))
    assert live == ["other"], live


def check_critical_is_high_priority():
    t = Transport(
        lambda r: (
            json_response("fcm_ok")
            if str(r.url).endswith("messages:send")
            else json_response("google_token_ok")
        )
    )
    ch = FcmPushChannel(
        parse_service_account(service_account_json()), client=t.client, clock=lambda: NOW
    )
    ch.send(PushMessage("tok", "t", "b", {}, "k", priority="critical"))
    m = json.loads(t.requests[-1].content)["message"]
    assert m["android"]["priority"] == "HIGH" and m["apns"]["headers"]["apns-priority"] == "10"


ALL_CHECKS = [
    check_key_is_stable_across_a_crash,
    check_signature_is_enforced,
    check_redirects_are_not_followed,
    check_deployed_without_credentials_does_not_fake_success,
    check_suppression_blocks_sending,
    check_replayed_delivery_is_acknowledged_without_reprocessing,
    check_retry_after_is_clamped,
    check_hard_bounce_suppresses,
    check_soft_bounce_does_not_suppress,
    check_unregistered_token_is_revoked_but_sender_mismatch_is_not,
    check_critical_is_high_priority,
]


@pytest.mark.parametrize("check", ALL_CHECKS, ids=lambda f: f.__name__)
def test_scenarios_pass_on_the_real_code(check):
    check()


# ----------------------------------------------------------------------------- mutants
def _random_key(mp):
    orig = ResendEmailSender.send
    mp.setattr(
        ResendEmailSender,
        "send",
        lambda self, m, *, idempotency_key: orig(self, m, idempotency_key=uuid.uuid4().hex),
    )


def _skip_signature(mp):
    mp.setattr(resend_mod, "verify_signature", lambda key, h, b, now, tol: int(NOW.timestamp()))


def _skip_timestamp(mp):
    orig = resend_mod.verify_signature
    mp.setattr(
        resend_mod, "verify_signature", lambda key, h, b, now, tol: orig(key, h, b, now, 10**12)
    )


def _follow_redirects(mp):
    real = httpx.Client
    mp.setattr(httpsafe, "build_client", lambda *a, **k: real(follow_redirects=True))


def _fake_success(mp):
    mp.setattr(UnconfiguredChannel, "send", lambda self, *a, **k: OK)
    from hs_core.notifications.channels import (
        ChannelResult,
        EmailError,
        SendResult,
        UnconfiguredEmailSender,
    )

    mp.setattr(
        UnconfiguredEmailSender,
        "send",
        lambda self, m, *, idempotency_key: SendResult("fake", True),
    )
    assert ChannelResult and EmailError


def _ignore_suppression(mp):
    mp.setattr(SuppressionList, "is_suppressed", lambda self, address: False)


def _no_replay_guard(mp):
    mp.setattr(webhooks_mod, "row_id", lambda *a: uuid.uuid4().hex[:36])


def _unclamped_retry_after(mp):
    mp.setattr(
        fcm_mod, "parse_retry_after", lambda value, now, cap=0: float(value) if value else None
    )


def _hard_bounce_not_suppressed(mp):
    mp.setattr(webhooks_mod, "_SUPPRESS", {})


def _soft_bounce_suppressed(mp):
    orig = resend_mod.map_event
    from dataclasses import replace

    def mapped(doc, ts):
        return [
            replace(e, kind="bounced") if e.kind == "bounced_soft" else e for e in orig(doc, ts)
        ]

    mp.setattr(resend_mod, "map_event", mapped)


def _sender_mismatch_revokes(mp):
    from hs_core.notifications.channels import ChannelResult, FailureKind

    orig = FcmPushChannel._interpret

    def interpret(self, resp):
        res = orig(self, resp)
        if res.detail == "sender_id_mismatch":
            return ChannelResult(False, FailureKind.UNREGISTERED, detail="x")
        return res

    mp.setattr(FcmPushChannel, "_interpret", interpret)


def _critical_normal_priority(mp):
    orig = FcmPushChannel.build_payload

    def build(self, m):
        from dataclasses import replace

        return orig(self, replace(m, priority="operational"))

    mp.setattr(FcmPushChannel, "build_payload", build)


MUTANTS = [
    ("random idempotency key per attempt", _random_key, check_key_is_stable_across_a_crash),
    ("signature check skipped", _skip_signature, check_signature_is_enforced),
    ("timestamp tolerance removed", _skip_timestamp, check_signature_is_enforced),
    ("redirects followed", _follow_redirects, check_redirects_are_not_followed),
    (
        "deployed placeholder fakes success",
        _fake_success,
        check_deployed_without_credentials_does_not_fake_success,
    ),
    ("suppression ignored", _ignore_suppression, check_suppression_blocks_sending),
    (
        "replay guard removed",
        _no_replay_guard,
        check_replayed_delivery_is_acknowledged_without_reprocessing,
    ),
    ("Retry-After not clamped", _unclamped_retry_after, check_retry_after_is_clamped),
    ("hard bounce does not suppress", _hard_bounce_not_suppressed, check_hard_bounce_suppresses),
    ("soft bounce suppresses", _soft_bounce_suppressed, check_soft_bounce_does_not_suppress),
    (
        "sender mismatch revokes tokens",
        _sender_mismatch_revokes,
        check_unregistered_token_is_revoked_but_sender_mismatch_is_not,
    ),
    (
        "critical sent at normal priority",
        _critical_normal_priority,
        check_critical_is_high_priority,
    ),
]


@pytest.mark.parametrize(
    "apply,check", [(m[1], m[2]) for m in MUTANTS], ids=[m[0] for m in MUTANTS]
)
def test_each_mutant_is_caught(apply, check, monkeypatch):
    apply(monkeypatch)
    with pytest.raises(AssertionError):
        check()
