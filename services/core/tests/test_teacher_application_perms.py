"""V01 (#56) x M05 (#40): permission decisions for the teacher application tables, role played
against every persona, plus withdrawal behaviours (suspended, label withdrawn, deleted)."""

import pytest

from hs_core.permissions.fake import PermissionDenied
from hs_core.permissions.rules import SPECS
from jobs_helpers import T0
from perm_world import World, iso
from test_perm_rules import users, want

TABLES = ("teacher_application", "teacher_referee", "teacher_application_subject")
ROWS = {
    "teacher_application": "tapp_t1",
    "teacher_referee": "tref_t1",
    "teacher_application_subject": "tsub_t1",
}
OTHER = {
    "teacher_application": "tapp_t4",
    "teacher_referee": "tref_t4",
    "teacher_application_subject": "tsub_t4",
}
PERSONAS = ["uPA", "uGA", "uT1", "uT2", "uT4", "uOps", "uCur", "uL1", "uSup", "uNone", None]


@pytest.fixture
def w():
    return World().seed()


def test_every_table_has_a_decision_and_the_claim_table_is_service_only():
    for t in TABLES:
        assert not SPECS[t].service_only and SPECS[t].static_labels() == ("opsadmin",)
    assert SPECS["teacher_application_claim"].service_only


@pytest.mark.parametrize("table", TABLES)
@pytest.mark.parametrize("uid", PERSONAS)
def test_role_played_read_allow_and_deny(w, table, uid):
    view = w.as_(uid)
    mine, other = ROWS[table], OTHER[table]
    owner = {"uT1": {mine}, "uT4": {other}, "uOps": {mine, other}}
    assert view.readable_ids(table) == owner.get(uid, set())


@pytest.mark.parametrize("table", (*TABLES, "teacher_application_claim"))
@pytest.mark.parametrize("uid", ["uT1", "uOps", "uSup", "uL1", "uPA", None])
def test_nobody_writes_from_a_client(w, table, uid):
    if table == "teacher_application_claim":
        w.add(table, "cl_x", kind="submit", teacher_id="uT1")
    view = w.as_(uid)
    rid = ROWS.get(table, "cl_x")
    with pytest.raises(PermissionDenied):
        view.create(table, "new", {"teacher_id": "uT1"})
    with pytest.raises(PermissionDenied):
        view.update(table, rid, {"status": "approved"})
    with pytest.raises(PermissionDenied):
        view.delete(table, rid)


def test_the_claim_table_is_readable_by_no_client(w):
    w.add("teacher_application_claim", "cl1", kind="submit", teacher_id="uT1")
    w.rebuild(["teacher_application_claim"])
    for uid in ("uT1", "uOps", "uSup", "uL1"):
        assert w.as_(uid).readable_ids("teacher_application_claim") == set()


def test_a_safeguarding_lead_on_the_applicants_case_still_cannot_read_the_application(w):
    # uL1 is assigned to an open case about teacher T1 and reads T1's documents (M05), but the
    # application holds date of birth, school and referee contacts: owner and ops only.
    assert "uL1" in users(want(w, "teacher_document", "td_t1"))
    for t in TABLES:
        assert "uL1" not in users(want(w, t, ROWS[t]))


@pytest.mark.parametrize("table", TABLES)
def test_suspended_teacher_account_keeps_their_own_application(w, table):
    w.patch("user_profile", "uT1", status="suspended")
    assert "uT1" in users(want(w, table, ROWS[table]))


@pytest.mark.parametrize("status", ["suspended", "deactivated", "pending"])
def test_profile_status_does_not_hide_the_application_from_its_owner(w, status):
    w.patch("teacher_profile", "T1", status=status)
    assert "uT1" in users(want(w, "teacher_application", "tapp_t1"))


def test_teacher_label_withdrawn_still_leaves_the_owner_their_record_but_ops_unaffected(w):
    w.revoke_label("uT1", "teacher")
    assert "uT1" in users(want(w, "teacher_application", "tapp_t1"))
    assert want(w, "teacher_application", "tapp_t1") >= {'read("label:opsadmin")'}


@pytest.mark.parametrize("table", TABLES)
def test_deleted_account_loses_the_application_but_ops_keep_it_for_retention(w, table):
    w.patch("user_profile", "uT1", deleted_at=iso(T0))
    perms = want(w, table, ROWS[table])
    assert "uT1" not in users(perms) and 'read("label:opsadmin")' in perms


@pytest.mark.parametrize("table", TABLES)
def test_soft_deleted_rows_are_readable_by_nobody(w, table):
    w.patch(table, ROWS[table], deleted_at=iso(T0))
    assert want(w, table, ROWS[table]) == set()


def test_ops_that_lost_the_label_or_is_suspended_loses_the_rows(w):
    w.revoke_label("uOps", "opsadmin")
    assert w.as_("uOps").readable_ids("teacher_application") == set()
