"""A03: the pure role model (roles_for, app_admits, admin_admits) against the shared fixture."""

from __future__ import annotations

import itertools
import json
from pathlib import Path

import pytest

from hs_core.roles import model
from hs_core.roles.model import (
    ADMIN_ROUTES,
    ROLES,
    STAFF_ROLES,
    AppDecision,
    admin_admits,
    app_admits,
    app_decision,
    clean_labels,
    roles_for,
)

FIXTURE = json.loads(
    (Path(__file__).resolve().parents[3] / "docs" / "ops" / "roles-matrix.json").read_text()
)


def test_fixture_role_lists_match_the_code():
    assert tuple(FIXTURE["roles"]) == ROLES
    assert tuple(FIXTURE["staff_roles"]) == STAFF_ROLES
    assert {k: tuple(v) for k, v in FIXTURE["apps"].items()} == model.APP_ROLES


def test_labels_are_alphanumeric_lowercase():
    assert all(r.isalnum() and r.islower() and "_" not in r for r in ROLES)


@pytest.mark.parametrize("case", FIXTURE["label_cases"], ids=lambda c: "|".join(c["labels"]))
def test_label_cases_from_the_shared_fixture(case):
    assert sorted(clean_labels(case["labels"])) == sorted(case["roles"])
    assert sorted(roles_for({"labels": case["labels"]})) == sorted(case["roles"])


@pytest.mark.parametrize("junk", [None, 5, "opsadmin", b"opsadmin", {"opsadmin": 1}, [None, 7, {}]])
def test_malformed_label_values_grant_nothing(junk):
    # A bare string is NOT a list of labels: iterating it would yield characters.
    assert clean_labels(junk) == frozenset()


def test_unknown_and_case_variant_labels_are_ignored_among_real_ones():
    assert clean_labels(["teacher", "Teacher", "ops_admin", "x"]) == {"teacher"}


@pytest.mark.parametrize("status", ["suspended", "deactivated", "deleted", "blocked", ""])
def test_roles_for_inactive_users_is_empty(status):
    assert roles_for({"labels": ["parent", "opsadmin"], "status": status}) == frozenset()


def test_roles_for_soft_deleted_user_is_empty():
    assert (
        roles_for({"labels": ["parent"], "status": "active", "deleted_at": "2026-01-01"}) == set()
    )


def test_roles_for_accepts_objects_and_missing_status_means_active():
    class U:
        labels = ["teacher"]

    assert roles_for(U()) == {"teacher"}
    assert roles_for({"labels": ["teacher"]}) == {"teacher"}
    assert roles_for(None) == frozenset()


# ---- role x app matrix (all 128 subsets of the 7 roles, every app) ------------------------
def _expected(app: str, roles: set[str]) -> bool:
    return bool(roles & set(FIXTURE["apps"][app]))


ALL_SUBSETS = [set(c) for n in range(len(ROLES) + 1) for c in itertools.combinations(ROLES, n)]


@pytest.mark.parametrize("app", ["parent", "teacher", "admin"])
def test_every_role_combination_against_every_app(app):
    for roles in ALL_SUBSETS:
        assert app_admits(app, roles) is _expected(app, roles), (app, roles)


@pytest.mark.parametrize("app", ["", "Parent", "ops", "parent ", None, 3, "admin_web"])
def test_unknown_app_admits_nobody(app):
    assert app_admits(app, set(ROLES)) is False


def test_single_roles_each_app():
    assert app_admits("parent", {"parent"}) and app_admits("parent", {"guardian"})
    assert not app_admits("parent", {"teacher"})
    assert app_admits("teacher", {"teacher"})
    assert not app_admits("teacher", {"parent", "guardian"})
    for r in STAFF_ROLES:
        assert app_admits("admin", {r})
        assert not app_admits("parent", {r}) and not app_admits("teacher", {r})
    assert not app_admits("admin", {"parent", "guardian", "teacher"})


def test_mixed_parent_and_teacher_user_is_admitted_to_both_mobile_apps_not_admin():
    both = {"parent", "teacher"}
    assert app_admits("parent", both) and app_admits("teacher", both)
    assert not app_admits("admin", both)


# ---- decisions: what the app should show -------------------------------------------------
def test_decision_admit():
    d = app_decision("parent", ["parent"], "active")
    assert d.kind is AppDecision.ADMIT and d.suggested_app is None


def test_decision_wrong_app_names_the_right_app():
    d = app_decision("parent", ["teacher"], "active")
    assert d.kind is AppDecision.WRONG_APP and d.suggested_app == "teacher"
    d = app_decision("teacher", ["guardian"], "active")
    assert d.kind is AppDecision.WRONG_APP and d.suggested_app == "parent"


def test_decision_staff_in_a_mobile_app_is_wrong_app_pointing_to_admin_web():
    d = app_decision("parent", ["opsadmin"], "active")
    assert d.kind is AppDecision.WRONG_APP and d.suggested_app == "admin"


def test_decision_mixed_user_in_wrong_app_choice_only_when_not_admitted():
    assert app_decision("teacher", ["parent", "teacher"], "active").kind is AppDecision.ADMIT


def test_decision_no_role_is_not_admitted_without_a_suggestion():
    d = app_decision("parent", [], "active")
    assert d.kind is AppDecision.NO_ROLE and d.suggested_app is None


def test_decision_suspended_teacher_is_suspended_not_wrong_app():
    d = app_decision("teacher", ["teacher"], "suspended")
    assert d.kind is AppDecision.SUSPENDED


def test_decision_suspended_user_with_wrong_role_is_still_wrong_app():
    # Do not tell someone about a suspension of a role they do not hold in this app.
    assert app_decision("teacher", ["parent"], "suspended").kind is AppDecision.WRONG_APP


def test_decision_deactivated_or_deleted_is_not_admitted():
    assert app_decision("parent", ["parent"], "deactivated").kind is AppDecision.NO_ROLE
    assert app_decision("parent", ["parent"], "deleted").kind is AppDecision.NO_ROLE


# ---- admin route table --------------------------------------------------------------------
def test_admin_routes_equal_the_shared_fixture():
    assert {h: tuple(r) for h, r in ADMIN_ROUTES.items()} == {
        r["href"]: tuple(r["roles"]) for r in FIXTURE["admin_routes"]
    }


def test_admin_admits_every_route_for_every_role_subset():
    for route in FIXTURE["admin_routes"]:
        for roles in ALL_SUBSETS:
            want = bool(roles & set(route["roles"]))
            assert admin_admits(route["href"], roles) is want, (route["href"], roles)


@pytest.mark.parametrize(
    "case", FIXTURE["admin_path_cases"], ids=lambda c: c["path"] + str(c["roles"])
)
def test_admin_path_cases_from_the_fixture(case):
    assert admin_admits(case["path"], case["roles"]) is case["admits"]


def test_admin_admits_denies_non_staff_roles_and_junk():
    assert not admin_admits("/", {"parent", "teacher", "guardian"})
    assert not admin_admits("/", {"ops_admin", "OpsAdmin"})
    assert not admin_admits("/payments", None)
    assert not admin_admits("", {"opsadmin"})
    assert not admin_admits("payments", {"opsadmin"})
    assert not admin_admits("/payments/../settings", {"opsadmin"})


def test_superadmin_alone_is_not_implicit_ops(  # SR 6.2, ADR 0012: no blanket power
):
    assert not admin_admits("/payments", {"superadmin"})
    assert admin_admits("/settings", {"superadmin"})


def test_every_admin_route_admits_at_least_one_staff_role_and_only_staff_roles():
    for route, roles in ADMIN_ROUTES.items():
        assert roles and set(roles) <= set(STAFF_ROLES), route
