"""V01 round 2: photo file ids refused, permissions on created rows, profile-id mapping, submit
claim takeover after a crash, revision claim recovery, the draft-save race."""

import threading

import pytest

from application_helpers import KEY, Env, deep, valid_content
from hs_core.auth import Principal
from hs_core.repository import RepositoryUnavailable
from hs_core.teacher_application.models import DraftBody, SubmitBody

P = Principal("teacher_one", ["teacher"])


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


# -- blocker 2: no client file ids -------------------------------------------------------------
@pytest.mark.parametrize(
    "photo",
    [
        {"state": "uploaded", "file_id": "someone_elses_file"},
        {"state": "chosen", "file_id": "someone_elses_file"},
        {"state": "uploaded"},
    ],
)
def test_a_client_file_id_or_uploaded_state_is_reported_never_stored_and_never_copied(photo):
    e = Env()
    c = valid_content()
    c["personal"]["photo"] = photo
    r = e.save(c)
    assert r.status_code == 200 and "personal.photo:file_not_allowed" in r.json()["issues"]
    stored = e.repo.get("teacher_application", "teacher_one")["content_json"]
    assert "someone_elses_file" not in stored and '"uploaded"' not in stored
    assert "file_id" not in e.get().json()["content"]["personal"]["photo"]
    # what remains is the on-device photo; submitting copies no file id anywhere
    assert e.submit(1).status_code == 200
    assert e.rows("teacher_profile")[0].get("photo_file_id") is None
    assert "someone_elses_file" not in str(e.rows("teacher_application"))


def test_the_photo_file_id_of_the_profile_is_always_empty_after_submit():
    e = Env()
    e.ready()
    e.submit(1)
    assert e.rows("teacher_profile")[0].get("photo_file_id") is None


# -- permissions of created rows (M05) ---------------------------------------------------------
def test_every_created_row_is_readable_by_owner_and_ops_only():
    e = Env()
    e.ready()
    e.submit(1)
    want = ['read("label:opsadmin")', 'read("user:teacher_one")']
    for t in (
        "teacher_application",
        "teacher_referee",
        "teacher_application_subject",
        "teacher_zone",
        "availability_slot",
        "teacher_approval",
        "teacher_profile",
    ):
        for r in e.rows(t):
            assert r["$permissions"] == want, t


def test_created_rows_match_m05_desired_permissions_so_the_audit_finds_no_drift():
    from hs_core.permissions.context import PermissionContext
    from hs_core.permissions.rules import desired_permissions
    from hs_core.permissions.service import PermissionService

    e = Env()
    e.ready()
    e.submit(1)
    svc = PermissionService(e.repo)
    ctx: PermissionContext = svc.context()
    for t in ("teacher_application", "teacher_referee", "teacher_application_subject"):
        for r in e.rows(t):
            assert list(desired_permissions(t, r, ctx)) == r["$permissions"], t


def test_an_existing_profile_with_another_id_is_reused_not_duplicated():
    e = Env()
    e.repo.create("teacher_profile", "profile_9", {"user_id": "teacher_one", "status": "pending"})
    e.ready()
    assert e.submit(1).status_code == 200
    assert [r["$id"] for r in e.rows("teacher_profile")] == ["profile_9"]
    assert {r["teacher_id"] for r in e.rows("teacher_zone")} == {"profile_9"}
    assert {r["teacher_id"] for r in e.rows("teacher_approval")} == {"profile_9"}
    assert {r["teacher_id"] for r in e.rows("teacher_referee")} == {"teacher_one"}


# -- takeover of a stuck submit ----------------------------------------------------------------
def crash_submit(e):
    e.audit.fail = True
    with pytest.raises(RuntimeError):
        e.service.submit(P, SubmitBody(base_revision=1, declaration=True), KEY)
    e.audit.fail = False


def test_a_fresh_claim_by_another_key_is_in_progress_not_already_submitted():
    e = Env()
    e.ready()
    crash_submit(e)
    r = e.submit(1, key="reinstalled-" + "n" * 10)
    assert r.status_code == 409 and r.json()["error"]["code"] == "submit_in_progress"
    assert int(r.headers["retry-after"]) > 0
    assert (
        e.save({"personal": {"full_name": "Edit"}}, rev=1).json()["error"]["code"]
        == "submit_in_progress"
    )


def test_after_the_window_a_new_key_takes_over_and_completes_without_duplicates():
    e = Env()
    e.ready()
    crash_submit(e)
    first = counts(e)  # the crash left the final rows behind (audit was the step that failed)
    e.clock.advance(minutes=11)
    r = e.submit(1, key="reinstalled-" + "n" * 10)
    assert (
        r.status_code == 200 and r.json()["status"] == "submitted" and r.json()["replayed"] is False
    )
    assert (
        counts(e)
        == first
        == {
            "teacher_profile": 1,
            "teacher_zone": 2,
            "availability_slot": 2,
            "teacher_referee": 2,
            "teacher_application_subject": 4,
            "teacher_approval": 2,
        }
    )
    assert len(e.audit.entries) == 1 and len(e.notifier.sent) == 2
    assert (
        e.repo.get("teacher_application", "teacher_one")["submit_key"] == "reinstalled-" + "n" * 10
    )
    # the old key is now a different key: refused, the new key replays
    assert e.submit(2, key=KEY).status_code == 409
    assert e.submit(2, key="reinstalled-" + "n" * 10).json()["replayed"] is True


def test_crash_halfway_through_the_writes_then_takeover_finishes_the_rest():
    e = Env()
    e.ready()
    original = e.repo.create
    calls = {"n": 0}

    def flaky(table, rid, data, perms=None):
        if table == "teacher_referee":
            calls["n"] += 1
            if calls["n"] == 2:
                raise RepositoryUnavailable("injected")
        return original(table, rid, data, perms)

    e.repo.create = flaky
    with pytest.raises(RepositoryUnavailable):
        e.service.submit(P, SubmitBody(base_revision=1, declaration=True), KEY)
    e.repo.create = original
    assert len(e.rows("teacher_referee")) == 1
    e.clock.advance(minutes=11)
    assert e.submit(1, key="reinstalled-" + "n" * 10).status_code == 200
    assert counts(e)["teacher_referee"] == 2 and len(e.audit.entries) == 1


def test_simultaneous_takeovers_have_one_winner_and_no_duplicates():
    e = Env(submits_per_hour=500)
    e.ready()
    crash_submit(e)
    e.clock.advance(minutes=11)
    out = []

    def go(i):
        out.append(e.submit(1, key=f"takeover-{i:03d}-" + "t" * 10).status_code)

    ts = [threading.Thread(target=go, args=(i,)) for i in range(20)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert out.count(200) == 1 and set(out) <= {200, 409}
    assert counts(e)["teacher_referee"] == 2 and len(e.audit.entries) == 1


def test_takeover_is_per_teacher_another_teacher_cannot_touch_the_claim():
    e = Env()
    e.repo.create("user_profile", "teacher_two", {"phone": "+256700000002", "name": "y"})
    e.ready()
    crash_submit(e)
    e.clock.advance(minutes=30)
    e.ready("t2")
    assert e.submit(1, token="t2", key="t2-key-" + "t" * 12).status_code == 200
    assert e.get().json()["status"] == "draft"  # teacher one is untouched
    assert counts(e)["teacher_referee"] == 4  # two left by the crash + two for teacher two


def test_the_takeover_window_is_configurable():
    e = Env(submit_takeover_minutes=1)
    e.ready()
    crash_submit(e)
    e.clock.advance(seconds=61)
    assert e.submit(1, key="reinstalled-" + "n" * 10).status_code == 200


def test_after_the_window_an_abandoned_claim_no_longer_blocks_draft_edits():
    e = Env()
    e.ready()
    crash_submit(e)
    e.clock.advance(minutes=11)
    assert e.save({"personal": {"full_name": "Back to editing"}}, rev=1).status_code == 200


# -- revision claim left by a crash --------------------------------------------------------------
def test_a_revision_claim_left_by_a_crash_unblocks_after_a_minute():
    e = Env()
    e.ready()
    original = e.repo.update

    def boom(table, rid, data, perms=None):
        if table == "teacher_application":
            raise RepositoryUnavailable("injected")
        return original(table, rid, data, perms)

    e.repo.update = boom
    r = e.client.put(
        "/v1/teacher/application/draft",
        json={"base_revision": 1, "content": {}},
        headers=e.h(),
    )
    assert r.status_code == 503
    e.repo.update = original
    assert e.save({}, rev=1).status_code == 409  # fresh claim: still a conflict
    e.clock.advance(seconds=61)
    assert e.save({"personal": {"full_name": "Recovered"}}, rev=1).status_code == 200
    assert e.get().json()["revision"] == 2


# -- the draft-save race
def test_a_draft_save_that_slips_in_while_submitting_is_never_lost():
    """The submit-claim check in `save_draft` is check-then-write. Simulate a save that passed
    it just before the claim: it lands while the submit works. The submit must notice (the
    revision slot is taken), redo its writes from the new content, and end consistent."""
    e = Env()
    e.ready()
    changed = deep(valid_content())
    changed["zone_ids"] = ["zone_kololo"]
    fired = {"done": False}
    real_exists = e.service._submit_claim_exists

    def slip_in(record):
        def inner(entry):
            if not fired["done"]:
                fired["done"] = True
                e.service._submit_claim_exists = lambda *a: (
                    False
                )  # the save already passed the check
                e.service.save_draft(P, DraftBody(base_revision=1, content=changed))
                e.service._submit_claim_exists = real_exists
            record(entry)

        return inner

    e.audit.record = slip_in(e.audit.record)
    r = e.submit(1)
    assert r.status_code == 200 and r.json()["status"] == "submitted"
    stored = e.get().json()["content"]
    assert stored["zone_ids"] == ["zone_kololo"]
    live = {z["zone_id"] for z in e.rows("teacher_zone")}
    assert "zone_kololo" in live  # the final rows follow the content that was actually submitted
    assert len(e.audit.entries) == 1


def test_a_save_from_the_old_revision_after_the_submit_took_the_slot_is_a_conflict():
    e = Env()
    e.ready()
    e.submit(1)
    assert e.save({}, rev=1).status_code in (409,)
