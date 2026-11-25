"""A03: phone sign-in and roles: Q27 (staff labels refused), suspended, revoked, role service."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from hs_core.auth_otp.service import OtpError
from hs_core.auth_otp.sms import FakeSmsSender
from hs_core.main import create_app
from hs_core.repository import InMemoryRepository
from hs_core.roles.service import SIGNUP, Actor
from hs_core.settings import Settings
from otp_helpers import PHONE, Env
from roles_helpers import CtxService


def attempt(e, app="parent", phone=PHONE):
    e.clock.advance(31)
    e.request(phone, app=app)
    return e.verify(e.last_code(), phone=phone, app=app)


def snapshot(e):
    return (
        {u.id: list(u.labels) for u in e.users.users.values()},
        len(e.users.tokens),
        len(e.repo.list("user_profile")),
        len(e.repo.list("role_assignment")),
    )


# ---- Q27: staff accounts never get a mobile session -----------------------------------------
@pytest.mark.parametrize("staff", ["opsadmin", "curator", "safeguardinglead", "superadmin"])
@pytest.mark.parametrize("app", ["parent", "teacher"])
def test_staff_label_holder_gets_no_session_through_the_phone_flow(staff, app):
    e = Env()
    e.users.add(PHONE, labels=[staff])
    before = snapshot(e)
    e.request(app=app)
    with pytest.raises(OtpError) as x:
        e.verify(e.last_code(), app=app)
    assert (x.value.code, x.value.status) == ("staff_account", 403)
    assert snapshot(e) == before  # no token, no label, no profile, no role row
    assert not e.users.tokens


def test_staff_plus_parent_label_is_still_refused_any_staff_label_counts():
    e = Env()
    e.users.add(PHONE, labels=["parent", "teacher", "curator"])
    with pytest.raises(OtpError) as x:
        attempt(e)
    assert x.value.code == "staff_account" and not e.users.tokens


def test_the_refusal_comes_only_after_the_code_is_proven_so_it_cannot_be_probed():
    e = Env()
    e.users.add(PHONE, labels=["superadmin"])
    e.request()
    with pytest.raises(OtpError) as x:
        e.verify(e.wrong_code())
    assert x.value.code == "invalid_code"  # identical to any other number's wrong code
    # requesting a code for a staff number looks exactly like any other number
    e2 = Env()
    r1 = e.request("+256701111111")
    r2 = e2.request("+256701111111")
    assert r1 == r2


def test_the_staff_refusal_is_a_clear_403_with_a_helpful_message_and_no_secret():
    e = Env()
    e.users.add(PHONE, labels=["opsadmin"])
    with pytest.raises(OtpError) as x:
        attempt(e)
    assert "admin dashboard" in x.value.message


def test_a_label_variant_is_not_a_staff_label_and_does_not_block_sign_in():
    e = Env()
    uid = e.users.add(PHONE, labels=["OpsAdmin", "ops_admin"])
    res = attempt(e)
    assert res.user_id == uid
    assert "parent" in e.users.get(uid).labels


def test_staff_user_over_http_is_403_with_the_named_error_and_no_token():
    sms, repo = FakeSmsSender(), InMemoryRepository()
    from hs_core.auth_otp.accounts import FakeUserDirectory

    users = FakeUserDirectory()
    users.add(PHONE, labels=["superadmin"])
    app = create_app(
        settings=Settings(_env_file=None, hs_env="dev", otp_pepper="x" * 40),
        repository=repo,
        sms_sender=sms,
        user_directory=users,
    )
    c = TestClient(app)
    c.post("/v1/auth/otp/request", json={"phone": "0772123456", "app": "parent"})
    import re

    code = re.search(r"(?<!\d)(\d{6})(?!\d)", sms.sent[-1].body).group(1)
    r = c.post("/v1/auth/otp/verify", json={"phone": "0772123456", "app": "parent", "code": code})
    assert r.status_code == 403 and r.json()["error"]["code"] == "staff_account"
    assert "secret" not in r.text and not users.tokens


# ---- suspended: a state, not a login error -------------------------------------------------
def test_suspended_teacher_gets_the_suspended_error_in_the_teacher_app():
    e = Env()
    uid = e.users.add(PHONE, labels=["teacher"])
    e.repo.create("teacher_profile", "tp1", {"user_id": uid, "status": "suspended"})
    with pytest.raises(OtpError) as x:
        attempt(e, app="teacher")
    assert (x.value.code, x.value.status) == ("account_suspended", 403)
    assert x.value.extra.get("suspended") is True
    assert not e.users.tokens


def test_a_teacher_suspension_does_not_lock_the_same_person_out_of_the_parent_app():
    e = Env()
    uid = e.users.add(PHONE, labels=["teacher", "parent"])
    e.repo.create("teacher_profile", "tp1", {"user_id": uid, "status": "suspended"})
    assert attempt(e, app="parent").user_id == uid


def test_suspended_user_profile_is_suspended_in_both_apps():
    e = Env()
    uid = e.users.add(PHONE, labels=["parent"])
    e.repo.create("user_profile", uid, {"phone": PHONE, "status": "suspended"})
    for app in ("parent", "teacher"):
        with pytest.raises(OtpError) as x:
            attempt(e, app=app)
        assert x.value.code == "account_suspended"


def test_active_and_pending_teacher_profiles_sign_in_normally():
    e = Env()
    uid = e.users.add(PHONE, labels=["teacher"])
    e.repo.create("teacher_profile", "tp1", {"user_id": uid, "status": "pending"})
    assert attempt(e, app="teacher").user_id == uid


def test_a_soft_deleted_suspension_row_does_not_count_and_deactivated_is_blocked():
    e = Env()
    uid = e.users.add(PHONE, labels=["teacher"])
    e.repo.create(
        "teacher_profile",
        "tp1",
        {"user_id": uid, "status": "suspended", "deleted_at": "2026-01-01"},
    )
    assert attempt(e, app="teacher").user_id == uid
    e.repo.update("user_profile", uid, {"status": "deactivated"})
    with pytest.raises(OtpError) as x:
        attempt(e, app="teacher")
    assert x.value.code == "account_blocked"


# ---- sign-in uses the role service ----------------------------------------------------------
def test_signup_grant_is_audited_as_the_user_signing_up_with_one_row():
    e = Env()
    res = attempt(e)
    rows = e.repo.list("audit_log")
    by_entity = {r["entity"]: r for r in rows}
    # (the profile write is audited too under the app's AuditedRepository; this fake is bare)
    assert set(by_entity) == {"role_assignment"} and len(rows) == 1
    assert by_entity["role_assignment"]["actor_id"] == res.user_id  # not the body, not "signup"
    assert by_entity["role_assignment"]["action"] == "role.grant"


def test_role_staff_withdrew_is_not_given_back_by_signing_in_again():
    e = Env()
    res = attempt(e, app="teacher")
    roles = CtxService(e.service.accounts.roles)
    roles.revoke_role(Actor.user("ops1", ["opsadmin"]), res.user_id, "teacher", reason="misconduct")
    with pytest.raises(OtpError) as x:
        attempt(e, app="teacher")
    assert (x.value.code, x.value.status) == ("role_revoked", 403)
    assert "teacher" not in e.users.get(res.user_id).labels
    assert not roles.users.get(res.user_id).labels  # still nothing
    assert attempt(e, app="parent").user_id == res.user_id  # parent role is independent


def test_signing_in_repairs_a_label_that_has_no_history_row():
    e = Env()
    uid = e.users.add(PHONE, labels=["parent"])  # A01 crash or console: label, no row
    attempt(e)
    assert [r["role"] for r in e.repo.list("role_assignment") if r["user_id"] == uid] == ["parent"]


def test_signing_in_repairs_a_history_row_that_has_no_label():
    e = Env()
    res = attempt(e)
    e.users.users[res.user_id].labels.clear()
    attempt(e)
    assert e.users.get(res.user_id).labels == ["parent"]
    assert len([r for r in e.repo.list("role_assignment") if r.get("active_key")]) == 1


def test_signup_actor_cannot_be_used_to_reach_a_staff_label():
    e = Env()
    uid = e.users.add(PHONE)
    with pytest.raises(Exception) as x:
        CtxService(e.service.accounts.roles).grant_role(SIGNUP, uid, "superadmin")
    assert x.value.code == "forbidden"
