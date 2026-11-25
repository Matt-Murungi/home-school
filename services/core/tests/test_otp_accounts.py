"""A01 (#43): find-or-create the Appwrite user, profile and role label, issue the custom token."""

import pytest

from hs_core.auth_otp.accounts import FakeUserDirectory
from hs_core.auth_otp.service import OtpError
from otp_helpers import OTHER, PHONE, Env


def sign_in(e, phone=PHONE, app="parent"):
    e.clock.advance(31)
    e.request(phone, app=app)
    return e.verify(e.last_code(), phone=phone, app=app)


def test_new_number_creates_user_profile_label_and_role_history_row():
    e = Env()
    e.request()
    res = e.verify(e.last_code())
    u = e.users.get(res.user_id)
    assert u.phone == PHONE and u.labels == ["parent"]
    prof = e.repo.get("user_profile", res.user_id)
    assert prof["phone"] == PHONE and prof["status"] == "active"
    assert prof["$permissions"] == [f'read("user:{res.user_id}")']
    hist = e.repo.list("role_assignment")
    assert [(h["user_id"], h["role"]) for h in hist] == [(res.user_id, "parent")]
    assert hist[0]["active_key"] == f"{res.user_id}:parent"
    assert e.users.tokens[-1][0] == res.user_id


def test_returning_number_signs_into_the_same_account_without_new_rows():
    e = Env()
    first = sign_in(e)
    second = sign_in(e)
    assert second.user_id == first.user_id and second.is_new_user is False
    assert len(e.users.users) == 1 and len(e.repo.list("user_profile")) == 1
    assert len(e.repo.list("role_assignment")) == 1


def test_teacher_app_signs_in_with_teacher_label():
    e = Env()
    res = sign_in(e, app="teacher")
    assert e.users.get(res.user_id).labels == ["teacher"]


def test_same_number_in_the_other_app_is_one_user_with_two_roles():
    e = Env()
    p = sign_in(e, app="parent")
    t = sign_in(e, app="teacher")
    assert p.user_id == t.user_id and len(e.users.users) == 1
    assert sorted(e.users.get(p.user_id).labels) == ["parent", "teacher"]
    assert sorted(h["role"] for h in e.repo.list("role_assignment")) == ["parent", "teacher"]
    assert len(e.repo.list("user_profile")) == 1


def test_client_claim_alone_never_grants_a_privileged_label():
    e = Env()
    for bad in ("opsadmin", "superadmin", "curator", "safeguardinglead", "guardian", "Parent", ""):
        with pytest.raises(OtpError) as x:
            e.request(app=bad)
        assert x.value.code == "invalid_app"
    assert not e.users.users


def test_existing_staff_labels_are_kept_and_not_extended():
    e = Env()
    e.users.add(PHONE, labels=["opsadmin"])
    res = sign_in(e, app="parent")
    assert sorted(e.users.get(res.user_id).labels) == ["opsadmin", "parent"]


def test_suspended_account_gets_no_token_even_with_the_right_code():
    e = Env()
    uid = e.users.add(PHONE, labels=["parent"], active=False)
    e.request()
    with pytest.raises(OtpError) as x:
        e.verify(e.last_code())
    assert x.value.code == "account_blocked" and x.value.status == 403
    assert not e.users.tokens and uid


def test_directory_outage_is_503_and_creates_nothing_partial():
    e = Env()
    e.users.available = False
    e.request()
    with pytest.raises(OtpError) as x:
        e.verify(e.last_code())
    assert x.value.code == "auth_unavailable"
    assert not e.repo.list("user_profile") and not e.users.users


def test_repository_outage_after_user_creation_is_retriable_and_converges():
    e = Env()
    e.request()
    code = e.last_code()
    e.repo.available = True
    real_create = e.repo.create
    state = {"fail": True}

    def flaky(table, row_id, data, permissions=None):
        if table == "user_profile" and state["fail"]:
            state["fail"] = False
            from hs_core.repository import RepositoryUnavailable

            raise RepositoryUnavailable("down")
        return real_create(table, row_id, data, permissions)

    e.repo.create = flaky
    with pytest.raises(OtpError) as x:
        e.verify(code)
    assert x.value.status == 503
    res = e.verify(code)  # same code still works; user is found, profile created now
    assert e.repo.get("user_profile", res.user_id)["phone"] == PHONE
    assert len(e.users.users) == 1


def test_two_first_sign_ins_racing_on_one_phone_make_one_user():
    e = Env()
    e.users.race_on_create = True  # directory reports a conflict once, as Appwrite would
    e.request()
    res = e.verify(e.last_code())
    assert len(e.users.users) == 1 and e.users.get(res.user_id).phone == PHONE


def test_profile_phone_conflict_with_another_user_is_a_409_not_a_takeover():
    """Appwrite's unique_phone index answers 409 when another profile already has this number."""
    from hs_core.repository import Conflict

    e = Env()
    real = e.repo.create

    def create(table, row_id, data, permissions=None):
        if table == "user_profile":
            raise Conflict("unique_phone")
        return real(table, row_id, data, permissions)

    e.repo.create = create
    e.request()
    with pytest.raises(OtpError) as x:
        e.verify(e.last_code())
    assert x.value.code == "account_conflict" and x.value.status == 409
    assert not e.users.tokens


def test_phone_number_change_moves_the_account_and_frees_the_old_number():
    e = Env()
    first = sign_in(e, phone=PHONE)
    e.users.change_phone(first.user_id, OTHER)  # done by a verified profile flow elsewhere
    again = sign_in(e, phone=OTHER)
    assert again.user_id == first.user_id and again.is_new_user is False
    stranger = sign_in(e, phone=PHONE)  # old number is now a different, new account
    assert stranger.user_id != first.user_id and stranger.is_new_user is True


def test_carrier_recycled_number_after_long_inactivity_is_flagged_for_reverification():
    e = Env(dormant_days=180)
    uid = e.users.add(PHONE, labels=["parent"], accessed_days_ago=400)
    res = sign_in(e)
    assert res.user_id == uid and res.dormant is True


def test_recent_account_is_not_flagged_dormant():
    e = Env(dormant_days=180)
    e.users.add(PHONE, labels=["parent"], accessed_days_ago=3)
    assert sign_in(e).dormant is False


def test_token_is_single_purpose_and_expires_soon():
    e = Env()
    res = sign_in(e)
    assert (res.expire - e.clock.now).total_seconds() <= 300


def test_fake_directory_enforces_unique_phone():
    d = FakeUserDirectory()
    d.create("u1", PHONE)
    from hs_core.auth_otp.accounts import DirectoryConflict

    with pytest.raises(DirectoryConflict):
        d.create("u2", PHONE)
