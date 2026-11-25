import pytest

from hs_core.auth import ROLES, roles_from_labels

H = lambda t: {"Authorization": f"Bearer {t}"}  # noqa: E731


def test_me_ok(client):
    r = client.get("/v1/me", headers=H("parent-token"))
    assert r.status_code == 200
    assert r.json() == {"user_id": "u_parent", "roles": ["parent"]}


def test_missing_header_401(client):
    r = client.get("/v1/me")
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "unauthenticated"
    assert r.headers["WWW-Authenticate"] == "Bearer"


@pytest.mark.parametrize("hdr", ["Basic abc", "Bearer", "Bearer ", "bearer", "Token x"])
def test_malformed_header_401(client, hdr):
    assert client.get("/v1/me", headers={"Authorization": hdr}).status_code == 401


def test_forged_token_401(client):
    r = client.get("/v1/me", headers=H("not.a.real.jwt"))
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "unauthenticated"


def test_expired_token_401(client):
    r = client.get("/v1/me", headers=H("expired-token"))
    assert r.status_code == 401


def test_wrong_role_403(client):
    r = client.get("/v1/admin/ping", headers=H("parent-token"))
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "forbidden"


def test_no_labels_403_on_guarded(client):
    assert client.get("/v1/admin/ping", headers=H("unlabelled-token")).status_code == 403
    assert client.get("/v1/me", headers=H("unlabelled-token")).json()["roles"] == []


def test_right_role_200(client):
    assert client.get("/v1/admin/ping", headers=H("admin-token")).status_code == 200


def test_superadmin_alone_does_not_pass_opsadmin_guard(client, verifier):
    verifier.add("sa", user_id="u_sa", labels=["superadmin"])
    assert client.get("/v1/admin/ping", headers=H("sa")).status_code == 403


def test_labels_map_to_roles_alphanumeric_only():
    assert roles_from_labels(["parent", "opsadmin", "ops_admin", "bogus", "Teacher"]) == [
        "opsadmin",
        "parent",
    ]
    assert set(ROLES) == {
        "parent", "guardian", "teacher", "opsadmin", "curator", "safeguardinglead", "superadmin",
    }  # fmt: skip


def test_health_needs_no_auth(client):
    assert client.get("/health").status_code == 200
    assert client.get("/ready").status_code == 200


def test_verifier_failure_is_401_not_500(client, verifier):
    def boom(token):
        raise RuntimeError("appwrite down")

    verifier.verify = boom
    r = client.get("/v1/me", headers=H("parent-token"))
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "auth_unavailable"
