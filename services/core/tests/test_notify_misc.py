"""N01 (#126): preferences store, retention hook, email contract mapping, wiring."""

import json
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from hs_core.main import create_app
from hs_core.notifications import build_notification_service, dispatch
from hs_core.notifications.channels import (
    EmailChannel,
    EmailError,
    EmailMessage,
    FailureKind,
    FakeEmailSender,
)
from hs_core.notifications.preferences import (
    NotPersistable,
    Preferences,
    RepositoryPreferenceStore,
    blocked_reason,
)
from hs_core.notifications.quiet_hours import KAMPALA
from hs_core.notifications.retention import expire_payloads
from hs_core.notifications.service import (
    NoDirectory,
    Recipient,
    notification_owner_id,
    public_status,
)
from hs_core.repository import InMemoryRepository
from hs_core.settings import Settings
from notify_helpers import LESSON, NOON, PARENT, Rig


def test_preference_rules_table():
    p = Preferences(marketing_muted=True, muted_channels=frozenset({"email"}))
    for cat in ("operational", "safeguarding"):
        assert blocked_reason(cat, "email", p) is None
        assert blocked_reason(cat, "push", p) is None
    assert blocked_reason("marketing", "push", p) == "marketing_muted"
    assert blocked_reason("marketing", "email", Preferences(False, frozenset({"email"}))) == (
        "channel_opt_out"
    )
    assert blocked_reason("marketing", "push", Preferences()) is None


def test_repository_preference_store_roundtrip_and_permissions():
    repo = InMemoryRepository()
    st = RepositoryPreferenceStore(repo)
    assert st.get("u1") == Preferences()
    st.set_marketing_muted("u1", True)
    st.set_marketing_muted("u1", True)
    assert st.get("u1").marketing_muted is True
    (row,) = repo.list("notification_preference")
    assert row["$permissions"] == ['read("user:u1")']
    st.set_marketing_muted("u1", False)
    assert st.get("u1").marketing_muted is False
    with pytest.raises(NotPersistable):
        st.set_muted_channels("u1", {"email"})


def test_email_error_mapping_follows_adr_0005():
    sender = FakeEmailSender()
    ch = EmailChannel(sender)
    for err, kind in [
        ("rate_limited", FailureKind.TRANSIENT),
        ("provider_down", FailureKind.TRANSIENT),
        ("invalid_address", FailureKind.PERMANENT),
        ("rejected", FailureKind.PERMANENT),
    ]:
        sender.script(err)
        r = ch.send("a@b.test", "s", "t", "<p>t</p>", {"template": "x"}, "k")
        assert not r.accepted and r.failure is kind and r.detail == EmailError(err).value
    assert ch.send("a@b.test", "s", "t", "<p>t</p>", {}, "k").accepted


def test_fake_email_sender_webhook_feeds_record_delivery():
    rig = Rig()
    rig.user(PARENT)
    rig.svc.send(
        "teacher_assigned_parent",
        PARENT,
        {"teacher_first_name": "A", "first_lesson": LESSON},
        "k-wh",
    )
    nid = rig.row("email")["$id"]
    body = json.dumps([{"id": "email-1", "kind": "delivered", "at": NOON.isoformat()}]).encode()
    with pytest.raises(ValueError):
        rig.email.parse_webhook({}, body)
    (ev,) = rig.email.parse_webhook({"x-fake-signature": "ok"}, body)
    rig.svc.record_delivery(nid, ev.kind, ev.occurred_at, ev.reason)
    assert rig.row("email")["status"] == "delivered"


def test_public_status_maps_the_two_virtual_states():
    assert public_status({"status": "blocked"}) == "suppressed"
    deferred = {"status": "queued", "payload": json.dumps({"meta": {"deferred": True}})}
    assert public_status(deferred) == "deferred"
    assert public_status({"status": "queued", "payload": "{}"}) == "queued"
    assert notification_owner_id({"user_id": "u9"}) == "u9"


def test_retention_hook_clears_payloads_once_and_is_chunked():
    rig = Rig()
    rig.user(PARENT)
    for i in range(5):
        rig.svc.send("session_log_published_parent", PARENT, {"lesson_time": LESSON}, f"r{i}")
        rig.clock.advance(minutes=20)
    cutoff = rig.clock.now + timedelta(days=1)
    assert expire_payloads(rig.repo, cutoff, rig.clock.now, page=4) == 4
    assert expire_payloads(rig.repo, cutoff, rig.clock.now, page=4) == 4
    assert expire_payloads(rig.repo, cutoff, rig.clock.now, page=4) == 2
    assert expire_payloads(rig.repo, cutoff, rig.clock.now, page=4) == 0
    rows = rig.repo.list("notification")
    assert all(r["deleted_at"] and json.loads(r["payload"])["vars"] == {} for r in rows)


DAY = datetime(2027, 3, 10, 9, 0, tzinfo=KAMPALA)  # 09:00 Africa/Kampala: outside quiet hours
NIGHT = datetime(2027, 3, 10, 23, 30, tzinfo=KAMPALA)  # inside quiet hours (21:00-07:00)


def test_app_binds_the_dispatch_job(settings, repo, verifier):
    dispatch._bound.clear()
    app = create_app(settings=settings, repository=repo, verifier=verifier)
    TestClient(app)
    assert dispatch._bound["service"] is app.state.notifications


def _unconfigured_send(clock):
    svc = build_notification_service(Settings(_env_file=None), InMemoryRepository(), clock=clock)
    svc.store.seed_defaults()
    svc.directory = NoDirectory()
    res = svc.send(
        "session_log_published_parent",
        Recipient("u1", "parent"),
        {"lesson_time": LESSON},
        "k-unconfigured",
    )
    return svc, res


def test_wires_no_real_channels_by_day():
    # no push/email adapter yet (N02): an operational send fails loudly, nothing is "sent".
    # The clock is injected: the real wall clock would put the suite inside quiet hours at night.
    svc, res = _unconfigured_send(lambda: DAY)
    assert {n.channel: n.status for n in res.notifications} == {
        "push": "failed",
        "in_app": "delivered",
    }
    assert svc.repo.list("notification")[0]["last_error"] == "permanent:no_device"


def test_wires_no_real_channels_at_night_push_is_deferred():
    svc, res = _unconfigured_send(lambda: NIGHT)
    assert {n.channel: n.status for n in res.notifications} == {
        "push": "deferred",
        "in_app": "delivered",
    }


def test_email_message_repr_hides_address_and_content():
    m = EmailMessage("secret@x.test", "subj", "<p>h</p>", "t", tags={"template": "x"})
    assert "secret@x.test" not in repr(m) and "subj" not in repr(m)
