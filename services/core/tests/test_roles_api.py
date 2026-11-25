"""A03: role guards read the VERIFIED account; /v1/me/access; role-change endpoints."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from hs_core.auth import AppwriteJwtVerifier, FakeTokenVerifier, require_roles
from hs_core.auth_otp.accounts import FakeUserDirectory
from hs_core.main import create_app
from hs_core.repository import InMemoryRepository
from hs_core.roles.model import ROLES, app_decision
from hs_core.settings import Settings

H = lambda t: {"Authorization": f"Bearer {t}"}  # noqa: E731


@pytest.fixture
def stack():
    repo, users, v = InMemoryRepository(), FakeUserDirectory(), FakeTokenVerifier()
    app = create_app(
        settings=Settings(_env_file=None, hs_env="dev"),
        repository=repo,
        verifier=v,
        user_directory=users,
    )
    return TestClient(app), repo, users, v


def add(stack, token, labels, status="active", teacher_status=None):
    _, repo, users, v = stack
    uid = users.add(f"+2567{len(users.users):08d}", list(labels))
    repo.create("user_profile", uid, {"phone": "x", "status": status})
    if teacher_status:
        repo.create("teacher_profile", f"tp{uid}"[:36], {"user_id": uid, "status": teacher_status})
    v.add(token, uid, list(labels))
    return uid


# ---- guards read the verified account only --------------------------------------------------
SPOOFS = [
    {"X-Roles": "opsadmin"},
    {"X-User-Roles": "superadmin"},
    {"X-Labels": "opsadmin"},
    {"X-Appwrite-Labels": "opsadmin"},
    {"Roles": "opsadmin"},
    {"X-Forwarded-User": "u_admin"},
    {"Cookie": "roles=opsadmin; labels=opsadmin"},
]


@pytest.mark.parametrize("extra", SPOOFS, ids=lambda h: next(iter(h)))
def test_client_supplied_role_headers_are_ignored_by_the_guards(client, extra):
    assert client.get("/v1/admin/ping", headers={**H("parent-token"), **extra}).status_code == 403
    assert client.get("/v1/me", headers={**H("parent-token"), **extra}).json()["roles"] == [
        "parent"
    ]


def test_role_claims_in_the_query_string_and_body_are_ignored(client):
    r = client.get("/v1/admin/ping?roles=opsadmin&labels=opsadmin", headers=H("parent-token"))
    assert r.status_code == 403
    r = client.post(
        "/v1/admin/ping",
        json={"roles": ["opsadmin"], "labels": ["opsadmin"]},
        headers=H("admin-token"),
    )
    assert r.status_code == 405  # no such write; and nothing reads roles from a body


@pytest.mark.parametrize(
    "labels",
    [
        ["ops_admin"],
        ["OpsAdmin"],
        ["OPSADMIN"],
        [" opsadmin"],
        ["opsadmin\n"],
        ["ops-admin"],
        ["admin"],
    ],
)
def test_variant_labels_grant_nothing_at_the_guard(client, verifier, labels):
    verifier.add("v", user_id="uv", labels=labels)
    assert client.get("/v1/admin/ping", headers=H("v")).status_code == 403
    assert client.get("/v1/me", headers=H("v")).json()["roles"] == []


def test_unknown_labels_are_ignored_but_real_ones_next_to_them_still_count(client, verifier):
    verifier.add("m", user_id="um", labels=["beta-tester", "opsadmin", "x_y"])
    r = client.get("/v1/me", headers=H("m"))
    assert r.json()["roles"] == ["opsadmin"]
    assert client.get("/v1/admin/ping", headers=H("m")).status_code == 200


def test_the_real_verifier_ignores_non_list_labels_and_reads_fresh_each_call():
    acct = MagicMock()
    v = AppwriteJwtVerifier("https://x/v1", "p", account_factory=lambda jwt: (MagicMock(), acct))
    acct.get.return_value = {"$id": "u1", "labels": "opsadmin", "status": True}
    assert v.verify("t").roles == []  # a bare string is not a list of labels
    acct.get.return_value = {"$id": "u1", "labels": {"opsadmin": True}, "status": True}
    assert v.verify("t").roles == []
    acct.get.return_value = {"$id": "u1", "labels": ["teacher"], "status": True}
    assert v.verify("t").roles == ["teacher"]
    acct.get.return_value = {"$id": "u1", "labels": ["teacher", "curator"], "status": True}
    assert v.verify("t").roles == ["curator", "teacher"]  # a label change shows at once
    acct.get.return_value = {"$id": "u1", "labels": [], "status": True}
    assert v.verify("t").roles == []  # and so does a removal


def test_require_roles_refuses_unknown_role_names_at_import_time():
    with pytest.raises(AssertionError):
        require_roles("ops_admin")


# ---- /v1/me/access -------------------------------------------------------------------------
def access(stack, token, app):
    return stack[0].get(f"/v1/me/access?app={app}", headers=H(token))


@pytest.mark.parametrize("role", ROLES)
@pytest.mark.parametrize("app", ["parent", "teacher", "admin"])
def test_access_decision_for_each_role_in_each_app(stack, role, app):
    add(stack, "t", [role])
    r = access(stack, "t", app)
    assert r.status_code == 200
    body = r.json()
    want = app_decision(app, [role], "active")
    assert body["decision"] == want.kind.value and body["suggested_app"] == want.suggested_app
    assert body["roles"] == [role]
    assert body["mfa_required"] is (app == "admin" and want.kind.value == "admit")
    assert r.headers["Cache-Control"] == "no-store"


def test_mixed_parent_and_teacher_is_admitted_in_both_apps(stack):
    add(stack, "t", ["parent", "teacher"])
    assert access(stack, "t", "parent").json()["decision"] == "admit"
    assert access(stack, "t", "teacher").json()["decision"] == "admit"
    assert access(stack, "t", "admin").json()["decision"] == "wrong_app"


def test_a_teacher_in_the_parent_app_is_told_which_app_to_use(stack):
    add(stack, "t", ["teacher"])
    b = access(stack, "t", "parent").json()
    assert b["decision"] == "wrong_app" and b["suggested_app"] == "teacher"


def test_suspended_teacher_gets_suspended_in_the_teacher_app_only(stack):
    add(stack, "t", ["teacher", "parent"], teacher_status="suspended")
    assert access(stack, "t", "teacher").json()["decision"] == "suspended"
    assert access(stack, "t", "parent").json()["decision"] == "admit"


def test_suspended_and_deactivated_accounts(stack):
    add(stack, "s", ["parent"], status="suspended")
    add(stack, "d", ["parent"], status="deactivated")
    assert access(stack, "s", "parent").json()["decision"] == "suspended"
    assert access(stack, "d", "parent").json()["decision"] == "no_role"


def test_soft_deleted_profile_is_not_admitted(stack):
    uid = add(stack, "x", ["parent"])
    stack[1].update("user_profile", uid, {"deleted_at": "2026-01-01T00:00:00+00:00"})
    assert access(stack, "x", "parent").json()["status"] == "deleted"
    assert access(stack, "x", "parent").json()["decision"] == "no_role"


def test_no_labels_means_no_role_and_unknown_app_is_422_and_unauthenticated_is_401(stack):
    add(stack, "n", [])
    assert access(stack, "n", "parent").json()["decision"] == "no_role"
    assert access(stack, "n", "ops").status_code == 422
    assert stack[0].get("/v1/me/access?app=parent").status_code == 401


def test_roles_refresh_when_the_labels_change(stack):
    uid = add(stack, "t", ["parent"])
    assert access(stack, "t", "teacher").json()["decision"] == "wrong_app"
    stack[3].add("t", uid, ["parent", "teacher"])  # Appwrite labels changed; same user
    assert access(stack, "t", "teacher").json()["decision"] == "admit"
    stack[3].add("t", uid, ["parent"])
    assert access(stack, "t", "teacher").json()["decision"] == "wrong_app"


def test_a_client_cannot_claim_a_role_through_the_access_query(stack):
    add(stack, "t", ["parent"])
    r = stack[0].get("/v1/me/access?app=admin&roles=opsadmin", headers=H("t"))
    assert r.json()["decision"] == "wrong_app" and r.json()["roles"] == ["parent"]


# ---- role-change endpoints ------------------------------------------------------------------
def post(stack, token, uid, action, body):
    return stack[0].post(f"/v1/admin/roles/{uid}/{action}", json=body, headers=H(token))


def test_superadmin_grants_and_revokes_a_staff_role_over_http(stack):
    add(stack, "sa", ["superadmin"])
    target = add(stack, "tg", [])
    r = post(
        stack, "sa", target, "grant", {"role": "curator", "reason": "joined the curriculum team"}
    )
    assert r.status_code == 200 and r.json()["roles"] == ["curator"] and r.json()["changed"]
    assert (
        post(stack, "sa", target, "grant", {"role": "curator", "reason": "joined the team"}).json()[
            "changed"
        ]
        is False
    )
    r = post(stack, "sa", target, "revoke", {"role": "curator", "reason": "left the team"})
    assert r.status_code == 200 and r.json()["roles"] == []


def test_opsadmin_cannot_grant_superadmin_over_http(stack):
    add(stack, "ops", ["opsadmin"])
    target = add(stack, "tg", [])
    r = post(stack, "ops", target, "grant", {"role": "superadmin", "reason": "please"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "forbidden"
    assert stack[2].users[target].labels == []


def test_self_escalation_is_refused_over_http(stack):
    uid = add(stack, "ops", ["opsadmin"])
    r = post(stack, "ops", uid, "grant", {"role": "superadmin", "reason": "please"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "self_change"
    sa = add(stack, "sa", ["superadmin"])
    assert post(stack, "sa", sa, "grant", {"role": "opsadmin", "reason": "please"}).json()["error"][
        "code"
    ] == ("self_change")


@pytest.mark.parametrize("token", ["parent-token", "teacher-token", "unlabelled-token"])
def test_non_admin_callers_are_403_at_the_guard(client, token):
    r = client.post("/v1/admin/roles/someone/grant", json={"role": "parent"}, headers=H(token))
    assert r.status_code == 403


def test_unauthenticated_is_401(client):
    assert client.post("/v1/admin/roles/x/grant", json={"role": "parent"}).status_code == 401


@pytest.mark.parametrize(
    "body",
    [
        {"role": "teacher", "actor": "someone"},
        {"role": "teacher", "roles": ["superadmin"]},
        {"role": "teacher", "actor_roles": ["superadmin"]},
        {"role": ["teacher"]},
        {"role": 5},
        {},
        {"role": "t" * 33},
    ],
)
def test_body_spoofing_and_malformed_bodies_are_422_and_change_nothing(stack, body):
    add(stack, "ops", ["opsadmin"])
    target = add(stack, "tg", [])
    assert post(stack, "ops", target, "grant", body).status_code == 422
    assert stack[2].users[target].labels == []


def test_unknown_role_and_unknown_user_over_http(stack):
    add(stack, "ops", ["opsadmin"])
    assert post(stack, "ops", "nobody", "grant", {"role": "teacher"}).status_code == 404
    target = add(stack, "tg", [])
    r = post(stack, "ops", target, "grant", {"role": "ops_admin"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "unknown_role"


def test_suspended_target_is_409_and_last_superadmin_is_409(stack):
    add(stack, "ops", ["opsadmin"])
    susp = add(stack, "s", [], status="suspended")
    assert post(stack, "ops", susp, "grant", {"role": "teacher"}).status_code == 409
    sa = add(stack, "sa", ["superadmin"])
    stack[1].create(
        "role_assignment",
        "r1",
        {
            "user_id": sa,
            "role": "superadmin",
            "granted_at": "2026-01-01T00:00:00+00:00",
            "active_key": f"{sa}:superadmin",
        },
    )
    other = add(stack, "sa2", ["superadmin"])  # label only, no row: not counted as a second
    r = post(stack, "sa2", sa, "revoke", {"role": "superadmin", "reason": "rotating admins"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "last_superadmin" and other


def test_a_role_change_writes_exactly_one_audit_row_for_the_verified_actor(stack):
    ops = add(stack, "ops", ["opsadmin"])
    target = add(stack, "tg", [])
    post(stack, "ops", target, "grant", {"role": "teacher", "reason": "approved"})
    rows = [r for r in stack[1].list("audit_log") if r["entity"] == "role_assignment"]
    assert len(rows) == 1 and rows[0]["reason"] == "approved"
    assert rows[0]["actor_id"] == ops and rows[0]["action"] == "role.grant"
    assert rows[0]["actor_roles"] == "opsadmin"


def test_the_audit_actor_comes_from_the_token_never_the_body_or_headers(stack):
    ops = add(stack, "ops", ["opsadmin"])
    other = add(stack, "other", ["superadmin"])
    target = add(stack, "tg", [])
    r = stack[0].post(
        f"/v1/admin/roles/{target}/grant",
        json={"role": "teacher", "reason": "approved"},
        headers={**H("ops"), "X-Actor-Id": other, "X-Request-ID": "abc"},
    )
    assert r.status_code == 200
    (row,) = [x for x in stack[1].list("audit_log") if x["entity"] == "role_assignment"]
    assert row["actor_id"] == ops and row["actor_id"] != other
    r = post(
        stack, "ops", target, "revoke", {"role": "teacher", "reason": "x y z", "actor_id": other}
    )
    assert r.status_code == 422


def test_staff_changes_over_http_need_a_reason(stack):
    add(stack, "sa", ["superadmin"])
    target = add(stack, "tg", [])
    for reason in ["", "\u200b\u200b\u200b", "ab"]:
        r = post(stack, "sa", target, "grant", {"role": "curator", "reason": reason})
        assert r.status_code == 422 and r.json()["error"]["code"] == "reason_required"
    assert stack[2].users[target].labels == []


def test_audit_store_down_over_http_means_no_role_and_no_label(stack):
    add(stack, "ops", ["opsadmin"])
    target = add(stack, "tg", [])
    real = stack[1].create

    def down(table, *a, **k):
        if table == "audit_log":
            from hs_core.repository import RepositoryUnavailable

            raise RepositoryUnavailable("down")
        return real(table, *a, **k)

    stack[1].create = down
    r = post(stack, "ops", target, "grant", {"role": "teacher"})
    assert r.status_code == 503
    assert stack[2].users[target].labels == [] and not stack[1].list("role_assignment")


# ---- per-actor rate limit (round 2) ----------------------------------------------------------
def test_role_changes_are_rate_limited_per_actor_and_reset_after_an_hour():
    from datetime import timedelta

    repo, users, v = InMemoryRepository(), FakeUserDirectory(), FakeTokenVerifier()
    app = create_app(
        settings=Settings(_env_file=None, hs_env="dev", role_changes_per_hour=3),
        repository=repo,
        verifier=v,
        user_directory=users,
    )
    s = (TestClient(app), repo, users, v)
    add(s, "ops", ["opsadmin"])
    add(s, "ops2", ["opsadmin"])
    targets = [add(s, f"t{i}", []) for i in range(6)]
    codes = [post(s, "ops", t, "grant", {"role": "teacher"}).status_code for t in targets[:5]]
    assert codes == [200, 200, 200, 429, 429]
    r = post(s, "ops", targets[5], "grant", {"role": "teacher"})
    assert r.headers["Retry-After"].isdigit() and r.json()["error"]["code"] == "rate_limited"
    assert post(s, "ops2", targets[5], "grant", {"role": "teacher"}).status_code == 200  # per actor
    clock = app.state.roles.clock
    app.state.role_limiter.clock = lambda: clock() + timedelta(hours=1, seconds=5)
    assert post(s, "ops", targets[3], "grant", {"role": "teacher"}).status_code == 200


def test_the_rate_limit_setting_is_range_checked():
    assert Settings(_env_file=None, role_changes_per_hour=0).problems()
    assert Settings(_env_file=None, role_changes_per_hour=60).problems() == []
