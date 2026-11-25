"""A03: grant_role / revoke_role: policy, idempotency, audit, ordering, edge cases."""

from __future__ import annotations

import pytest

from hs_core.audit.service import meta_of
from hs_core.roles.service import (
    SIGNUP,
    Actor,
    Forbidden,
    IncompleteChange,
    LastSuperadmin,
    RevokedByStaff,
    SelfChange,
    TargetNotActive,
    UnknownRole,
    UnknownUser,
)
from roles_helpers import OPS, Env


# ---- who can grant what --------------------------------------------------------------------
@pytest.mark.parametrize("role", ["opsadmin", "curator", "safeguardinglead", "superadmin"])
def test_superadmin_grants_every_staff_role(role):
    e = Env()
    _, boss = e.superadmin()
    u = e.user()
    res = e.service.grant_role(boss, u, role, reason="test reason")
    assert res.changed and e.labels(u) == {role}
    assert [r["role"] for r in e.active_rows(u)] == [role]


@pytest.mark.parametrize("role", ["opsadmin", "curator", "safeguardinglead", "superadmin"])
def test_opsadmin_cannot_grant_any_staff_role_not_even_superadmin(role):
    e = Env()
    u = e.user()
    with pytest.raises(Forbidden):
        e.service.grant_role(OPS(), u, role)
    assert e.labels(u) == set() and e.active_rows(u) == []


@pytest.mark.parametrize("role", ["parent", "guardian", "teacher"])
def test_opsadmin_may_grant_mobile_roles(role):
    e = Env()
    u = e.user()
    assert e.service.grant_role(OPS(), u, role).changed
    assert e.labels(u) == {role}


@pytest.mark.parametrize(
    "actor_roles", [["parent"], ["teacher"], ["curator"], ["safeguardinglead"], []]
)
@pytest.mark.parametrize("role", ["parent", "teacher", "opsadmin", "superadmin"])
def test_other_roles_cannot_grant_anything(actor_roles, role):
    e = Env()
    u = e.user()
    with pytest.raises(Forbidden):
        e.service.grant_role(Actor.user("someone", actor_roles), u, role)
    assert e.labels(u) == set()


@pytest.mark.parametrize("role", ["parent", "guardian", "teacher", "opsadmin", "superadmin"])
def test_nobody_grants_themselves_anything_not_even_a_superadmin(role):
    e = Env()
    uid, boss = e.superadmin()
    with pytest.raises(SelfChange):
        e.service.grant_role(boss, uid, role)
    plain = e.user()
    with pytest.raises(SelfChange):
        e.service.grant_role(Actor.user(plain, ["parent"]), plain, role)
    assert e.labels(plain) == set()


def test_self_escalation_by_claiming_a_role_in_the_actor_is_not_possible_without_a_verified_actor():
    # Actor.user() cleans labels: a spoofed variant never becomes a role.
    a = Actor.user("u", ["OpsAdmin", "ops_admin", "superadmin "])
    assert a.roles == frozenset()
    e = Env()
    with pytest.raises(Forbidden):
        e.service.grant_role(a, e.user(), "parent")


def test_system_actors_never_grant_staff_roles():
    e = Env()
    for actor in (SIGNUP, Actor.system("guardian-link"), Actor.system("role-reconcile")):
        with pytest.raises(Forbidden):
            e.service.grant_role(actor, e.user(), "opsadmin", reason="test reason")


def test_signup_actor_only_grants_parent_and_teacher():
    e = Env()
    u = e.user()
    assert e.service.grant_role(SIGNUP, u, "parent").changed
    assert e.service.grant_role(SIGNUP, u, "teacher").changed
    with pytest.raises(Forbidden):
        e.service.grant_role(SIGNUP, u, "guardian")
    assert e.service.grant_role(Actor.system("guardian-link"), u, "guardian").changed


@pytest.mark.parametrize(
    "bad", ["", "Parent", "ops_admin", "super_admin", "root", None, 7, "parent "]
)
def test_unknown_roles_are_refused_and_write_nothing(bad):
    e = Env()
    u = e.user()
    with pytest.raises(UnknownRole):
        e.service.grant_role(OPS(), u, bad)
    with pytest.raises(UnknownRole):
        e.service.revoke_role(OPS(), u, bad)
    assert e.labels(u) == set() and e.raw_repo.list("role_assignment") == []


@pytest.mark.parametrize("uid", ["", None, "x" * 37, 5])
def test_bad_user_ids_are_refused(uid):
    with pytest.raises(UnknownUser):
        Env().service.grant_role(OPS(), uid, "parent")


def test_unknown_user_is_refused():
    with pytest.raises(UnknownUser):
        Env().service.grant_role(OPS(), "nobody", "parent")


# ---- idempotency --------------------------------------------------------------------------
def test_grant_twice_is_a_noop_with_one_row_and_one_audit_row():
    e = Env()
    u = e.user()
    first = e.service.grant_role(OPS(), u, "teacher")
    again = e.service.grant_role(OPS(), u, "teacher")
    assert first.changed and not again.changed
    assert len(e.rows(u, "teacher")) == 1 and e.labels(u) == {"teacher"}
    assert len(e.audit.role_intents()) == 1 and len(e.audit.rows()) == 1


def test_revoke_twice_is_a_noop():
    e = Env()
    u = e.user(["teacher"])
    e.service.grant_role(SIGNUP, u, "teacher")
    first = e.service.revoke_role(OPS(), u, "teacher")
    again = e.service.revoke_role(OPS(), u, "teacher")
    assert first.changed and not again.changed
    assert e.labels(u) == set() and e.active_rows(u) == []


def test_revoke_of_a_role_never_held_is_a_noop_and_writes_nothing():
    e = Env()
    u = e.user()
    res = e.service.revoke_role(OPS(), u, "teacher")
    assert not res.changed and e.raw_repo.list("role_assignment") == [] and not e.audit.rows()


def test_regrant_after_revoke_makes_a_new_generation_and_keeps_history():
    e = Env()
    u = e.user()
    e.service.grant_role(OPS(), u, "teacher")
    e.service.revoke_role(OPS(), u, "teacher", reason="left")
    e.service.grant_role(OPS(), u, "teacher")
    rows = e.rows(u, "teacher")
    assert len(rows) == 2
    assert sum(1 for r in rows if r.get("active_key")) == 1
    assert sum(1 for r in rows if r.get("revoked_at")) == 1
    assert e.labels(u) == {"teacher"}


def test_grant_repairs_an_active_row_whose_label_is_missing():
    e = Env()
    u = e.user()
    e.service.grant_role(OPS(), u, "teacher")
    e.raw_users.users[u].labels.clear()  # console edit / earlier crash
    res = e.service.grant_role(OPS(), u, "teacher")
    assert res.changed and res.repaired and e.labels(u) == {"teacher"}
    assert len(e.rows(u, "teacher")) == 1


def test_revoke_removes_a_label_that_has_no_row_when_asked_explicitly():
    e = Env()
    _, boss = e.superadmin()
    u = e.user(["curator"])
    res = e.service.revoke_role(boss, u, "curator", reason="test reason")
    assert res.changed and e.labels(u) == set()


# ---- revoke policy ------------------------------------------------------------------------
def test_revoke_policy_matrix():
    e = Env()
    _, boss = e.superadmin()
    staff = e.user()
    e.service.grant_role(boss, staff, "opsadmin", reason="test reason")
    with pytest.raises(Forbidden):  # ops cannot take away staff roles
        e.service.revoke_role(OPS(), staff, "opsadmin", reason="test reason")
    with pytest.raises(Forbidden):
        e.service.revoke_role(Actor.user("c", ["curator"]), staff, "opsadmin", reason="test reason")
    assert e.service.revoke_role(boss, staff, "opsadmin", reason="test reason").changed


def test_nobody_revokes_their_own_staff_role():
    e = Env()
    uid, boss = e.superadmin()
    other = e.user()
    e.service.grant_role(boss, other, "opsadmin", reason="test reason")
    with pytest.raises(SelfChange):
        e.service.revoke_role(
            Actor.user(other, ["opsadmin", "superadmin"]), other, "opsadmin", reason="test reason"
        )
    with pytest.raises(SelfChange):
        e.service.revoke_role(boss, uid, "superadmin", reason="test reason")


def test_a_user_may_drop_their_own_mobile_role_but_not_anothers():
    e = Env()
    u, v = e.user(), e.user()
    e.service.grant_role(SIGNUP, u, "teacher")
    e.service.grant_role(SIGNUP, v, "teacher")
    with pytest.raises(Forbidden):
        e.service.revoke_role(Actor.user(u, ["teacher"]), v, "teacher")
    assert e.service.revoke_role(Actor.user(u, ["teacher"]), u, "teacher").changed


def test_revoking_the_last_role_leaves_a_user_with_no_roles_but_a_profile():
    e = Env()
    u = e.user()
    e.service.grant_role(SIGNUP, u, "parent")
    res = e.service.revoke_role(OPS(), u, "parent")
    assert res.roles == () and e.labels(u) == set()
    assert e.raw_repo.get("user_profile", u)["status"] == "active"  # account untouched


def test_the_last_superadmin_cannot_be_revoked_but_a_second_can():
    e = Env()
    a_id, a = e.superadmin()
    b_id, _ = e.superadmin()
    assert e.service.revoke_role(a, b_id, "superadmin", reason="test reason").changed
    other = e.user()
    with pytest.raises(LastSuperadmin):
        e.service.revoke_role(
            Actor.user(other, ["superadmin"]), a_id, "superadmin", reason="test reason"
        )
    assert e.labels(a_id) == {"superadmin"} and e.active_rows(a_id, "superadmin")


# ---- suspended and deleted users -----------------------------------------------------------
@pytest.mark.parametrize(
    "kw",
    [
        {"active": False},
        {"profile_status": "suspended"},
        {"profile_status": "deactivated"},
        {"deleted": True},
    ],
)
def test_no_grants_to_suspended_blocked_or_deleted_users(kw):
    e = Env()
    u = e.user(**kw)
    with pytest.raises(TargetNotActive):
        e.service.grant_role(OPS(), u, "teacher")
    assert e.labels(u) == set() and e.rows(u, "teacher") == []


def test_revoke_is_allowed_for_suspended_and_deleted_users():
    e = Env()
    u = e.user()
    e.service.grant_role(OPS(), u, "teacher")
    e.raw_repo.update("user_profile", u, {"deleted_at": "2026-11-25T00:00:00+00:00"})
    assert e.service.revoke_role(OPS(), u, "teacher").changed
    assert e.labels(u) == set()


def test_revoke_for_an_account_gone_from_the_directory_still_closes_the_row():
    e = Env()
    u = e.user()
    e.service.grant_role(OPS(), u, "teacher")
    del e.raw_users.users[u]
    assert e.service.revoke_role(OPS(), u, "teacher").changed
    assert e.active_rows(u) == []


def test_a_role_withdrawn_by_staff_is_not_regranted_by_signing_in_again():
    e = Env()
    u = e.user()
    e.service.grant_role(SIGNUP, u, "teacher")
    e.service.revoke_role(OPS("ops9"), u, "teacher", reason="misconduct")
    with pytest.raises(RevokedByStaff):
        e.service.grant_role(SIGNUP, u, "teacher")
    assert e.labels(u) == set()
    # staff can still restore it deliberately
    assert e.service.grant_role(OPS(), u, "teacher").changed


def test_a_role_the_user_dropped_themselves_can_be_regained_at_sign_in():
    e = Env()
    u = e.user()
    e.service.grant_role(SIGNUP, u, "teacher")
    e.service.revoke_role(Actor.user(u, ["teacher"]), u, "teacher")
    assert e.service.grant_role(SIGNUP, u, "teacher").changed


# ---- audit ---------------------------------------------------------------------------------
def test_every_change_is_one_audit_row_with_actor_target_role_and_reason():
    e = Env()
    u = e.user()
    e.service.grant_role(OPS("ops7"), u, "guardian", reason="added by phone")
    (row,) = e.audit.rows()  # exactly one row for the change
    assert (row["action"], row["entity"], row["actor_id"]) == (
        "role.grant",
        "role_assignment",
        "ops7",
    )
    assert row["reason"] == "added by phone" and meta_of(row)["status"] == "intent"
    assert row["actor_roles"] == "opsadmin"
    e.service.revoke_role(OPS("ops7"), u, "guardian")
    assert [r["action"] for r in e.audit.role_intents()] == ["role.grant", "role.revoke"]
    assert len(e.audit.rows()) == 2


def test_the_audit_row_exists_before_the_change_lands():
    e = Env()
    u = e.user()
    e.tick.n, e.tick.crash_at = 0, 2  # write 1 = audit intent, write 2 = the history row
    from roles_helpers import Boom

    with pytest.raises(Boom):
        e.service.grant_role(OPS(), u, "teacher")
    assert len(e.audit.role_intents()) == 1 and e.rows(u, "teacher") == []


def test_system_changes_are_audited_as_system_with_the_job_name():
    e = Env()
    u = e.user()
    e.service.grant_role(SIGNUP, u, "parent")
    (row,) = e.audit.rows()
    assert row["actor_id"] is None and row["actor_roles"] == "system"
    assert meta_of(row)["job"] == "signup" and meta_of(row)["source"] == "system"


def test_audit_outage_refuses_the_change_and_writes_nothing():
    e = Env()
    u = e.user()
    e.repo.fail_audit = True
    with pytest.raises(Exception) as x:
        e.service.grant_role(OPS(), u, "teacher")
    assert getattr(x.value, "code", "") == "unavailable"
    assert e.labels(u) == set() and e.raw_repo.list("role_assignment") == []


def test_audit_outage_on_a_staff_revoke_leaves_the_label_and_the_row_untouched():
    e = Env()
    _, boss = e.superadmin()
    u = e.user()
    e.service.grant_role(boss, u, "opsadmin", reason="joined the team")
    e.repo.fail_audit = True
    with pytest.raises(Exception) as x:
        e.service.revoke_role(boss, u, "opsadmin", reason="left the team")
    assert getattr(x.value, "code", "") == "unavailable"
    assert "opsadmin" in e.labels(u) and e.active_rows(u, "opsadmin")
    assert e.raw_users.sessions_ended == []


def test_refused_changes_are_audited_as_refusals_with_the_actor():
    e = Env()
    u = e.user()
    with pytest.raises(Forbidden):
        e.service.grant_role(OPS("ops7"), u, "superadmin", reason="test reason")
    (row,) = e.audit.rows()
    assert row["action"] == "role.grant_refused" and row["actor_id"] == "ops7"
    assert e.raw_repo.list("role_assignment") == []


# ---- reasons (staff roles need one) --------------------------------------------------------
@pytest.mark.parametrize("reason", ["", "  ", "ab", "\u200b\u200b\u200b\u200b", "\u2800" * 5, None])
def test_staff_role_changes_need_a_real_reason_a_zero_width_one_does_not_count(reason):
    from hs_core.audit.errors import ReasonRequired

    e = Env()
    _, boss = e.superadmin()
    u = e.user()
    with pytest.raises(ReasonRequired):
        e.service.grant_role(boss, u, "curator", reason=reason)
    assert e.labels(u) == set() and e.rows(u, "curator") == []
    e.service.grant_role(boss, u, "curator", reason="joined the curriculum team")
    with pytest.raises(ReasonRequired):
        e.service.revoke_role(boss, u, "curator", reason=reason)
    assert "curator" in e.labels(u) and e.active_rows(u, "curator")


def test_mobile_role_changes_do_not_need_a_reason():
    e = Env()
    u = e.user()
    e.service.grant_role(OPS(), u, "teacher")
    e.service.revoke_role(OPS(), u, "teacher")
    assert e.labels(u) == set()


def test_the_audit_reasons_registry_lists_the_staff_actions_only():
    from hs_core.audit.registry import REASON_REQUIRED

    assert {"role.grant_staff", "role.revoke_staff"} <= REASON_REQUIRED
    assert "role.grant" not in REASON_REQUIRED and "role.revoke" not in REASON_REQUIRED


# ---- failure ordering (non-crash errors) --------------------------------------------------
def test_label_service_outage_on_grant_leaves_the_row_and_reports_incomplete_then_retry_finishes():
    e = Env()
    u = e.user()
    e.raw_users.available = False
    with pytest.raises(Exception) as x:  # get() also fails first: nothing is written
        e.service.grant_role(OPS(), u, "teacher")
    assert x.value.code == "unavailable" and e.rows(u, "teacher") == []
    e.raw_users.available = True
    e.users.inner.add_label = lambda *a, **k: (_ for _ in ()).throw(  # label call only fails
        __import__("hs_core.auth_otp.accounts", fromlist=["x"]).DirectoryUnavailable()
    )
    with pytest.raises(IncompleteChange):
        e.service.grant_role(OPS(), u, "teacher")
    assert len(e.active_rows(u, "teacher")) == 1 and e.labels(u) == set()
    del e.users.inner.add_label
    res = e.service.grant_role(OPS(), u, "teacher")
    assert res.repaired and e.labels(u) == {"teacher"} and len(e.rows(u, "teacher")) == 1
    assert [meta_of(r).get("op") for r in e.audit.rows()] == ["create", "repair"]


def test_role_changed_event_is_emitted_once_per_change_with_a_versioned_id():
    class Perm:
        def __init__(self):
            self.events = []

        def run(self, event, write):
            self.events.append(event)
            return write(), None

    e = Env()
    e.service.permissions = p = Perm()
    u = e.user()
    e.service.grant_role(OPS(), u, "teacher")
    e.service.grant_role(OPS(), u, "teacher")  # no-op: no event
    e.service.revoke_role(OPS(), u, "teacher")
    e.service.grant_role(OPS(), u, "teacher")
    kinds = [(ev.kind, ev.scopes[0].kind, ev.scopes[0].id) for ev in p.events]
    assert kinds == [("role_changed", "user", u)] * 3
    assert len({ev.event_id for ev in p.events}) == 3  # versioned: repeats are not deduped away


def test_permissions_recompute_sees_the_new_labels_through_the_real_service():
    from hs_core.permissions.service import PermissionService

    e = Env()
    e.service.permissions = PermissionService(e.raw_repo)
    u = e.user()
    e.service.grant_role(OPS(), u, "teacher")
    assert e.labels(u) == {"teacher"}


# ---- M06 integration -----------------------------------------------------------------------
def test_no_role_or_profile_write_bypasses_the_audited_repository():
    from pathlib import Path

    import hs_core
    from hs_core.audit.tripwire import find_unaudited_writes

    assert find_unaudited_writes(Path(hs_core.__file__).parent) == []


def test_a_role_change_without_an_audit_context_is_refused_before_anything_is_written():
    from hs_core.audit.errors import AuditContextMissing

    e = Env()
    u = e.user()
    with pytest.raises(AuditContextMissing):
        e.real_service.grant_role(OPS(), u, "teacher")  # no ctx, no job scope
    assert e.labels(u) == set() and e.raw_repo.list("role_assignment") == []


def test_the_service_over_a_raw_repository_still_audits_through_a_wrapper():
    e = Env()
    u = e.user()
    e.service.grant_role(OPS(), u, "teacher")
    assert e.audit.rows(), "a bare repository is wrapped, never written around"
