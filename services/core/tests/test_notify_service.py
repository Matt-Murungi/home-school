"""N01 (#126): NotificationService: acceptance criteria and edge cases."""

import json
import logging
import threading
from datetime import UTC, datetime, timedelta

import pytest

from hs_core.notifications.errors import (
    InvalidIdempotencyKey,
    MissingVariables,
    RecipientMismatch,
    UnknownEvent,
)
from hs_core.notifications.service import notification_owner_id
from notify_helpers import (
    ADMIN_BACKUP,
    ADMIN_LEAD,
    CANCEL_VARS,
    LESSON,
    NIGHT,
    NOON,
    PARENT,
    TEACHER,
    Rig,
    all_log_text,
    check_email_html_is_escaped,
    check_quiet_hours_defers_push,
    check_retry_after_crash_does_not_double_send,
    check_safeguarding_cannot_be_muted,
)

LOG = {"lesson_time": LESSON}


# ---- criterion: notification rows store status ------------------------------------------------
def test_row_per_channel_stores_status_owner_and_template_version():
    rig = Rig()
    rig.user(PARENT)
    res = rig.svc.send(
        "teacher_assigned_parent",
        PARENT,
        {"teacher_first_name": "Amy", "first_lesson": LESSON},
        "k1",
    )
    assert {n.channel for n in res.notifications} == {"push", "email", "in_app"}
    push, email, inapp = rig.row("push"), rig.row("email"), rig.row("in_app")
    assert (push["status"], email["status"], inapp["status"]) == ("sent", "sent", "delivered")
    assert push["template_key"] == "teacher_assigned_parent" and push["template_version"] == 1
    assert push["category"] == "operational" and push["user_id"] == "u_parent"
    assert push["sent_at"] and push["attempts"] == 1
    assert push["$permissions"] == ['read("user:u_parent")']
    assert notification_owner_id(push) == "u_parent"


def test_payload_holds_variables_only_for_the_template_not_rendered_content():
    rig = Rig()
    rig.user(PARENT)
    rig.svc.send(
        "teacher_assigned_parent",
        PARENT,
        {"teacher_first_name": "Amy", "first_lesson": LESSON},
        "k1",
    )
    payload = json.loads(rig.row("push")["payload"])
    assert set(payload["vars"]) == {"teacher_first_name", "first_lesson"}


# ---- criterion: operational and safeguarding cannot be muted -----------------------------------
def test_marketing_can_be_muted_per_user():
    rig = Rig()
    rig.user(PARENT)
    rig.prefs.set_marketing_muted("u_parent", True)
    res = rig.svc.send("marketing_announcement_parent", PARENT, {"headline": "New term"}, "m1")
    assert rig.push.deliveries == 0 and not rig.email.sent
    assert all(n.status == "suppressed" for n in res.notifications)
    assert {r["status"] for r in rig.rows()} == {"blocked"}
    assert rig.rows()[0]["last_error"] == "suppressed:marketing_muted"


def test_marketing_sent_when_not_muted():
    rig = Rig()
    rig.user(PARENT)
    rig.svc.send("marketing_announcement_parent", PARENT, {"headline": "New term"}, "m2")
    assert rig.push.deliveries == 1 and len(rig.email.sent) == 1


@pytest.mark.parametrize(
    "event,who,vars_",
    [
        ("session_cancelled_parent", PARENT, CANCEL_VARS),
        ("payment_failed_parent", PARENT, {"amount": 50000}),
        ("assignment_offer_teacher", TEACHER, {"subject": "Maths", "respond_within_minutes": 30}),
        ("safeguarding_concern_admin", ADMIN_LEAD, {}),
    ],
)
def test_operational_and_safeguarding_cannot_be_muted(event, who, vars_):
    rig = Rig()
    rig.user(who)
    rig.prefs.set_marketing_muted(who.user_id, True)
    rig.prefs.set_muted_channels(who.user_id, {"push", "email"})
    rig.svc.send(event, who, vars_, "k-mute")
    assert rig.push.deliveries == 1


def test_per_channel_opt_out_applies_to_marketing_only():
    rig = Rig()
    rig.user(PARENT)
    rig.prefs.set_muted_channels("u_parent", {"email"})
    rig.svc.send("marketing_announcement_parent", PARENT, {"headline": "x"}, "m3")
    assert rig.push.deliveries == 1 and not rig.email.sent
    rig.svc.send(
        "teacher_assigned_parent",
        PARENT,
        {"teacher_first_name": "A", "first_lesson": LESSON},
        "k-op",
    )
    assert len(rig.email.sent) == 1  # operational email is not opt-out-able


def test_safeguarding_cannot_be_muted_scenario():
    check_safeguarding_cannot_be_muted()


# ---- edge case: missing variable -> send blocked, error logged, alert raised ------------------
def test_missing_variable_blocks_logs_and_alerts(caplog):
    caplog.set_level(logging.INFO)
    rig = Rig()
    rig.user(PARENT)
    with pytest.raises(MissingVariables):
        rig.svc.send("session_cancelled_parent", PARENT, {"lesson_time": LESSON}, "k-miss")
    assert rig.push.deliveries == 0 and not rig.email.sent
    assert rig.rows() and all(r["status"] == "blocked" for r in rig.rows())
    assert "template:missing:reason_label" in rig.rows()[0]["last_error"]
    assert [a.kind for a in rig.alerts.sent] == ["notification_template_error"]
    text = all_log_text(caplog)
    assert "reason_label" in text and "session_cancelled_parent" in text


def test_extra_variable_is_blocked_too():
    rig = Rig()
    rig.user(PARENT)
    from hs_core.notifications.errors import ExtraVariables

    with pytest.raises(ExtraVariables):
        rig.svc.send("session_cancelled_parent", PARENT, {**CANCEL_VARS, "phone": "0700"}, "k-x")
    assert rig.push.deliveries == 0


def test_corrected_retry_after_a_blocked_send_is_delivered():
    rig = Rig()
    rig.user(PARENT)
    with pytest.raises(MissingVariables):
        rig.svc.send("session_cancelled_parent", PARENT, {"lesson_time": LESSON}, "k-fix")
    rig.svc.send("session_cancelled_parent", PARENT, CANCEL_VARS, "k-fix")
    assert rig.push.deliveries == 1


def test_safeguarding_template_error_still_sends_the_generic_alert():
    rig = Rig()
    rig.user(ADMIN_LEAD)
    rig.svc.send("safeguarding_concern_admin", ADMIN_LEAD, {"unexpected": "x"}, "k-sg-bad")
    assert rig.push.deliveries == 1 and len(rig.email.sent) == 1
    assert rig.alerts.sent and rig.alerts.sent[0].kind == "notification_template_error"
    assert "unexpected" not in rig.email.sent[0].text


def test_unknown_event_wrong_audience_and_bad_idempotency_key():
    rig = Rig()
    with pytest.raises(UnknownEvent):
        rig.svc.send("nope", PARENT, {}, "k")
    with pytest.raises(RecipientMismatch):
        rig.svc.send("payout_sent_teacher", PARENT, {"amount": 1}, "k")
    for bad in ("", " ", "x" * 121, "a\nb"):
        with pytest.raises(InvalidIdempotencyKey):
            rig.svc.send("payout_sent_teacher", TEACHER, {"amount": 1}, bad)


# ---- idempotency ------------------------------------------------------------------------------
def test_same_key_is_one_notification():
    rig = Rig()
    rig.user(PARENT)
    a = rig.svc.send("session_cancelled_parent", PARENT, CANCEL_VARS, "same")
    b = rig.svc.send("session_cancelled_parent", PARENT, CANCEL_VARS, "same")
    assert b.duplicate and not a.duplicate
    assert len(rig.rows()) == len(a.notifications)
    assert rig.push.deliveries == 1 and len(rig.email.sent_unique) == 1


def test_different_keys_or_recipients_are_distinct():
    rig = Rig()
    rig.user(PARENT)
    rig.svc.send("session_log_published_parent", PARENT, LOG, "k1")
    rig.svc.send("session_log_published_parent", PARENT, LOG, "k2")
    assert rig.push.deliveries == 2


def test_concurrent_duplicates_send_once():
    rig = Rig()
    rig.user(PARENT)
    barrier = threading.Barrier(16)

    def go():
        barrier.wait()
        rig.svc.send("session_log_published_parent", PARENT, LOG, "race")

    ts = [threading.Thread(target=go) for _ in range(16)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert rig.push.deliveries == 1 and len(rig.rows(channel="push")) == 1


def test_retry_after_crash_does_not_double_send():
    check_retry_after_crash_does_not_double_send()


def test_crash_between_row_claim_and_send_is_resumed_by_a_retry():
    rig = Rig()
    rig.user(PARENT)
    rig.push.crash_before_accept = True
    from hs_core.notifications.channels import SimulatedCrash

    with pytest.raises(SimulatedCrash):
        rig.svc.send("session_log_published_parent", PARENT, LOG, "k-c2")
    rig.push.crash_before_accept = False
    rig.clock.advance(seconds=rig.config.lease_s + 1)
    rig.svc.send("session_log_published_parent", PARENT, LOG, "k-c2")
    assert rig.push.deliveries == 1 and rig.row("push")["status"] == "sent"


def test_retry_inside_a_live_lease_does_not_send_again():
    rig = Rig()
    rig.user(PARENT)
    rig.push.crash_after_accept = True
    from hs_core.notifications.channels import SimulatedCrash

    with pytest.raises(SimulatedCrash):
        rig.svc.send("session_log_published_parent", PARENT, LOG, "k-c3")
    rig.push.crash_after_accept = False
    res = rig.svc.send("session_log_published_parent", PARENT, LOG, "k-c3")  # lease still live
    assert rig.push.calls_count == 1 and res.duplicate


# ---- quiet hours ------------------------------------------------------------------------------
def test_quiet_hours_defer_non_urgent_push_through_the_dispatch_job():
    check_quiet_hours_defers_push()


def test_deferred_push_goes_out_at_0700_by_the_dispatch_job_once():
    rig = Rig(NIGHT)
    rig.user(PARENT)
    rig.svc.send("session_log_published_parent", PARENT, LOG, "k-q")
    assert rig.svc.dispatch_due() == 0
    rig.clock.now = datetime(2027, 1, 19, 3, 59, 59, tzinfo=UTC)
    assert rig.svc.dispatch_due() == 0 and rig.push.deliveries == 0
    rig.clock.now = datetime(2027, 1, 19, 4, 0, 0, tzinfo=UTC)
    assert rig.svc.dispatch_due() == 1
    assert rig.svc.dispatch_due() == 0
    assert rig.push.deliveries == 1 and rig.row("push")["status"] == "sent"


def test_quiet_hours_do_not_defer_email_in_app_urgent_or_critical():
    rig = Rig(NIGHT)
    rig.user(PARENT)
    rig.user(TEACHER)
    rig.svc.send(
        "teacher_assigned_parent", PARENT, {"teacher_first_name": "A", "first_lesson": LESSON}, "k1"
    )
    assert len(rig.email.sent) == 1 and rig.row("in_app")["status"] == "delivered"
    assert rig.row("push")["status"] == "queued"  # operational push waits
    rig.svc.send(
        "assignment_offer_teacher",
        TEACHER,
        {"subject": "Maths", "respond_within_minutes": 30},
        "k2",
    )  # urgent
    rig.svc.send("session_cancelled_parent", PARENT, CANCEL_VARS, "k3")  # critical
    assert rig.push.deliveries == 2


def test_safeguarding_is_never_deferred_in_quiet_hours():
    rig = Rig(NIGHT)
    rig.user(ADMIN_LEAD)
    rig.svc.send("safeguarding_concern_admin", ADMIN_LEAD, {}, "k-sg-night")
    assert rig.push.deliveries == 1 and len(rig.email.sent) == 1


def test_not_after_expired_while_deferred_is_suppressed_not_sent_late():
    rig = Rig(NIGHT)
    rig.user(PARENT)
    rig.svc.send(
        "session_reminder_day_before_parent",
        PARENT,
        LOG,
        "k-na",
        not_after=NIGHT + timedelta(hours=2),
    )
    rig.clock.now = datetime(2027, 1, 19, 4, 0, tzinfo=UTC)
    rig.svc.dispatch_due()
    assert rig.push.deliveries == 0
    assert rig.row("push")["status"] == "blocked"
    assert rig.row("push")["last_error"] == "suppressed:expired"


# ---- critical: safeguarding at once to lead and backup, escalation hooks ----------------------
def test_safeguarding_goes_to_lead_and_backup_on_push_and_email_together():
    rig = Rig()
    rig.user(ADMIN_LEAD)
    rig.user(ADMIN_BACKUP)
    res = rig.svc.send_safeguarding(
        "safeguarding_concern_admin", ADMIN_LEAD, ADMIN_BACKUP, {}, "case-1"
    )
    assert rig.push.deliveries == 2 and len(rig.email.sent_unique) == 2
    assert {m.to for m in rig.email.sent} == {"u_lead@example.org", "u_backup@example.org"}
    assert len(res.notifications) >= 4
    assert len(rig.escalation.armed) == 1
    arm = rig.escalation.armed[0]
    assert (arm["lead_id"], arm["backup_id"]) == ("u_lead", "u_backup")
    assert arm["after"] == timedelta(minutes=rig.config.escalate_after_min)


def test_safeguarding_is_idempotent_and_an_email_failure_does_not_block_push():
    rig = Rig()
    rig.user(ADMIN_LEAD)
    rig.user(ADMIN_BACKUP)
    rig.email.script("provider_down")
    rig.svc.send_safeguarding("safeguarding_concern_admin", ADMIN_LEAD, ADMIN_BACKUP, {}, "c2")
    rig.svc.send_safeguarding("safeguarding_concern_admin", ADMIN_LEAD, ADMIN_BACKUP, {}, "c2")
    assert rig.push.deliveries == 2
    assert len(rig.escalation.armed) == 1  # armed once per group id


def test_safeguarding_without_a_backup_alerts():
    rig = Rig()
    rig.user(ADMIN_LEAD)
    rig.svc.send_safeguarding("safeguarding_concern_admin", ADMIN_LEAD, None, {}, "c3")
    assert rig.push.deliveries == 1
    assert [a.kind for a in rig.alerts.sent] == ["safeguarding_no_backup"]


def test_acknowledge_is_passed_to_the_escalation_hook():
    rig = Rig()
    rig.svc.acknowledge("case-1", "u_lead")
    assert rig.escalation.acknowledged == [("case-1", "u_lead")]


# ---- fallback and failures --------------------------------------------------------------------
def test_push_permanent_failure_for_critical_event_falls_back_to_email():
    rig = Rig()
    rig.user(TEACHER)
    rig.push.script("permanent")
    rig.svc.send("session_cancelled_teacher", TEACHER, CANCEL_VARS, "k-fb")
    assert rig.row("push", "u_teacher")["status"] == "fallback"
    em = rig.row("email", "u_teacher")
    assert em["status"] == "sent" and em["dedupe_key"].endswith(":fallback")
    assert len(rig.email.sent) == 1


def test_critical_with_no_registered_device_falls_back_to_email():
    rig = Rig()
    rig.directory.set_email("u_teacher", "t@example.org")
    rig.svc.send("session_cancelled_teacher", TEACHER, CANCEL_VARS, "k-nodev")
    assert rig.row("push", "u_teacher")["status"] == "fallback"
    assert len(rig.email.sent) == 1


def test_non_critical_push_failure_does_not_fall_back():
    rig = Rig()
    rig.user(PARENT)
    rig.push.script("permanent")
    rig.svc.send("session_log_published_parent", PARENT, LOG, "k-nf")
    assert rig.row("push")["status"] == "failed" and not rig.email.sent


def test_parallel_email_event_does_not_create_a_second_fallback_email():
    rig = Rig()
    rig.user(PARENT)
    rig.push.script("permanent")
    rig.svc.send("session_cancelled_parent", PARENT, CANCEL_VARS, "k-par")
    assert len(rig.rows(channel="email")) == 1 and len(rig.email.sent) == 1
    assert rig.row("push")["status"] == "failed"


def test_transient_failure_is_retried_by_the_dispatch_job_with_backoff_then_sent():
    rig = Rig()
    rig.user(PARENT)
    rig.push.script("transient", "transient")
    rig.svc.send("session_log_published_parent", PARENT, LOG, "k-tr")
    r = rig.row("push")
    assert r["status"] == "queued" and r["attempts"] == 1 and r["last_error"] == "transient"
    assert datetime.fromisoformat(r["scheduled_at"]) > rig.clock.now
    assert rig.svc.dispatch_due() == 0  # backoff not elapsed
    rig.clock.advance(minutes=2)
    rig.svc.dispatch_due()
    assert rig.row("push")["attempts"] == 2
    rig.clock.advance(minutes=10)
    rig.svc.dispatch_due()
    assert rig.row("push")["status"] == "sent" and rig.push.deliveries == 1


def test_transient_failures_end_as_failed_after_max_attempts_and_critical_falls_back():
    rig = Rig()
    rig.user(TEACHER)
    rig.push.always("transient")
    rig.svc.send("session_cancelled_teacher", TEACHER, CANCEL_VARS, "k-max")
    for _ in range(rig.config.max_attempts + 2):
        rig.clock.advance(minutes=30)
        rig.svc.dispatch_due()
    assert rig.row("push", "u_teacher")["status"] == "fallback"
    assert len(rig.email.sent) == 1


def test_unregistered_token_is_revoked_and_other_devices_still_get_the_push():
    rig = Rig()
    rig.devices.register("u_parent", "parent", "android", "dead-token")
    rig.devices.register("u_parent", "parent", "ios", "live-token")
    rig.directory.set_email("u_parent", "p@example.org")
    rig.push.unregistered_tokens.add("dead-token")
    rig.svc.send("session_log_published_parent", PARENT, LOG, "k-un")
    assert [t.token for t in rig.devices.active_tokens("u_parent")] == ["live-token"]
    assert rig.row("push")["status"] == "sent" and rig.push.deliveries == 1


def test_all_tokens_unregistered_cleans_up_and_fails_permanently():
    rig = Rig()
    rig.user(PARENT, token="dead")
    rig.push.unregistered_tokens.add("dead")
    rig.svc.send("session_log_published_parent", PARENT, LOG, "k-un2")
    assert rig.devices.active_tokens("u_parent") == []
    assert rig.row("push")["status"] == "failed"
    assert rig.row("push")["last_error"] == "permanent:no_device"


def test_email_permanent_failure_for_critical_event_raises_an_alert():
    rig = Rig()
    rig.user(PARENT)
    rig.email.script("invalid_address")
    rig.svc.send("payment_failed_parent", PARENT, {"amount": 5000}, "k-ef")
    assert rig.row("email")["status"] == "failed"
    assert "notification_delivery_failed" in [a.kind for a in rig.alerts.sent]


def test_user_without_email_address_fails_email_row_without_calling_provider():
    rig = Rig()
    rig.devices.register("u_parent", "parent", "android", "t")
    rig.svc.send(
        "teacher_assigned_parent",
        PARENT,
        {"teacher_first_name": "A", "first_lesson": LESSON},
        "k-noemail",
    )
    assert rig.row("email")["status"] == "failed" and not rig.email.calls
    assert rig.row("email")["last_error"] == "permanent:no_email"


# ---- delivery receipts ------------------------------------------------------------------------
def test_record_delivery_transitions_and_is_idempotent_and_order_safe():
    rig = Rig()
    rig.user(PARENT)
    rig.svc.send(
        "teacher_assigned_parent",
        PARENT,
        {"teacher_first_name": "A", "first_lesson": LESSON},
        "k-rd",
    )
    nid = rig.row("email")["$id"]
    t = NOON + timedelta(minutes=1)
    rig.svc.record_delivery(nid, "delivered", t)
    rig.svc.record_delivery(nid, "delivered", t)
    assert rig.row("email")["status"] == "delivered"
    rig.svc.record_delivery(nid, "bounced", t + timedelta(minutes=1), "mailbox full")
    assert rig.row("email")["status"] == "failed"
    rig.svc.record_delivery(nid, "delivered", t + timedelta(minutes=2))  # late, must not reopen
    assert rig.row("email")["status"] == "failed"


def test_soft_bounce_and_complaint_do_not_fail_the_row_but_are_recorded():
    rig = Rig()
    rig.user(PARENT)
    rig.svc.send(
        "teacher_assigned_parent",
        PARENT,
        {"teacher_first_name": "A", "first_lesson": LESSON},
        "k-sb",
    )
    nid = rig.row("email")["$id"]
    rig.svc.record_delivery(nid, "bounced_soft", NOON)
    assert rig.row("email")["status"] == "sent"
    rig.svc.record_delivery(nid, "complained", NOON)
    meta = json.loads(rig.row("email")["payload"])["meta"]
    assert meta["complained"] is True and "soft_bounces" in meta


def test_hard_bounce_of_a_critical_email_alerts():
    rig = Rig()
    rig.user(PARENT)
    rig.svc.send("payment_failed_parent", PARENT, {"amount": 5000}, "k-hb")
    rig.svc.record_delivery(rig.row("email")["$id"], "bounced", NOON, "invalid")
    assert "notification_delivery_failed" in [a.kind for a in rig.alerts.sent]


def test_record_delivery_unknown_id_and_kind():
    rig = Rig()
    from hs_core.repository import NotFound

    with pytest.raises(NotFound):
        rig.svc.record_delivery("missing", "delivered", NOON)
    rig.user(PARENT)
    rig.svc.send("session_log_published_parent", PARENT, LOG, "k")
    with pytest.raises(ValueError):
        rig.svc.record_delivery(rig.row("push")["$id"], "exploded", NOON)


# ---- rate limit -------------------------------------------------------------------------------
def test_burst_control_per_recipient_and_template_for_non_critical_only():
    rig = Rig()
    rig.user(PARENT)
    n = rig.config.rate_limit
    for i in range(n + 3):
        rig.svc.send("session_log_published_parent", PARENT, LOG, f"b{i}")
    assert rig.push.deliveries == n
    limited = [r for r in rig.rows(channel="push") if r["status"] == "blocked"]
    assert len(limited) == 3 and limited[0]["last_error"] == "suppressed:rate_limited"
    # another recipient and another template are unaffected
    rig.user(TEACHER)
    rig.svc.send("session_disputed_teacher", TEACHER, LOG, "o1")
    rig.svc.send("session_checked_in_parent", PARENT, {"time": LESSON}, "o2")
    assert rig.push.deliveries == n + 2
    # critical is never rate limited
    for i in range(n + 3):
        rig.svc.send("session_cancelled_parent", PARENT, CANCEL_VARS, f"c{i}")
    assert rig.push.deliveries == n + 2 + n + 3
    # the window slides
    rig.clock.advance(seconds=rig.config.rate_window_s + 1)
    rig.svc.send("session_log_published_parent", PARENT, LOG, "after")
    assert rig.push.deliveries == n + 2 + n + 3 + 1


# ---- PII-safe logging and rendering ----------------------------------------------------------
def test_logs_contain_ids_only_never_variables_tokens_or_addresses(caplog):
    caplog.set_level(logging.DEBUG)
    rig = Rig()
    rig.user(PARENT, token="TOKEN-SECRET-123", email="amy.parent@example.org")
    rig.push.script("transient")
    rig.svc.send(
        "teacher_assigned_parent",
        PARENT,
        {"teacher_first_name": "Zainabu-Unique", "first_lesson": LESSON},
        "k-log",
    )
    rig.email.script("rejected")
    rig.svc.send(
        "teacher_assigned_parent",
        PARENT,
        {"teacher_first_name": "Zainabu-Unique", "first_lesson": LESSON},
        "k-log2",
    )
    with pytest.raises(MissingVariables):
        rig.svc.send(
            "teacher_assigned_parent", PARENT, {"teacher_first_name": "Zainabu-Unique"}, "k-log3"
        )
    text = all_log_text(caplog)
    for secret in ("Zainabu-Unique", "TOKEN-SECRET-123", "amy.parent@example.org", "example.org"):
        assert secret not in text
    assert rig.rows()  # something was logged and sent
    assert "notification" in text


def test_html_email_is_escaped_plain_text_is_not():
    check_email_html_is_escaped()


def test_email_always_has_both_html_and_text_and_tags_carry_ids_only():
    rig = Rig()
    rig.user(PARENT)
    rig.svc.send(
        "teacher_assigned_parent",
        PARENT,
        {"teacher_first_name": "Amy", "first_lesson": LESSON},
        "k-em",
    )
    m = rig.email.sent[-1]
    assert m.html and m.text and m.subject and "\n" not in m.subject
    assert set(m.tags) == {"template", "notification_id"}
    assert m.to == "u_parent@example.org"


def test_push_payload_has_deep_link_and_no_extra_data():
    rig = Rig()
    rig.user(PARENT)
    rig.svc.send(
        "session_log_published_parent", PARENT, LOG, "k-dl", deep_link="homeschool://sessions/s1"
    )
    m = rig.push.messages[-1]
    assert m.data == {
        "notification_id": rig.row("push")["$id"],
        "deep_link": "homeschool://sessions/s1",
    }
    with pytest.raises(ValueError):
        rig.svc.send(
            "session_log_published_parent",
            PARENT,
            LOG,
            "k-dl2",
            deep_link="https://evil.example/phish",
        )


def test_push_text_is_truncated_to_the_channel_limit():
    rig = Rig()
    rig.user(PARENT)
    rig.svc.send(
        "session_cancelled_parent",
        PARENT,
        {"lesson_time": LESSON, "reason_label": "R" * 200},
        "k-long",
    )
    m = rig.push.messages[-1]
    assert len(m.title) <= 65 and len(m.body) <= 240


# ---- misc -------------------------------------------------------------------------------------
def test_render_notification_for_the_inbox_uses_the_pinned_template_version():
    rig = Rig()
    rig.user(PARENT)
    rig.svc.send("session_log_published_parent", PARENT, LOG, "k-inb")
    row = rig.row("in_app")
    first = rig.svc.render_notification(row)
    from hs_core.notifications.engine import VarSpec
    from hs_core.notifications.templates import Actor, Channel

    rig.store.publish(
        Actor("a", ("superadmin",)),
        key="session_log_published_parent",
        channel=Channel.IN_APP,
        locale="en",
        title="Changed",
        body="Changed {{lesson_time}}",
        category="operational",
        variables=(VarSpec("lesson_time", "datetime"),),
    )
    assert rig.svc.render_notification(row) == first


def test_dispatch_due_is_chunked_and_idempotent():
    rig = Rig(NIGHT)
    for i in range(7):
        rig.user(Recipient_(i))
        rig.svc.send("session_checked_out_parent", Recipient_(i), {"time": LESSON}, f"k{i}")
    rig.clock.now = datetime(2027, 1, 19, 4, 0, tzinfo=UTC)
    assert rig.svc.dispatch_due(page=3) == 3
    assert rig.svc.dispatch_due(page=3) == 3
    assert rig.svc.dispatch_due(page=3) == 1
    assert rig.svc.dispatch_due(page=3) == 0
    assert rig.push.deliveries == 7


def Recipient_(i):
    from hs_core.notifications.service import Recipient

    return Recipient(f"u{i}", "parent")
