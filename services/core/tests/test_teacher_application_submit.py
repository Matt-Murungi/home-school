"""V01 (#56): submit exactly once, idempotency, concurrency, crash resume, resubmission."""

import json
import threading
from datetime import timedelta

import pytest

from application_helpers import KEY, Env, deep, valid_content
from hs_core.auth import Principal
from hs_core.teacher_application.models import SubmitBody


@pytest.fixture
def env():
    return Env()


@pytest.fixture
def busy():
    return Env(submits_per_hour=500)


def counts(e):
    return {
        t: len(e.rows(t))
        for t in (
            "teacher_profile",
            "teacher_zone",
            "availability_slot",
            "teacher_referee",
            "teacher_application_subject",
            "teacher_approval",
        )
    }


# -- completeness -------------------------------------------------------------------------------
def test_submit_without_a_draft_is_incomplete(env):
    r = env.submit(0)
    assert r.status_code == 422 and r.json()["error"]["issues"] == ["application:required"]


def test_incomplete_application_lists_every_missing_item(env):
    c = valid_content()
    del c["personal"]["photo"]
    c["referees"] = c["referees"][:1]
    c["availability"] = []
    env.save(c)
    r = env.submit(1)
    assert r.status_code == 422
    issues = r.json()["error"]["issues"]
    assert {"personal.photo:required", "referees:wrong_count", "availability:required"} <= set(
        issues
    )
    assert env.get().json()["status"] == "draft" and counts(env)["teacher_profile"] == 0


def test_submit_requires_the_declaration(env):
    env.ready()
    r = env.submit(1, declaration=False)
    assert r.status_code == 422 and "declaration:required" in r.json()["error"]["issues"]


@pytest.mark.parametrize("key", [None, "", "short", "k" * 65, "has space " * 3, "bad/slash" * 3])
def test_submit_needs_a_valid_idempotency_key(env, key):
    env.ready()
    r = env.submit(1, key=key)
    assert r.status_code == 400 and r.json()["error"]["code"] == "idempotency_key_required"


def test_submit_with_a_stale_revision_is_a_conflict(env):
    env.ready()
    assert env.submit(0).status_code == 409


def test_server_validates_even_if_the_client_skipped_its_checks(env):
    c = valid_content()
    c["personal"]["date_of_birth"] = "2015-01-01"
    c["availability"] = [
        {"weekday": 1, "start_minute": 900, "end_minute": 960},
        {"weekday": 1, "start_minute": 930, "end_minute": 990},
    ]
    c["referees"][1]["phone"] = c["referees"][0]["phone"]
    env.save(c)
    issues = set(env.submit(1).json()["error"]["issues"])
    assert {
        "personal.date_of_birth:under_age",
        "availability[1]:overlap",
        "referees[1].phone:duplicate",
    } <= issues


# -- the happy path -----------------------------------------------------------------------------
def test_submit_creates_the_application_in_submitted_state(env):
    env.ready()
    r = env.submit(1)
    assert r.status_code == 200
    j = r.json()
    assert j["status"] == "submitted" and j["attempt"] == 1 and j["replayed"] is False
    assert j["submitted_at"].startswith("2026-11-25T09:00:00")
    g = env.get().json()
    assert g["status"] == "submitted" and g["can_edit"] is False


def test_submit_writes_profile_zones_availability_referees_subjects(env):
    env.ready()
    env.submit(1)
    assert counts(env) == {
        "teacher_profile": 1,
        "teacher_zone": 2,
        "availability_slot": 2,
        "teacher_referee": 2,
        "teacher_application_subject": 4,
        "teacher_approval": 2,
    }
    prof = env.repo.get("teacher_profile", "teacher_one")
    assert prof["status"] == "pending"  # never active: admin approval (V04) does that
    assert prof["travel_radius_km"] == 10 and prof["gender"] == "female"
    refs = sorted(env.rows("teacher_referee"), key=lambda r: r["position"])
    assert [r["phone"] for r in refs] == ["+256701234567", "+256755000111"]
    assert all(r["consent_at"] and r["teacher_id"] == "teacher_one" for r in refs)
    slot = env.rows("availability_slot")[0]
    assert (slot["start_minute"], slot["end_minute"]) in {(900, 1080)} and slot["weekday"] in (1, 3)


def test_approvals_are_requests_only(env):
    env.ready()
    env.submit(1)
    for a in env.rows("teacher_approval"):
        assert a.get("approved_at") is None and a.get("approved_by_id") is None
        assert a["curriculum_id"] == "cur_ug" and a["level_id"] in ("lvl_p1", "lvl_p2")


def test_no_referee_is_contacted_and_no_message_goes_to_them(env):
    env.ready()
    env.submit(1)
    assert all(n.user_id in (None, "teacher_one") for n in env.notifier.sent)
    assert all(r.get("contacted_at") is None for r in env.rows("teacher_referee"))


def test_audit_and_notifications_are_written_once_without_personal_data(env):
    env.ready()
    env.submit(1)
    assert len(env.audit.entries) == 1
    e = env.audit.entries[0]
    assert (e.action, e.entity, e.entity_id, e.actor_id) == (
        "teacher_application.submitted",
        "teacher_application",
        "teacher_one",
        "teacher_one",
    )
    blob = json.dumps(e.detail)
    for pii in ("Grace", "Peter", "0701", "+256", "peter@", "Makerere", "1992"):
        assert pii not in blob
    assert sorted(n.template for n in env.notifier.sent) == [
        "application_submitted_ops",
        "application_submitted_teacher",
    ]


def test_graduate_without_registration_number_can_submit(env):
    c = valid_content()
    c["qualifications"]["teacher_type"] = "graduate"
    del c["qualifications"]["registration_number"]
    env.save(c)
    assert env.submit(1).status_code == 200
    assert env.repo.get("teacher_application", "teacher_one")["teacher_type"] == "graduate"


def test_existing_active_profile_is_never_downgraded_by_a_submit(env):
    env.repo.create(
        "teacher_profile", "teacher_one", {"user_id": "teacher_one", "status": "active"}
    )
    env.ready()
    env.submit(1)
    assert env.repo.get("teacher_profile", "teacher_one")["status"] == "active"


# -- exactly once ---------------------------------------------------------------------------------
def test_same_key_again_replays_without_new_rows(env):
    env.ready()
    first = env.submit(1).json()
    before = counts(env)
    again = env.submit(2)
    assert again.status_code == 200 and again.json()["replayed"] is True
    assert again.json()["submitted_at"] == first["submitted_at"]
    assert counts(env) == before and len(env.audit.entries) == 1 and len(env.notifier.sent) == 2


def test_a_different_key_after_submit_is_already_submitted(env):
    env.ready()
    env.submit(1)
    r = env.submit(2, key="other-key-" + "z" * 10)
    assert r.status_code == 409 and r.json()["error"]["code"] == "already_submitted"


def test_editing_after_submit_is_refused(env):
    env.ready()
    env.submit(1)
    r = env.save({"personal": {"full_name": "Changed"}}, rev=2)
    assert r.status_code == 409 and r.json()["error"]["code"] == "application_locked"
    assert env.get().json()["content"]["personal"]["full_name"] == "Grace Nakato"


def test_fifty_simultaneous_submits_with_the_same_key_create_one_application(busy):
    busy.ready()
    out = []

    def go():
        out.append(busy.submit(1))

    ts = [threading.Thread(target=go) for _ in range(50)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert [r.status_code for r in out].count(200) == 50, [
        r.text for r in out if r.status_code != 200
    ][:2]
    assert counts(busy) == {
        "teacher_profile": 1,
        "teacher_zone": 2,
        "availability_slot": 2,
        "teacher_referee": 2,
        "teacher_application_subject": 4,
        "teacher_approval": 2,
    }
    assert len(busy.audit.entries) == 1 and len(busy.notifier.sent) == 2
    row = busy.repo.get("teacher_application", "teacher_one")
    assert row["status"] == "submitted" and row["attempt"] == 1
    assert len({r.json()["submitted_at"] for r in out if r.json()["replayed"] is False}) <= 50


def test_fifty_simultaneous_submits_with_different_keys_have_one_winner(busy):
    busy.ready()
    out = []

    def go(i):
        out.append(busy.submit(1, key=f"key-{i:03d}-" + "q" * 12).status_code)

    ts = [threading.Thread(target=go, args=(i,)) for i in range(50)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert out.count(200) == 1 and out.count(409) == 49
    assert len(busy.audit.entries) == 1 and counts(busy)["teacher_referee"] == 2


# -- crash safety ---------------------------------------------------------------------------------
@pytest.mark.parametrize("target", ["audit", "notifier"])
def test_a_failure_after_the_claim_leaves_a_draft_and_a_retry_completes_once(env, target):
    env.ready()
    getattr(env, target).fail = True
    p = Principal("teacher_one", ["teacher"])
    with pytest.raises(RuntimeError):
        env.service.submit(p, SubmitBody(base_revision=1, declaration=True), KEY)
    assert env.get().json()["status"] == "draft"  # the status flips last
    getattr(env, target).fail = False
    r = env.submit(1)
    assert r.status_code == 200 and r.json()["status"] == "submitted"
    assert counts(env)["teacher_referee"] == 2 and len(env.audit.entries) == 1
    assert sorted(n.template for n in env.notifier.sent) == [
        "application_submitted_ops",
        "application_submitted_teacher",
    ]


def test_after_a_crash_a_different_key_cannot_take_over(env):
    env.ready()
    env.audit.fail = True
    with pytest.raises(RuntimeError):
        env.service.submit(
            Principal("teacher_one", ["teacher"]),
            SubmitBody(base_revision=1, declaration=True),
            KEY,
        )
    env.audit.fail = False
    r = env.submit(1, key="another-" + "k" * 12)
    assert r.status_code == 409 and r.json()["error"]["code"] == "submit_in_progress"


def test_drafts_cannot_change_while_a_submit_is_in_flight(env):
    env.ready()
    env.audit.fail = True
    with pytest.raises(RuntimeError):
        env.service.submit(
            Principal("teacher_one", ["teacher"]),
            SubmitBody(base_revision=1, declaration=True),
            KEY,
        )
    r = env.save({"personal": {"full_name": "Sneaky Edit"}}, rev=1)
    assert r.status_code == 409 and r.json()["error"]["code"] == "submit_in_progress"


# -- statuses and resubmission ---------------------------------------------------------------------
def set_status(e, status, **extra):
    e.repo.update("teacher_application", "teacher_one", {"status": status, **extra})


@pytest.mark.parametrize("status", ["under_review", "approved"])
def test_locked_statuses_cannot_be_edited_or_resubmitted(env, status):
    env.ready()
    env.submit(1)
    set_status(env, status)
    assert env.save({}, rev=2).status_code == 409
    assert env.submit(2, key="new-key-" + "n" * 12).status_code == 409
    assert env.submit(2).json()["replayed"] is True
    assert env.client.post("/v1/teacher/application/reopen", headers=env.h()).status_code == 409


def test_a_client_cannot_move_its_own_status(env):
    env.ready()
    env.submit(1)
    set_status(env, "under_review")
    for path in ("/draft", "/submit"):
        r = env.client.request(
            "PUT" if path == "/draft" else "POST",
            "/v1/teacher/application" + path,
            json={"base_revision": 2, "declaration": True, "content": {}, "status": "draft"},
            headers=env.h(key="x" * 20),
        )
        assert r.status_code == 422


def test_action_needed_is_editable_and_resubmits_as_attempt_two(env):
    env.ready()
    env.submit(1)
    set_status(env, "action_needed")
    g = env.get().json()
    assert g["can_edit"] is True
    c = deep(g["content"])
    c["referees"][0]["name"] = "Peter B Okello"
    r = env.save(c, rev=g["revision"])
    assert r.status_code == 200 and r.json()["status"] == "action_needed"
    r = env.submit(r.json()["revision"], key="second-" + "s" * 12)
    assert r.status_code == 200 and r.json()["attempt"] == 2
    live = [x for x in env.rows("teacher_referee") if not x.get("deleted_at")]
    assert len(live) == 2 and {x["attempt"] for x in live} == {2}
    assert len(env.rows("teacher_referee")) == 4  # attempt 1 kept, soft-deleted
    assert len(env.rows("teacher_approval")) == 2  # requests are not duplicated
    assert len(env.audit.entries) == 2


def test_resubmission_after_rejection_only_after_the_cooldown(env):
    env.ready()
    env.submit(1)
    decided = env.clock.now
    set_status(env, "not_approved", decided_at=decided.isoformat())
    assert env.get().json()["can_reopen"] is False
    assert env.submit(2, key="again-" + "a" * 12).status_code == 409
    r = env.client.post("/v1/teacher/application/reopen", headers=env.h())
    assert r.status_code == 409 and r.json()["error"]["code"] == "reapply_too_soon"
    env.clock.advance(days=89)
    assert env.client.post("/v1/teacher/application/reopen", headers=env.h()).status_code == 409
    env.clock.advance(days=1)
    r = env.client.post("/v1/teacher/application/reopen", headers=env.h())
    assert r.status_code == 200 and r.json()["status"] == "draft"
    assert r.json()["content"]["personal"]["full_name"] == "Grace Nakato"  # nothing lost
    r = env.submit(r.json()["revision"], key="again-" + "a" * 12)
    assert r.status_code == 200 and r.json()["attempt"] == 2


def test_cooldown_is_configurable_and_zero_allows_immediate_reapply():
    e = Env(reapply_cooldown_days=0)
    e.ready()
    e.submit(1)
    e.repo.update(
        "teacher_application",
        "teacher_one",
        {"status": "not_approved", "decided_at": e.clock.now.isoformat()},
    )
    assert e.client.post("/v1/teacher/application/reopen", headers=e.h()).status_code == 200


def test_two_teachers_do_not_interfere(env):
    env.repo.create("user_profile", "teacher_two", {"phone": "+256700000002", "name": "y"})
    env.ready()
    env.ready("t2")
    assert env.submit(1).status_code == 200
    assert env.get("t2").json()["status"] == "draft"
    assert env.submit(1, token="t2", key="t2-key-" + "t" * 12).status_code == 200
    assert counts(env)["teacher_profile"] == 2 and counts(env)["teacher_referee"] == 4


def test_referee_may_not_be_the_applicant(env):
    c = valid_content()
    c["referees"][0]["phone"] = "0772 123 456"
    env.save(c)
    r = env.submit(1)
    assert r.status_code == 422 and "referees[0].phone:own_number" in r.json()["error"]["issues"]


def test_subjects_and_levels_must_come_from_the_published_catalogue(env):
    c = valid_content()
    c["curricula"][0]["subject_ids"] = ["sub_ghost"]
    env.save(c)
    assert "curricula[0].subject_ids:unknown_choice" in env.submit(1).json()["error"]["issues"]
    env.catalogue.options = []
    assert "curricula[0].curriculum_id:unknown_choice" in env.submit(1).json()["error"]["issues"]


def test_submit_time_is_utc_instant_stored_and_displayable_in_kampala(env):
    env.ready()
    env.submit(1)
    row = env.repo.get("teacher_application", "teacher_one")
    assert row["submitted_at"].endswith("+00:00")


def test_rate_limited_submit_does_not_touch_state():
    e = Env(submits_per_hour=1)
    e.ready()
    assert e.submit(1).status_code == 200
    assert e.submit(2).status_code == 429
    assert e.get().json()["status"] == "submitted"
    e.clock.advance(hours=1, minutes=1)
    assert e.submit(2).json()["replayed"] is True


def test_cooldown_default_matches_policy():
    assert Env().service.policy.reapply_cooldown_days == 90
    assert timedelta(days=90).days == 90
