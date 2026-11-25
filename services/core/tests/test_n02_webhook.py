"""N02 (#127): Resend webhook: signature attacks, mapping fixtures, idempotent crash-safe
processing, suppression, the HTTP endpoint. Offline; the clock is injected."""

import json
import logging
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from hs_core.jobs.idempotency import row_id
from hs_core.main import create_app
from hs_core.notifications.channels import EmailChannel, WebhookRejected
from hs_core.notifications.resend import ResendEmailSender
from hs_core.notifications.suppression import SuppressionList, address_hash
from hs_core.notifications.webhooks import WebhookProcessor, cleanup_webhook_events
from hs_core.settings import Settings
from jobs_helpers import Crash, HookRepository
from n02_helpers import (
    API_KEY,
    CANARY_EMAIL,
    NID,
    NOW,
    WEBHOOK_SECRET,
    fixture,
    svix_headers,
)
from notify_helpers import LESSON, PARENT, Rig

SENDER = ResendEmailSender(
    API_KEY,
    WEBHOOK_SECRET,
    "no-reply@mail.example.org",
    clock=lambda: NOW,  # secret-scan: allow
)
VARS = {"teacher_first_name": "A", "first_lesson": LESSON}


def body_of(name: str) -> bytes:
    return json.dumps(fixture(name)).encode()


def signed(name_or_body, **kw):
    body = body_of(name_or_body) if isinstance(name_or_body, str) else name_or_body
    return svix_headers(body, **kw), body


def reject(headers, body, status=401, sender=SENDER):
    with pytest.raises(WebhookRejected) as e:
        sender.parse_webhook(headers, body)
    assert e.value.status == status
    return e.value.code


# ------------------------------------------------------------------ signature attacks
def test_valid_signature_is_accepted():
    h, b = signed("resend_delivered")
    (ev,) = SENDER.parse_webhook(h, b)
    assert ev.kind == "delivered" and ev.notification_id == NID


def test_header_names_are_case_insensitive():
    h, b = signed("resend_delivered")
    assert SENDER.parse_webhook({k.upper(): v for k, v in h.items()}, b)


def test_wrong_secret():
    h, b = signed("resend_delivered", key=b"z" * 32)
    assert reject(h, b) == "bad_signature"


def test_tampered_body():
    h, b = signed("resend_delivered")
    assert reject(h, b.replace(b"delivered", b"bounced_x")) == "bad_signature"
    assert reject(h, b + b" ") == "bad_signature"


def test_tampered_id_and_timestamp_headers():
    h, b = signed("resend_delivered")
    assert reject({**h, "svix-id": "msg_other"}, b) == "bad_signature"
    assert reject({**h, "svix-timestamp": str(int(NOW.timestamp()) + 1)}, b) == "bad_signature"


@pytest.mark.parametrize("missing", ["svix-id", "svix-timestamp", "svix-signature"])
def test_missing_headers(missing):
    h, b = signed("resend_delivered")
    h.pop(missing)
    assert reject(h, b) == "missing_headers"


def test_empty_header_values():
    h, b = signed("resend_delivered")
    assert reject({**h, "svix-signature": ""}, b) == "missing_headers"


@pytest.mark.parametrize(
    "delta,ok",
    [(0, True), (299, True), (300, True), (301, False), (-299, True), (-301, False),
     (86400, False), (-86400 * 400, False)],
)  # fmt: skip
def test_timestamp_tolerance_both_directions(delta, ok):
    ts = int(NOW.timestamp()) + delta  # delta > 0 = from the future
    h, b = signed("resend_delivered", ts=ts)
    if ok:
        assert SENDER.parse_webhook(h, b)
    else:
        assert reject(h, b) == "timestamp_out_of_tolerance"


@pytest.mark.parametrize("ts", ["abc", "-5", "1e9", "12.5", " 1", "", "9" * 40, "0x10"])
def test_malformed_timestamp(ts):
    h, b = signed("resend_delivered")
    assert reject({**h, "svix-timestamp": ts}, b) in ("malformed_headers", "missing_headers")


@pytest.mark.parametrize("mid", ["a b", "a.b\n", "x" * 200, "id;drop", "é"])
def test_malformed_message_id(mid):
    h, b = signed("resend_delivered")
    assert reject({**h, "svix-id": mid}, b) == "malformed_headers"


@pytest.mark.parametrize(
    "sig", ["", "v1,", "v1", "v2,AAAA", "nonsense", "v1,!!!", "V1,abc", ",", "v1,v1,v1"]
)
def test_garbage_signature_values(sig):
    h, b = signed("resend_delivered")
    assert reject({**h, "svix-signature": sig}, b) in ("bad_signature", "missing_headers")


def test_multiple_signatures_any_valid_one_passes_and_unknown_versions_are_ignored():
    h, b = signed("resend_delivered")
    good = h["svix-signature"]
    assert SENDER.parse_webhook({**h, "svix-signature": f"v1,AAAA v2,{good[3:]} {good}"}, b)
    assert reject({**h, "svix-signature": f"v2,{good[3:]}"}, b) == "bad_signature"


def test_signature_list_is_capped():
    h, b = signed("resend_delivered")
    junk = " ".join(["v1,AAAA"] * 10)
    assert reject({**h, "svix-signature": junk + " " + h["svix-signature"]}, b) == "bad_signature"
    assert reject({**h, "svix-signature": "v1,A" * 2000}, b) == "malformed_headers"


def test_non_json_signed_body_is_a_400_after_the_signature_check():
    h, b = signed(b"not json at all")
    assert reject(h, b, status=400) == "not_json"


@pytest.mark.parametrize("body", [b"[]", b'"x"', b"5", b"null", b'{"type": 5}', b"{}"])
def test_signed_but_wrong_shape_is_400(body):
    h, b = signed(body)
    assert reject(h, b, status=400) == "bad_shape"


def test_deeply_nested_json_is_a_400_not_a_crash():
    h, b = signed(b"[" * 100000 + b"]" * 100000)
    assert reject(h, b, status=400) in ("not_json", "bad_shape")


def test_huge_body_is_refused_before_any_work():
    h, b = signed(b"{" + b" " * (300 * 1024) + b"}")
    assert reject(h, b, status=413) == "too_large"


def test_signature_comparison_is_constant_time():
    import inspect

    import hs_core.notifications.resend as mod

    assert "compare_digest" in inspect.getsource(mod.verify_signature)


# ------------------------------------------------------------------ event mapping fixtures
@pytest.mark.parametrize(
    "name,kind,reason",
    [
        ("resend_delivered", "delivered", ""),
        ("resend_bounced_permanent", "bounced", "General"),
        ("resend_bounced_temporary", "bounced_soft", "MailboxFull"),
        ("resend_bounced_unknown_type", "bounced_soft", "X"),
        ("resend_complained", "complained", ""),
        ("resend_delivery_delayed", "bounced_soft", "delivery_delayed"),
        ("resend_failed", "failed", "provider_failed"),
        ("resend_suppressed", "suppressed", "provider_suppressed"),
    ],
)
def test_event_mapping(name, kind, reason):
    h, b = signed(name)
    (ev,) = SENDER.parse_webhook(h, b)
    assert ev.kind == kind and ev.reason == reason
    assert ev.provider_message_id == "56761188-7520-42d8-8898-ff6fc54ce618"
    assert ev.notification_id == NID and ev.address == CANARY_EMAIL
    assert ev.occurred_at == datetime(2027, 1, 18, 9, 0, 10, tzinfo=UTC)
    assert CANARY_EMAIL not in repr(ev)


@pytest.mark.parametrize("name", ["resend_sent_ignored", "resend_opened_ignored"])
def test_unknown_event_types_are_ignored_not_errors(name):
    h, b = signed(name)
    assert SENDER.parse_webhook(h, b) == []


def test_event_without_email_id_or_data_is_ignored():
    for doc in ({"type": "email.delivered"}, {"type": "email.delivered", "data": {}}):
        h, b = signed(json.dumps(doc).encode())
        assert SENDER.parse_webhook(h, b) == []


def test_hostile_tag_and_address_values_are_dropped_not_trusted():
    doc = fixture("resend_bounced_permanent")
    doc["data"]["tags"]["notification_id"] = "x" * 500
    doc["data"]["to"] = ["a@b.co,c@d.co"]
    h, b = signed(json.dumps(doc).encode())
    (ev,) = SENDER.parse_webhook(h, b)
    assert ev.notification_id is None and ev.address is None


# ------------------------------------------------------------------ processing
class Env:
    def __init__(self, repo=None):
        self.rig = Rig()
        if repo is not None:  # crash tests need a repo with a write hook
            self.rig = Rig()
            self.rig.repo = repo
            self.rig.store.repo = repo
            self.rig.devices.repo = repo
            self.rig.svc.repo = repo
            self.rig.store.seed_defaults()
        self.rig.user(PARENT, email=CANARY_EMAIL)
        self.supp = SuppressionList(self.rig.repo, self.rig.clock)
        self.rig.svc.email = EmailChannel(self.rig.email, "", None, self.supp)
        self.proc = WebhookProcessor(SENDER, self.rig.svc, self.supp, self.rig.repo, self.rig.clock)

    def make_sent_email(self, key="k-wh"):
        self.rig.svc.send("teacher_assigned_parent", PARENT, VARS, key)
        rows = [r for r in self.rig.rows(channel="email") if r["status"] == "sent"]
        return rows[-1]["$id"]

    def feed(self, name, **kw):
        h, b = signed(name, **kw)
        return self.proc.handle(h, b)

    def feed_for(self, nid, name, msg_id):
        doc = fixture(name)
        doc["data"]["tags"]["notification_id"] = nid
        b = json.dumps(doc).encode()
        return self.proc.handle(svix_headers(b, msg_id=msg_id), b), b


def test_delivered_event_updates_the_row_once_and_a_replay_is_a_noop():
    e = Env()
    nid = e.make_sent_email()
    o1, b = e.feed_for(nid, "resend_delivered", "msg_1")
    assert e.rig.row("email")["status"] == "delivered" and not o1.duplicate and o1.applied == 1
    snapshot = {
        t: e.rig.repo.list(t) for t in ("notification", "webhook_event", "email_suppression")
    }
    o2 = e.proc.handle(svix_headers(b, msg_id="msg_1"), b)  # exact replay (same svix-id)
    assert o2.duplicate and o2.applied == 0
    assert snapshot == {t: e.rig.repo.list(t) for t in snapshot}  # nothing changed


def test_hard_bounce_marks_failed_and_suppresses_the_address():
    e = Env()
    nid = e.make_sent_email()
    e.feed_for(nid, "resend_bounced_permanent", "msg_b")
    row = e.rig.row("email")
    assert row["status"] == "failed" and row["last_error"].startswith("bounced:")
    assert CANARY_EMAIL not in row["last_error"]
    assert e.supp.is_suppressed(CANARY_EMAIL) and e.supp.is_suppressed(CANARY_EMAIL.upper())


@pytest.mark.parametrize(
    "name,reason",
    [("resend_complained", "complaint"), ("resend_suppressed", "provider_suppressed")],
)
def test_complaint_and_provider_suppression_also_suppress(name, reason):
    e = Env()
    nid = e.make_sent_email()
    e.feed_for(nid, name, "m")
    assert e.supp.is_suppressed(CANARY_EMAIL)
    rid = row_id("email_suppression", address_hash(CANARY_EMAIL))
    assert e.rig.repo.get("email_suppression", rid)["reason"] == reason


@pytest.mark.parametrize(
    "name",
    ["resend_bounced_temporary", "resend_delivery_delayed", "resend_delivered", "resend_failed"],
)
def test_soft_events_never_suppress(name):
    e = Env()
    nid = e.make_sent_email()
    e.feed_for(nid, name, "m")
    assert not e.supp.is_suppressed(CANARY_EMAIL)


def test_failed_event_marks_the_row_failed():
    e = Env()
    nid = e.make_sent_email()
    e.feed_for(nid, "resend_failed", "m")
    assert e.rig.row("email")["status"] == "failed"


def test_late_delivered_after_a_bounce_does_not_reopen_the_row():
    e = Env()
    nid = e.make_sent_email()
    e.feed_for(nid, "resend_bounced_permanent", "m0")
    e.feed_for(nid, "resend_delivered", "m1")
    assert e.rig.row("email")["status"] == "failed"


def test_suppressed_address_is_never_emailed_again():
    e = Env()
    nid = e.make_sent_email("k1")
    e.feed_for(nid, "resend_bounced_permanent", "m")
    calls_before = len(e.rig.email.calls)
    e.rig.svc.send("teacher_assigned_parent", PARENT, VARS, "k2")
    assert len(e.rig.email.calls) == calls_before  # no provider call at all
    other = [r for r in e.rig.rows(channel="email") if r["$id"] != nid]
    assert other[0]["status"] == "failed" and "suppressed" in other[0]["last_error"]


def test_safeguarding_to_a_suppressed_address_alerts_and_push_still_goes():
    e = Env()
    from notify_helpers import ADMIN_LEAD

    e.rig.user(ADMIN_LEAD, email=CANARY_EMAIL)
    e.supp.add(CANARY_EMAIL, "hard_bounce")
    e.rig.svc.send("safeguarding_concern_admin", ADMIN_LEAD, {}, "k-sg")
    assert e.rig.push.deliveries == 1  # SR 8: push keeps working
    assert e.rig.row("email", ADMIN_LEAD.user_id)["status"] == "failed"
    assert any("notification_delivery_failed" in str(a) for a in e.rig.alerts.sent)


def test_suppression_release_and_rebounce():
    e = Env()
    assert e.supp.add(CANARY_EMAIL, "hard_bounce") is True
    assert e.supp.add(CANARY_EMAIL, "hard_bounce") is False  # idempotent
    assert e.supp.release(CANARY_EMAIL) is True and not e.supp.is_suppressed(CANARY_EMAIL)
    assert e.supp.add(CANARY_EMAIL, "complaint") is True and e.supp.is_suppressed(CANARY_EMAIL)


def test_suppression_row_holds_a_hash_never_the_address():
    e = Env()
    e.supp.add(CANARY_EMAIL, "hard_bounce", "evt1")
    dump = json.dumps(e.rig.repo.list("email_suppression"))
    assert CANARY_EMAIL not in dump and CANARY_EMAIL.split("@")[0] not in dump


def test_unmatched_notification_is_counted_not_an_error_but_still_suppresses():
    e = Env()
    out = e.feed("resend_bounced_permanent")  # the NID row does not exist
    assert out.unmatched == 1 and e.supp.is_suppressed(CANARY_EMAIL)


def test_ignored_event_type_still_claims_and_acks():
    e = Env()
    out = e.feed("resend_sent_ignored")
    assert out.events == 0 and not out.duplicate


# ------------------------------------------------------------------ crash injection
def _run(crash_at=None):
    repo = HookRepository()
    e = Env(repo)
    nid = e.make_sent_email()
    doc = fixture("resend_bounced_permanent")
    doc["data"]["tags"]["notification_id"] = nid
    b = json.dumps(doc).encode()
    h = svix_headers(b, msg_id="msg_crash")
    counter = {"n": 0}

    def before(op, table, rid, data):
        counter["n"] += 1
        if crash_at is not None and counter["n"] == crash_at:
            raise Crash

    repo.before = before
    try:
        e.proc.handle(h, b)
    except Crash:
        repo.before = None
        e.proc.handle(h, b)  # the provider retries the SAME delivery
    repo.before = None
    e.proc.handle(h, b)  # and once more: still fine
    return e, counter["n"]


def _end_state(e):
    return (
        e.rig.row("email")["status"],
        e.supp.is_suppressed(CANARY_EMAIL),
        sorted(r["status"] for r in e.rig.repo.list("webhook_event")),
        len(e.rig.repo.list("email_suppression")),
    )


def test_crash_before_every_write_converges_to_the_clean_end_state():
    clean, writes = _run()
    # claim, suppression create, notification update, done mark (the 2nd and 3rd runs add none)
    assert writes == 4
    want = _end_state(clean)
    assert want == ("failed", True, ["done"], 1)
    for k in range(1, writes + 1):
        e, _ = _run(crash_at=k)
        assert _end_state(e) == want, f"crash before write {k}"


def test_a_half_processed_claim_is_reapplied():
    e = Env()
    nid = e.make_sent_email()
    _, b = e.feed_for(nid, "resend_bounced_permanent", "msg_half")
    (row,) = e.rig.repo.list("webhook_event")
    e.rig.repo.update("webhook_event", row["$id"], {"status": "processing"})  # as if it died
    out = e.proc.handle(svix_headers(b, msg_id="msg_half"), b)
    assert not out.duplicate and e.rig.repo.list("webhook_event")[0]["status"] == "done"


def test_webhook_event_rows_hold_no_address_or_body():
    e = Env()
    e.feed("resend_bounced_permanent")
    dump = json.dumps(e.rig.repo.list("webhook_event"))
    assert CANARY_EMAIL not in dump and "Mailbox" not in dump


def test_cleanup_deletes_only_expired_rows_in_chunks():
    e = Env()
    for i in range(5):
        e.feed("resend_delivered", msg_id=f"m{i}")
    assert cleanup_webhook_events(e.rig.repo, NOW + timedelta(days=6)) == 0
    assert cleanup_webhook_events(e.rig.repo, NOW + timedelta(days=8), page=3) == 3
    assert cleanup_webhook_events(e.rig.repo, NOW + timedelta(days=8), page=3) == 2
    assert e.rig.repo.list("webhook_event") == []


def test_logs_are_free_of_addresses_and_secrets(caplog):
    e = Env()
    nid = e.make_sent_email()
    with caplog.at_level(logging.DEBUG):
        e.feed_for(nid, "resend_bounced_permanent", "m")
        e.feed_for(nid, "resend_bounced_permanent", "m")
    assert CANARY_EMAIL not in caplog.text and WEBHOOK_SECRET not in caplog.text


# ------------------------------------------------------------------ the HTTP endpoint
@pytest.fixture
def app_env(repo):
    rig = Rig()
    supp = SuppressionList(repo, rig.clock)
    svc = rig.svc
    svc.repo = repo
    svc.email = EmailChannel(SENDER, "", None, supp)
    app = create_app(
        settings=Settings(_env_file=None, hs_env="dev"), repository=repo, notification_service=svc
    )
    return TestClient(app), repo


def test_endpoint_accepts_a_valid_event_and_acks_a_replay(app_env):
    client, repo = app_env
    h, b = signed("resend_complained", msg_id="msg_http")
    r = client.post("/v1/webhooks/resend", content=b, headers=h)
    assert r.status_code == 200 and r.json() == {"ok": True, "duplicate": False}
    r2 = client.post("/v1/webhooks/resend", content=b, headers=h)
    assert r2.status_code == 200 and r2.json()["duplicate"] is True
    assert len(repo.list("email_suppression")) == 1


def test_endpoint_rejects_attacks_with_a_generic_answer(app_env):
    client, repo = app_env
    h, b = signed("resend_complained")
    cases = [
        dict(content=b, headers={}),
        dict(content=b + b"x", headers=h),
        dict(content=b, headers={**h, "svix-timestamp": "1"}),
        dict(content=b, headers={**h, "svix-signature": "v1,AAAA"}),
    ]
    for kw in cases:
        r = client.post("/v1/webhooks/resend", **kw)
        assert r.status_code == 401
        assert "signature" not in r.text.lower() and "secret" not in r.text.lower()
    assert repo.list("email_suppression") == [] and repo.list("webhook_event") == []


def test_endpoint_huge_and_non_json_bodies(app_env):
    client, _ = app_env
    big = b"x" * (300 * 1024)
    r = client.post("/v1/webhooks/resend", content=big, headers=svix_headers(big))
    assert r.status_code == 413
    nj = b"<<<not json>>>"
    r = client.post("/v1/webhooks/resend", content=nj, headers=svix_headers(nj))
    assert r.status_code == 400


def test_endpoint_is_503_when_the_provider_is_the_unconfigured_placeholder(client):
    r = client.post("/v1/webhooks/resend", content=b"{}", headers={"svix-id": "x"})
    assert r.status_code == 503


def test_endpoint_failure_inside_processing_is_a_5xx_so_the_provider_retries(app_env):
    client, repo = app_env
    h, b = signed("resend_complained", msg_id="msg_5xx")
    orig = repo.create

    def boom(*a, **k):
        raise RuntimeError("db down")

    repo.create = boom  # type: ignore[method-assign]
    r = client.post("/v1/webhooks/resend", content=b, headers=h)
    repo.create = orig  # type: ignore[method-assign]
    assert r.status_code == 500
    assert client.post("/v1/webhooks/resend", content=b, headers=h).status_code == 200
