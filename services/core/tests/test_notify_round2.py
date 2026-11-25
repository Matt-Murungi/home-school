"""N01 (#126) fix round 2: retention paging, email key stability, TTL, address hygiene, rate-limit
key, inbox renderability, catalog count."""

import json
from datetime import UTC, datetime, timedelta

import pytest

from hs_core.notifications import catalog
from hs_core.notifications.catalog import EVENTS
from hs_core.notifications.channels import (
    EmailChannel,
    FakeEmailSender,
    SimulatedCrash,
)
from hs_core.notifications.errors import MissingVariables, NaiveDatetime
from hs_core.notifications.retention import expire_payloads
from hs_core.notifications.service import Recipient, is_renderable, public_status
from jobs_helpers import HookRepository
from notify_helpers import (
    LESSON,
    NIGHT,
    NOON,
    PARENT,
    Rig,
)

LOG = {"lesson_time": LESSON}
T0 = datetime(2027, 1, 1, 0, 0, tzinfo=UTC)


# ---- BLOCKER 2: retention ---------------------------------------------------------------------
def make_rows(repo, n, start=T0, step=timedelta(minutes=1), status="sent", prefix="r"):
    for i in range(n):
        at = (start + i * step).isoformat()
        repo.create(
            "notification",
            f"{prefix}{i:04d}",
            {
                "user_id": f"u{i % 7}",
                "channel": "push",
                "template_key": "payment_received_parent",
                "template_version": 1,
                "category": "operational",
                "payload": json.dumps(
                    {"vars": {"amount": 12345 + i}, "meta": {"locale": "en", "group": 0}}
                ),
                "status": status,
                "attempts": 1,
                "sent_at": at,
                "scheduled_at": at,
                "created_at": at,
            },
        )


def drain(repo, before, now, page=40):
    calls = []
    while True:
        n = expire_payloads(repo, before, now, page=page)
        calls.append(n)
        if n == 0:
            return calls
        assert len(calls) < 100


def test_expire_payloads_reaches_every_row_not_just_the_oldest_page_times_three():
    repo = HookRepository()
    make_rows(repo, 600)
    before = T0 + timedelta(days=30)
    calls = drain(repo, before, before)
    assert calls[:-1] == [40] * 15 and calls[-1] == 0, calls
    rows = repo.list("notification")
    assert len(rows) == 600
    assert all(r["deleted_at"] for r in rows)
    assert not [r for r in rows if json.loads(r["payload"])["vars"]]


def test_expire_payloads_pages_by_cursor_never_offsets_and_never_over_100():
    repo = HookRepository()
    make_rows(repo, 450)
    seen = []
    orig = repo.list

    def spy(table, queries=None):
        seen.append([json.loads(q) if isinstance(q, str) else q for q in queries or []])
        return orig(table, queries)

    repo.list = spy
    before = T0 + timedelta(days=30)
    expire_payloads(repo, before, before, page=1000)  # one call may walk the whole table
    methods = [q["method"] for qs in seen for q in qs]
    assert "offset" not in methods and "cursorAfter" in methods
    limits = [q["values"][0] for qs in seen for q in qs if q["method"] == "limit"]
    assert limits and max(limits) <= 100
    assert all(r["deleted_at"] for r in orig("notification"))


def test_boundary_older_than_is_expired_equal_is_kept():
    repo = HookRepository()
    make_rows(repo, 3, step=timedelta(days=1))  # day 0, 1, 2
    boundary = T0 + timedelta(days=1)
    assert expire_payloads(repo, boundary, boundary) == 1  # only day 0 is older
    rows = {r["$id"]: r for r in repo.list("notification")}
    assert rows["r0000"]["deleted_at"] and not rows["r0001"].get("deleted_at")
    assert expire_payloads(repo, boundary + timedelta(microseconds=1), boundary) == 1  # r0001
    assert not rows["r0002"].get("deleted_at")


def test_rerun_is_idempotent_and_writes_nothing():
    repo = HookRepository()
    make_rows(repo, 30)
    before = T0 + timedelta(days=1)
    assert expire_payloads(repo, before, before, page=100) == 30
    repo.calls.clear()
    assert expire_payloads(repo, before, before + timedelta(hours=5), page=100) == 0
    assert not [c for c in repo.calls if c[0] in ("update", "create", "delete")]
    row = repo.get("notification", "r0000")
    assert row["deleted_at"] == before.isoformat()  # not rewritten with a later time


def test_crash_midway_resumes_and_finishes():
    repo = HookRepository()
    make_rows(repo, 250)
    before = T0 + timedelta(days=1)
    n = {"updates": 0}

    def flaky(op, table, row_id, data):
        if op == "update":
            n["updates"] += 1
            if n["updates"] == 120:
                raise ConnectionError("store went away")

    repo.before = flaky
    with pytest.raises(ConnectionError):
        expire_payloads(repo, before, before, page=1000)
    repo.before = None
    done = sum(1 for r in repo.list("notification") if r.get("deleted_at"))
    assert done == 119
    assert expire_payloads(repo, before, before, page=1000) == 250 - 119
    assert expire_payloads(repo, before, before, page=1000) == 0
    assert all(r["deleted_at"] for r in repo.list("notification"))


def test_status_and_metadata_are_kept_but_variables_are_removed():
    rig = Rig()
    rig.user(PARENT)
    rig.svc.send("payment_received_parent", PARENT, {"amount": 987654}, "k-ret")
    before = rig.clock.now + timedelta(days=1)
    row0 = rig.row("push")
    assert expire_payloads(rig.repo, before, before) == 3  # push, email, in_app
    row = rig.row("push")
    for col in ("status", "channel", "template_key", "template_version", "user_id", "attempts"):
        assert row[col] == row0[col]
    assert row["sent_at"] == row0["sent_at"] and row["deleted_at"]
    payload = json.loads(row["payload"])
    assert payload["vars"] == {}
    assert payload["meta"]["locale"] == "en" and payload["meta"]["payload_expired"] is True
    assert "987654" not in json.dumps(row)
    assert public_status(row) == "sent"


def test_rows_still_waiting_to_be_sent_are_never_wiped():
    repo = HookRepository()
    make_rows(repo, 5, status="queued", prefix="q")
    make_rows(repo, 5, status="sent", prefix="s", start=T0 + timedelta(hours=1))
    before = T0 + timedelta(days=1)
    assert expire_payloads(repo, before, before, page=100) == 5
    assert all(not r.get("deleted_at") for r in repo.list("notification") if r["$id"][0] == "q")


def test_naive_boundary_is_rejected():
    with pytest.raises(NaiveDatetime):
        expire_payloads(HookRepository(), datetime(2027, 1, 1), T0)


# ---- inbox: expired rows are not renderable ----------------------------------------------------
def test_is_renderable_false_after_expiry_and_render_would_raise():
    rig = Rig()
    rig.user(PARENT)
    rig.svc.send("payment_received_parent", PARENT, {"amount": 5000}, "k-inbox")
    row = rig.row("in_app")
    assert is_renderable(row)
    rig.svc.render_notification(row)
    before = rig.clock.now + timedelta(days=1)
    expire_payloads(rig.repo, before, before)
    row = rig.row("in_app")
    assert not is_renderable(row)
    with pytest.raises(MissingVariables):
        rig.svc.render_notification(row)


def test_is_renderable_for_odd_rows():
    assert not is_renderable({"payload": None, "deleted_at": None, "template_key": "x"})
    assert not is_renderable({"payload": "{not json", "template_key": "x"})
    assert is_renderable({"payload": json.dumps({"vars": {}, "meta": {"generic": True}})})


# ---- (a) email provider key is stable across crash retries ------------------------------------
def check_email_crash_retry_is_deduped_by_the_provider() -> None:
    rig = Rig()
    rig.user(PARENT)
    rig.email.crash_after_accept = True
    vars_ = {"teacher_first_name": "Amy", "first_lesson": LESSON}
    with pytest.raises(SimulatedCrash):
        rig.svc.send("teacher_assigned_parent", PARENT, vars_, "k-ecrash")
    rig.email.crash_after_accept = False
    rig.clock.advance(seconds=rig.config.lease_s + 5)
    rig.svc.send("teacher_assigned_parent", PARENT, vars_, "k-ecrash")
    rig.svc.send("teacher_assigned_parent", PARENT, vars_, "k-ecrash")
    assert len(rig.email.calls) == 2, "expected exactly one retry"
    keys = {m.tags["notification_id"] for m in rig.email.calls}
    assert len(rig.email.sent_unique) == 1, f"provider saw {len(rig.email.sent_unique)} keys"
    assert len(keys) == 1
    assert rig.row("email")["status"] == "sent"


def test_email_crash_after_accept_retry_uses_the_same_provider_key():
    check_email_crash_retry_is_deduped_by_the_provider()


def test_mutation_random_email_key_per_attempt_is_caught(monkeypatch):
    import uuid

    real = EmailChannel.send

    def mutated(self, to, subject, text, html, tags, key):
        return real(self, to, subject, text, html, tags, f"{key}:{uuid.uuid4().hex}")

    monkeypatch.setattr(EmailChannel, "send", mutated)
    with pytest.raises(AssertionError):
        check_email_crash_retry_is_deduped_by_the_provider()


# ---- (d) CR/LF in the address -------------------------------------------------------------------
@pytest.mark.parametrize(
    "addr",
    [
        "a@x.test\r\nBcc: evil@x.test",
        "a@x.test\nBcc: evil@x.test",
        "a@x.test\rX: y",
        "a@x.test\x00",
        "a b@x.test",
        "",
    ],
)
def test_email_channel_rejects_control_characters_in_the_address(addr):
    sender = FakeEmailSender()
    res = EmailChannel(sender).send(addr, "s", "t", "<p>t</p>", {}, "k")
    assert not res.accepted and res.failure.value == "permanent"
    assert res.detail == "invalid_address" and sender.calls == []


def test_a_directory_returning_a_crlf_address_fails_the_row_permanently_without_a_call():
    rig = Rig()
    rig.devices.register("u_parent", "parent", "android", "tok-p")
    rig.directory.set_email("u_parent", "a@x.test\r\nBcc: evil@x.test")
    rig.svc.send(
        "teacher_assigned_parent",
        PARENT,
        {"teacher_first_name": "A", "first_lesson": LESSON},
        "k-crlf",
    )
    assert rig.email.calls == []
    row = rig.row("email")
    assert row["status"] == "failed" and row["last_error"] == "permanent:invalid_address"


# ---- (b) per-template TTL -----------------------------------------------------------------------
def test_every_event_has_an_explicit_ttl_decision():
    assert len(EVENTS) == 26
    for key, ev in EVENTS.items():
        assert key in catalog.TTL_DECISIONS, key
        if ev.category == "safeguarding":
            assert ev.ttl_minutes is None  # a safeguarding alert never expires
        else:
            assert ev.ttl_minutes and ev.ttl_minutes > 0, key
    assert (
        EVENTS["session_reminder_1h_parent"].ttl_minutes < EVENTS["payment_due_parent"].ttl_minutes
    )
    assert EVENTS["session_reminder_1h_parent"].expires_at_var == "lesson_time"


def test_catalog_has_25_prd_events_and_one_sample_marketing_event():
    marketing = [k for k, e in EVENTS.items() if e.category == "marketing"]
    assert marketing == ["marketing_announcement_parent"]
    assert len(EVENTS) - len(marketing) == 25


def test_a_stale_reminder_is_expired_not_sent_when_the_job_was_down():
    rig = Rig(NIGHT)  # deferred overnight: 1h reminder is urgent so it would go; use day-before
    rig.user(PARENT)
    lesson = NIGHT + timedelta(hours=10)
    rig.svc.send("session_reminder_day_before_parent", PARENT, {"lesson_time": lesson}, "k-ttl")
    assert rig.row("push")["status"] == "queued"  # deferred to 07:00
    rig.clock.now = lesson + timedelta(hours=2)  # job was down until after the lesson
    rig.svc.dispatch_due()
    r = rig.row("push")
    assert r["status"] == "blocked" and r["last_error"] == "suppressed:expired"
    assert public_status(r) == "expired"
    assert rig.push.deliveries == 0
    assert rig.svc.dispatch_due() == 0


def test_one_hour_reminder_expires_at_the_session_start_even_with_a_long_ttl():
    rig = Rig()
    rig.user(PARENT)
    lesson = NOON + timedelta(hours=1)
    rig.push.always("transient")
    rig.svc.send("session_reminder_1h_parent", PARENT, {"lesson_time": lesson}, "k-1h")
    rig.push.always("ok")
    rig.clock.now = lesson + timedelta(minutes=1)
    rig.svc.dispatch_due()
    assert public_status(rig.row("push")) == "expired" and rig.push.deliveries == 0


def test_payment_reminder_outlives_the_session_reminder():
    rig = Rig()
    rig.user(PARENT)
    rig.push.always("transient")
    rig.svc.send("payment_due_parent", PARENT, {"amount": 5000, "due_date": NOON.date()}, "k-pd")
    rig.push.always("ok")
    rig.clock.advance(hours=6)
    rig.svc.dispatch_due()
    assert rig.row("push")["status"] == "sent"


def test_explicit_not_after_wins_over_the_default():
    rig = Rig()
    rig.user(PARENT)
    rig.push.always("transient")
    rig.svc.send(
        "payment_due_parent",
        PARENT,
        {"amount": 1, "due_date": NOON.date()},
        "k-na2",
        not_after=NOON + timedelta(minutes=10),
    )
    rig.push.always("ok")
    rig.clock.advance(minutes=30)
    rig.svc.dispatch_due()
    assert public_status(rig.row("push")) == "expired"


def test_safeguarding_never_expires():
    rig = Rig()
    lead = Recipient("u_lead", "admin")
    rig.user(lead)
    rig.push.always("transient")
    rig.svc.send("safeguarding_concern_admin", lead, {}, "k-sgttl")
    meta = json.loads(rig.row("push", "u_lead")["payload"])["meta"]
    assert "not_after" not in meta


def test_naive_not_after_is_rejected_with_a_named_error():
    rig = Rig()
    rig.user(PARENT)
    with pytest.raises(NaiveDatetime):
        rig.svc.send(
            "session_log_published_parent",
            PARENT,
            LOG,
            "k-naive",
            not_after=datetime(2027, 1, 18, 12, 0),
        )
    assert rig.rows() == []


# ---- (c) rate limit key includes the subject ---------------------------------------------------
def test_operational_events_for_different_children_are_not_rate_limited_together():
    rig = Rig()
    rig.user(PARENT)
    n = rig.config.rate_limit + 3
    for i in range(n):
        rig.svc.send(
            "payment_received_parent",
            PARENT,
            {"amount": 1000 + i},
            f"pay{i}",
            subject_id=f"child{i}",
        )
    assert rig.push.deliveries == n
    assert not [r for r in rig.rows() if r["status"] == "blocked"]


def test_same_subject_is_still_limited_and_no_subject_keeps_the_old_behaviour():
    rig = Rig()
    rig.user(PARENT)
    n = rig.config.rate_limit
    for i in range(n + 2):
        rig.svc.send(
            "payment_received_parent", PARENT, {"amount": 1}, f"same{i}", subject_id="child1"
        )
    assert rig.push.deliveries == n
    for i in range(n + 2):  # no subject given: one shared bucket (documented)
        rig.svc.send("payment_received_parent", PARENT, {"amount": 1}, f"nos{i}")
    assert rig.push.deliveries == 2 * n


def test_rate_limit_counts_every_row_in_the_window_not_just_the_first_200():
    rig = Rig()
    rig.user(PARENT)
    # 230 recent rows of ANOTHER template pad the window; the limit must still see the real count
    for i in range(230):
        rig.repo.create(
            "notification",
            f"pad{i:04d}",
            {
                "user_id": "u_parent",
                "channel": "push",
                "template_key": "session_checked_in_parent",
                "template_version": 1,
                "category": "operational",
                "payload": "{}",
                "status": "sent",
                "attempts": 1,
                "scheduled_at": (NOON - timedelta(minutes=1)).isoformat(),
                "created_at": (NOON - timedelta(minutes=5, seconds=i % 5)).isoformat(),
            },
        )
    for i in range(rig.config.rate_limit):
        rig.repo.create(
            "notification",
            f"zz{i:04d}",
            {
                "user_id": "u_parent",
                "channel": "push",
                "template_key": "session_log_published_parent",
                "template_version": 1,
                "category": "operational",
                "payload": "{}",
                "status": "sent",
                "attempts": 1,
                "scheduled_at": NOON.isoformat(),
                "created_at": (NOON - timedelta(seconds=1)).isoformat(),
            },
        )
    res = rig.svc.send("session_log_published_parent", PARENT, LOG, "k-late")
    assert {n.channel: n.status for n in res.notifications}["push"] == "suppressed"
