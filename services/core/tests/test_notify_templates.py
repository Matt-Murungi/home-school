"""N01 (#126): template store, versions, super-admin edits, seeded defaults, copy hygiene."""

import re
from datetime import UTC, datetime

import pytest

from hs_core.notifications import catalog, engine
from hs_core.notifications.errors import (
    ExtraVariables,
    MissingVariables,
    PermissionDenied,
    TemplateSyntaxError,
    TemplateTooLong,
    UnknownTemplate,
)
from hs_core.notifications.templates import Actor, Channel, render_template, sample_values
from notify_helpers import Rig

S = engine.VarSpec
ADMIN = Actor("u_admin", ("opsadmin", "superadmin"))
OPS = Actor("u_ops", ("opsadmin",))


@pytest.fixture
def rig():
    return Rig()


def publish(rig, actor=ADMIN, **kw):
    args = dict(
        key="session_cancelled_parent",
        channel=Channel.PUSH,
        locale="en",
        title="Cancelled",
        body="Lesson {{lesson_time}} cancelled: {{reason_label}}.",
        variables=(S("lesson_time", "datetime"), S("reason_label", "str")),
        category="operational",
    )
    args.update(kw)
    return rig.store.publish(actor, **args)


def test_super_admin_can_publish_a_new_version_and_old_one_stays_readable(rig):
    before = rig.store.get_active("session_cancelled_parent", Channel.PUSH)
    new = publish(rig)
    assert new.version == before.version + 1
    assert rig.store.get_active("session_cancelled_parent", Channel.PUSH).version == new.version
    old = rig.store.get_version("session_cancelled_parent", Channel.PUSH, "en", before.version)
    assert old.body == before.body and old.active is False
    assert new.created_by_id == "u_admin"


def test_only_super_admin_may_edit(rig):
    with pytest.raises(PermissionDenied):
        publish(rig, actor=OPS)
    with pytest.raises(PermissionDenied):
        publish(rig, actor=Actor("u_p", ("parent",)))
    assert rig.store.get_active("session_cancelled_parent", Channel.PUSH).version == 1


def test_edits_are_audited(rig):
    seen = []
    rig.store.audit = seen.append
    publish(rig)
    assert seen and seen[0]["action"] == "notification_template.publish"
    assert seen[0]["actor_id"] == "u_admin" and "body" not in seen[0]


def test_publish_validates_placeholders_declared_and_used(rig):
    with pytest.raises(MissingVariables):  # placeholder not declared
        publish(rig, body="Hi {{lesson_time}} {{reason_label}} {{phone}}")
    with pytest.raises(ExtraVariables):  # declared but unused (would carry data for nothing)
        publish(rig, body="Hi {{lesson_time}}")
    with pytest.raises(TemplateSyntaxError):
        publish(rig, body="{{ lesson_time.__class__ }} {{reason_label}}")


def test_publish_cannot_change_the_variable_set_of_a_catalog_event(rig):
    with pytest.raises(ExtraVariables):
        publish(
            rig,
            body="{{lesson_time}} {{reason_label}} {{parent_phone}}",
            variables=(
                S("lesson_time", "datetime"),
                S("reason_label", "str"),
                S("parent_phone", "str", pii=True),
            ),
        )


def test_length_limits_per_channel(rig):
    with pytest.raises(TemplateTooLong):
        publish(rig, title="T" * 66)
    with pytest.raises(TemplateTooLong):
        publish(rig, body="x" * 241 + "{{lesson_time}}{{reason_label}}")
    with pytest.raises(TemplateTooLong):
        publish(rig, channel=Channel.EMAIL, title="S" * 151)


def test_email_subject_must_be_single_line(rig):
    with pytest.raises(TemplateSyntaxError):
        publish(rig, channel=Channel.EMAIL, title="Hi\nBcc: x@evil.test")


def test_unknown_template_and_locale_fallback_to_english(rig):
    with pytest.raises(UnknownTemplate):
        rig.store.get_active("nope", Channel.PUSH)
    spec = rig.store.get_active("session_cancelled_parent", Channel.PUSH, "lg")  # Luganda later
    assert spec.locale == "en"


def test_concurrent_publish_gets_distinct_versions(rig):
    import threading

    out, errs = [], []

    def go():
        try:
            out.append(publish(rig).version)
        except Exception as e:  # noqa: BLE001
            errs.append(e)

    ts = [threading.Thread(target=go) for _ in range(8)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert not errs and sorted(out) == list(range(2, 10))


def test_seed_is_idempotent_and_creates_every_catalog_template(rig):
    n = len(rig.repo.list("notification_template"))
    rig.store.seed_defaults()
    assert len(rig.repo.list("notification_template")) == n
    for ev in catalog.EVENTS.values():
        for ch in ev.template_channels:
            assert rig.store.get_active(ev.key, ch).version == 1


def test_every_seeded_template_renders_without_missing_variables(rig):
    """'Test send of every template renders without missing variables.'"""
    count = 0
    for ev in catalog.EVENTS.values():
        for ch in ev.template_channels:
            spec = rig.store.get_active(ev.key, ch)
            r = render_template(spec, sample_values(spec.variables))
            assert r.title and r.body
            assert "{{" not in r.title + r.body
            if ch is Channel.EMAIL:
                assert r.html and r.html.startswith("<")
            count += 1
    assert count >= 60


def test_dry_run_reports_all_problems_for_admin_test_send(rig):
    spec = rig.store.get_active("session_cancelled_parent", Channel.PUSH)
    assert rig.store.dry_run(spec.key, spec.channel).body
    with pytest.raises(MissingVariables):
        render_template(spec, {"lesson_time": datetime(2027, 1, 1, tzinfo=UTC)})


def test_matrix_events_exist_with_expected_channels():
    e = catalog.EVENTS
    assert {c.value for c in e["teacher_assigned_parent"].channels} == {"push", "email", "in_app"}
    assert {c.value for c in e["assignment_offer_teacher"].channels} == {"push", "in_app"}
    assert {c.value for c in e["safeguarding_concern_admin"].channels} >= {"push", "email"}
    for k in (
        "matching_failed_admin",
        "no_substitute_admin",
        "payout_sent_teacher",
        "document_expiring_teacher",
        "tier_changed_teacher",
        "payment_failed_parent",
        "session_disputed_teacher",
        "session_confirm_request_parent",
    ):
        assert k in e
    assert e["safeguarding_concern_admin"].category == "safeguarding"
    assert e["safeguarding_concern_admin"].priority == "critical"
    for k in ("substitute_assigned_parent", "session_cancelled_parent", "payment_failed_parent"):
        assert e[k].priority == "critical" and e[k].fallback_email
    assert e["marketing_announcement_parent"].priority == "marketing"


def test_copy_is_marked_draft_and_avoids_the_banned_word_and_pii():
    assert "draft for owner review" in catalog.COPY_STATUS
    for t in catalog.default_templates():
        txt = (t.title + " " + t.body).lower()
        assert "homeschool" not in txt
        assert not re.search(r"\d{9,}", txt)
        assert t.created_by_id == "system:seed-draft"
    # lock-screen safety: no push copy names the child
    for t in catalog.default_templates():
        assert "child_name" not in {v.name for v in t.variables}
    sg = [t for t in catalog.default_templates() if t.key == "safeguarding_concern_admin"]
    assert all(not t.variables for t in sg)  # minimal detail in a mailbox (ADR 0005)
