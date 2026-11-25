"""N01 (#126) fix round: provider exceptions, poisoned rows, attempt cap, log hygiene."""

import json
import logging
from datetime import UTC, datetime

import pytest

from hs_core.notifications.channels import (
    FakeEmailSender,
    FakeInAppChannel,
    FakePushChannel,
    SimulatedCrash,
)
from hs_core.notifications.service import Recipient
from notify_helpers import (
    ADMIN_BACKUP,
    ADMIN_LEAD,
    CANCEL_VARS,
    LESSON,
    NIGHT,
    PARENT,
    TEACHER,
    Rig,
    all_log_text,
)

LOG = {"lesson_time": LESSON}
SECRET = "SECRET-TOKEN-9f3a user-x@leak.test https://fcm.example/send?key=abc123"
EXCEPTIONS = [
    TimeoutError(SECRET),
    ConnectionError(SECRET),
    RuntimeError(SECRET),
    ValueError(SECRET),
    OSError(SECRET),
]
EXC_IDS = [type(e).__name__ for e in EXCEPTIONS]


class RaisingPush(FakePushChannel):
    def __init__(self, exc=None, only_token=None):
        super().__init__()
        self.exc, self.only_token = exc, only_token

    def send(self, message):
        if self.exc is not None and (self.only_token in (None, message.token)):
            self.messages.append(message)
            raise self.exc
        return super().send(message)


class RaisingEmail(FakeEmailSender):
    def __init__(self, exc=None):
        super().__init__()
        self.exc = exc

    def send(self, message, *, idempotency_key):
        if self.exc is not None:
            self.calls.append(message)
            raise self.exc
        return super().send(message, idempotency_key=idempotency_key)


class RaisingInApp(FakeInAppChannel):
    def __init__(self, exc=None):
        super().__init__()
        self.exc = exc

    def send(self, message):
        if self.exc is not None:
            raise self.exc
        return super().send(message)


def rig_with(push=None, email=None, in_app=None, now=None) -> Rig:
    rig = Rig(now) if now else Rig()
    if push is not None:
        rig.push = rig.svc.push = push
    if email is not None:
        rig.email = email
        rig.svc.email.sender = email
    if in_app is not None:
        rig.in_app = rig.svc.in_app = in_app
    return rig


def run_to_exhaustion(rig: Rig, n: int | None = None) -> None:
    for _ in range(n or rig.config.max_attempts + 3):
        rig.clock.advance(minutes=30)
        rig.svc.dispatch_due()


# ---- BLOCKER 1: raising providers -------------------------------------------------------------
@pytest.mark.parametrize("exc", EXCEPTIONS, ids=EXC_IDS)
def test_raising_push_is_a_transient_failure_and_send_does_not_raise(exc):
    rig = rig_with(push=RaisingPush(exc))
    rig.user(PARENT)
    res = rig.svc.send("session_log_published_parent", PARENT, LOG, "k-rp")  # must not raise
    by = {n.channel: n.status for n in res.notifications}
    assert by == {"push": "queued", "in_app": "delivered"}
    r = rig.row("push")
    assert r["status"] == "queued" and r["attempts"] == 1
    assert r["last_error"] == f"transient:exception:{type(exc).__name__}"
    assert datetime.fromisoformat(r["scheduled_at"]) > rig.clock.now
    # recovers when the provider comes back
    rig.push.exc = None
    rig.clock.advance(minutes=5)
    assert rig.svc.dispatch_due() == 1
    assert rig.row("push")["status"] == "sent" and rig.push.deliveries == 1


@pytest.mark.parametrize("exc", EXCEPTIONS[:3], ids=EXC_IDS[:3])
def test_raising_email_is_transient_and_other_channels_still_go(exc):
    rig = rig_with(email=RaisingEmail(exc))
    rig.user(PARENT)
    res = rig.svc.send(
        "teacher_assigned_parent",
        PARENT,
        {"teacher_first_name": "Amy", "first_lesson": LESSON},
        "k-re",
    )
    by = {n.channel: n.status for n in res.notifications}
    assert by == {"push": "sent", "email": "queued", "in_app": "delivered"}
    assert rig.row("email")["last_error"] == f"transient:exception:{type(exc).__name__}"
    rig.email.exc = None
    rig.clock.advance(minutes=5)
    rig.svc.dispatch_due()
    assert rig.row("email")["status"] == "sent" and len(rig.email.sent_unique) == 1


@pytest.mark.parametrize("exc", EXCEPTIONS[:3], ids=EXC_IDS[:3])
def test_raising_in_app_is_transient(exc):
    rig = rig_with(in_app=RaisingInApp(exc))
    rig.user(PARENT)
    res = rig.svc.send("session_log_published_parent", PARENT, LOG, "k-ra")
    assert {n.channel: n.status for n in res.notifications} == {"push": "sent", "in_app": "queued"}
    assert rig.row("in_app")["attempts"] == 1
    rig.in_app.exc = None
    rig.clock.advance(minutes=5)
    rig.svc.dispatch_due()
    assert rig.row("in_app")["status"] == "delivered"


def test_raising_render_is_transient_not_a_crash(monkeypatch):
    rig = Rig()
    rig.user(PARENT)

    def boom(row):
        raise RuntimeError(SECRET)

    monkeypatch.setattr(rig.svc, "render_notification", boom)
    res = rig.svc.send("session_log_published_parent", PARENT, LOG, "k-rr")
    assert {n.status for n in res.notifications} == {"queued"}
    assert rig.row("push")["last_error"] == "transient:exception:RuntimeError"
    assert rig.push.calls_count == 0
    monkeypatch.undo()
    rig.clock.advance(minutes=5)
    rig.svc.dispatch_due()
    assert rig.row("push")["status"] == "sent"


def test_one_raising_token_does_not_stop_the_other_devices():
    rig = rig_with(push=RaisingPush(TimeoutError("x"), only_token="tok-a"))
    rig.devices.register("u_parent", "parent", "android", "tok-a")
    rig.devices.register("u_parent", "parent", "android", "tok-b")
    rig.directory.set_email("u_parent", "p@example.org")
    rig.svc.send("session_log_published_parent", PARENT, LOG, "k-2tok")
    assert rig.row("push")["status"] == "sent"  # b accepted
    assert rig.push.deliveries == 1


def test_send_retry_after_lease_expiry_does_not_raise_either():
    rig = rig_with(push=RaisingPush(TimeoutError(SECRET)))
    rig.user(PARENT)
    rig.svc.send("session_log_published_parent", PARENT, LOG, "k-rs")
    rig.clock.advance(minutes=30)
    res = rig.svc.send("session_log_published_parent", PARENT, LOG, "k-rs")  # _resume path
    assert res.duplicate and rig.row("push")["attempts"] == 2


def test_attempts_stop_at_the_maximum_and_the_row_fails_for_good():
    rig = rig_with(push=RaisingPush(TimeoutError(SECRET)))
    rig.user(PARENT)
    rig.svc.send("session_log_published_parent", PARENT, LOG, "k-cap")
    run_to_exhaustion(rig, 12)
    r = rig.row("push")
    assert r["status"] == "failed" and r["attempts"] == rig.config.max_attempts == 4
    assert r["last_error"] == "transient_exhausted"
    assert rig.push.calls_count == 4
    assert rig.svc.dispatch_due() == 0
    assert not rig.alerts.sent  # not critical: no alert


def test_critical_event_falls_back_to_email_after_a_raising_push():
    rig = rig_with(push=RaisingPush(ConnectionError(SECRET)))
    rig.user(TEACHER)
    rig.svc.send("session_cancelled_teacher", TEACHER, CANCEL_VARS, "k-cf")
    run_to_exhaustion(rig)
    assert rig.row("push", "u_teacher")["status"] == "fallback"
    assert rig.row("push", "u_teacher")["attempts"] == rig.config.max_attempts
    assert len(rig.email.sent_unique) == 1
    assert rig.row("email", "u_teacher")["status"] == "sent"


def test_critical_safeguarding_push_that_keeps_raising_fails_alerts_and_still_emails():
    rig = rig_with(push=RaisingPush(TimeoutError(SECRET)))
    rig.user(ADMIN_LEAD)
    rig.user(ADMIN_BACKUP)
    rig.svc.send_safeguarding("safeguarding_concern_admin", ADMIN_LEAD, ADMIN_BACKUP, {}, "k-sgx")
    assert len(rig.email.sent_unique) == 2  # email at once, not waiting for push
    run_to_exhaustion(rig)
    assert rig.row("push", "u_lead")["status"] == "failed"
    assert any(a.kind == "notification_delivery_failed" for a in rig.alerts.sent)


def test_critical_email_that_keeps_raising_ends_failed_with_an_alert():
    rig = rig_with(email=RaisingEmail(TimeoutError(SECRET)))
    rig.user(PARENT)
    rig.svc.send("session_cancelled_parent", PARENT, CANCEL_VARS, "k-ce")
    run_to_exhaustion(rig)
    assert rig.row("email")["status"] == "failed"
    assert any(a.kind == "notification_delivery_failed" for a in rig.alerts.sent)


def test_a_poisoned_row_does_not_block_fifty_healthy_rows_over_several_runs():
    rig = rig_with(push=RaisingPush(TimeoutError(SECRET), only_token="tok-poison"), now=NIGHT)
    poison = Recipient("u_poison", "parent")
    rig.user(poison, token="tok-poison")
    rig.svc.send("session_checked_out_parent", poison, {"time": LESSON}, "kp")  # sorts first
    healthy = [Recipient(f"u{i}", "parent") for i in range(50)]
    for h in healthy:
        rig.user(h)
        rig.svc.send("session_checked_out_parent", h, {"time": LESSON}, f"kh-{h.user_id}")
    rig.clock.now = datetime(2027, 1, 19, 4, 0, tzinfo=UTC)
    for _ in range(4):  # page 40: needs two runs even without the poisoned row
        rig.svc.dispatch_due(page=40)
        rig.clock.advance(minutes=1)
    assert rig.push.deliveries == 50
    assert {r["status"] for r in rig.rows(channel="push") if r["user_id"] != "u_poison"} == {"sent"}


def test_a_row_whose_processing_blows_up_is_isolated_in_dispatch_due():
    rig = Rig(NIGHT)
    users = [Recipient(f"u{i}", "parent") for i in range(6)]
    for u in users:
        rig.user(u)
        rig.svc.send("session_checked_out_parent", u, {"time": LESSON}, f"k-{u.user_id}")
    bad = rig.rows(user_id="u0", channel="push")[0]["$id"]

    def before(op, table, row_id, data):
        if op == "update" and row_id == bad:
            raise ConnectionError(SECRET)

    rig.repo.before = before
    rig.clock.now = datetime(2027, 1, 19, 4, 0, tzinfo=UTC)
    rig.svc.dispatch_due()  # must not raise
    assert rig.push.deliveries == 5


def test_baseexceptions_are_not_swallowed():
    for exc in (KeyboardInterrupt(), SystemExit(1), SimulatedCrash()):
        for which in ("push", "email", "in_app"):
            kw = {
                "push": {"push": RaisingPush(exc)},
                "email": {"email": RaisingEmail(exc)},
                "in_app": {"in_app": RaisingInApp(exc)},
            }[which]
            rig = rig_with(**kw)
            rig.user(PARENT)
            with pytest.raises(type(exc)):
                rig.svc.send(
                    "teacher_assigned_parent",
                    PARENT,
                    {"teacher_first_name": "A", "first_lesson": LESSON},
                    f"k-{which}-{type(exc).__name__}",
                )


def test_baseexception_in_dispatch_due_propagates():
    rig = Rig(NIGHT)
    rig.user(PARENT)
    rig.svc.send("session_checked_out_parent", PARENT, {"time": LESSON}, "k-bd")
    rig.push = rig.svc.push = RaisingPush(KeyboardInterrupt())
    rig.clock.now = datetime(2027, 1, 19, 4, 0, tzinfo=UTC)
    with pytest.raises(KeyboardInterrupt):
        rig.svc.dispatch_due()


def test_logs_alerts_and_rows_carry_only_the_exception_type(caplog):
    caplog.set_level(logging.DEBUG)
    rig = rig_with(
        push=RaisingPush(TimeoutError(SECRET)), email=RaisingEmail(ConnectionError(SECRET))
    )
    rig.user(PARENT, token="tok-LEAKME-123", email="leak-me@leak.test")
    rig.svc.send("session_cancelled_parent", PARENT, CANCEL_VARS, "k-log")
    run_to_exhaustion(rig)
    text = all_log_text(caplog) + json.dumps(rig.rows(), default=str)
    text += json.dumps([vars(a) for a in rig.alerts.sent], default=str)
    for leak in ("SECRET-TOKEN", "leak.test", "abc123", "fcm.example", "LEAKME", "leak-me"):
        assert leak not in text, leak
    assert "TimeoutError" in text or "exception:" in text
