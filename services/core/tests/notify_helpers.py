"""N01 test helpers: a fully faked NotificationService, a fake clock, scenario checks shared by
the normal tests and the mutation tests."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from hs_core.jobs.alerts import FakeAlertSink
from hs_core.notifications.channels import (
    FakeEmailSender,
    FakeInAppChannel,
    FakePushChannel,
)
from hs_core.notifications.config import NotificationConfig
from hs_core.notifications.devices import DeviceRegistry
from hs_core.notifications.preferences import FakePreferenceStore
from hs_core.notifications.service import (
    FakeContactDirectory,
    FakeEscalation,
    NotificationService,
    Recipient,
)
from hs_core.notifications.templates import TemplateStore
from jobs_helpers import FakeClock, HookRepository

KAMPALA_OFFSET = timedelta(hours=3)
# 2027-01-18 (Monday) 09:00 UTC = 12:00 Kampala: outside quiet hours.
NOON = datetime(2027, 1, 18, 9, 0, tzinfo=UTC)
# 22:30 Kampala = 19:30 UTC.
NIGHT = datetime(2027, 1, 18, 19, 30, tzinfo=UTC)

PARENT = Recipient("u_parent", "parent")
TEACHER = Recipient("u_teacher", "teacher")
ADMIN_LEAD = Recipient("u_lead", "admin")
ADMIN_BACKUP = Recipient("u_backup", "admin")

LESSON = datetime(2027, 1, 19, 14, 0, tzinfo=UTC)
CANCEL_VARS = {"lesson_time": LESSON, "reason_label": "Teacher unavailable"}


class Rig:
    """Everything wired with fakes. `rig.svc` is the service under test."""

    def __init__(self, now: datetime = NOON, config: NotificationConfig | None = None) -> None:
        self.repo = HookRepository()
        self.clock = FakeClock(now)
        self.alerts = FakeAlertSink()
        self.push = FakePushChannel()
        self.email = FakeEmailSender()
        self.in_app = FakeInAppChannel()
        self.directory = FakeContactDirectory()
        self.escalation = FakeEscalation()
        self.prefs = FakePreferenceStore()
        self.store = TemplateStore(self.repo, clock=self.clock)
        self.store.seed_defaults()
        self.devices = DeviceRegistry(self.repo, clock=self.clock)
        self.config = config or NotificationConfig()
        self.svc = NotificationService(
            self.repo,
            store=self.store,
            push=self.push,
            email_sender=self.email,
            in_app=self.in_app,
            directory=self.directory,
            devices=self.devices,
            preferences=self.prefs,
            config=self.config,
            clock=self.clock,
            alerts=self.alerts,
            escalation=self.escalation,
        )

    def user(self, rec: Recipient, *, token: str | None = None, email: str | None = None):
        """Give a user a device token and an email address."""
        self.devices.register(rec.user_id, "parent", "android", token or f"tok-{rec.user_id}")
        self.directory.set_email(rec.user_id, email or f"{rec.user_id}@example.org")

    def rows(self, **where) -> list[dict]:
        rows = self.repo.list("notification")
        return [r for r in rows if all(r.get(k) == v for k, v in where.items())]

    def row(self, channel: str, user: str = "u_parent") -> dict:
        found = self.rows(channel=channel, user_id=user)
        assert len(found) == 1, found
        return found[0]


def all_log_text(caplog: object) -> str:
    return "\n".join(
        f"{r.getMessage()} {sorted(r.__dict__.items(), key=str)}"
        for r in caplog.records  # type: ignore[attr-defined]
    )


def quiet_logger() -> logging.Logger:
    return logging.getLogger("hs_core.notifications")


# ---- scenario checks (raise AssertionError); used by tests AND by the mutation tests ----------
def check_quiet_hours_defers_push() -> None:
    rig = Rig(NIGHT)
    rig.user(PARENT)
    res = rig.svc.send("session_log_published_parent", PARENT, {"lesson_time": LESSON}, "k-quiet-1")
    assert rig.push.deliveries == 0, "non-urgent push must not be sent in quiet hours"
    push = rig.row("push")
    assert push["status"] == "queued"
    assert datetime.fromisoformat(push["scheduled_at"]) == datetime(2027, 1, 19, 4, 0, tzinfo=UTC)
    assert any(n.deferred for n in res.notifications)


def check_safeguarding_cannot_be_muted() -> None:
    rig = Rig()
    rig.user(ADMIN_LEAD)
    rig.prefs.set_marketing_muted(ADMIN_LEAD.user_id, True)
    rig.svc.send("safeguarding_concern_admin", ADMIN_LEAD, {}, "k-sg-mute")
    assert rig.push.deliveries == 1 and len(rig.email.sent_unique) == 1


def check_retry_after_crash_does_not_double_send() -> None:
    from hs_core.notifications.channels import SimulatedCrash

    rig = Rig()
    rig.user(PARENT)
    rig.push.crash_after_accept = True
    try:
        rig.svc.send("session_log_published_parent", PARENT, {"lesson_time": LESSON}, "k-crash")
        raise AssertionError("expected the injected crash")
    except SimulatedCrash:
        pass
    rig.push.crash_after_accept = False
    rig.clock.advance(seconds=rig.config.lease_s + 5)
    rig.svc.send("session_log_published_parent", PARENT, {"lesson_time": LESSON}, "k-crash")
    rig.svc.send("session_log_published_parent", PARENT, {"lesson_time": LESSON}, "k-crash")
    assert rig.push.deliveries == 1, f"delivered {rig.push.deliveries} times"
    assert len(rig.rows(channel="push")) == 1
    assert rig.row("push")["status"] == "sent"


def check_email_html_is_escaped() -> None:
    rig = Rig()
    rig.user(PARENT)
    evil = "<script>alert(1)</script>&\"'"
    rig.svc.send(
        "teacher_assigned_parent",
        PARENT,
        {"teacher_first_name": evil, "first_lesson": LESSON},
        "k-html",
    )
    msg = rig.email.sent[-1]
    assert "<script>" not in msg.html and "&lt;script&gt;" in msg.html
    assert evil in msg.text  # the plain-text part is not HTML: no escaping there
