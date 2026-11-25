"""M06 (#41) blocker 4a: `POST /v1/admin/audit/event`, the path the admin app's search audit takes
(H09 `AuditSink`). Ids, counts and entity types only; fails closed; actor/IP/request id are the
server's."""

import json

import pytest
from fastapi.testclient import TestClient

from audit_helpers import audit_rows
from hs_core.auth import FakeTokenVerifier
from hs_core.main import create_app
from hs_core.repository import InMemoryRepository
from hs_core.settings import Settings

URL = "/v1/admin/audit/event"
OK = {
    "event": "admin.search",
    "entity_types": ["parent", "payment"],
    "query_kind": "phone",
    "results": {"parent": 3, "payment": 0},
}


def meta(row):
    return json.loads(row["after"])["meta"]


def make(repo=None, **kw):
    raw = repo or InMemoryRepository()
    v = FakeTokenVerifier()
    for tok, uid, labels in [
        ("ops", "u_ops", ["opsadmin"]),
        ("cur", "u_cur", ["curator"]),
        ("sg", "u_sg", ["safeguardinglead"]),
        ("sup", "u_sup", ["superadmin"]),
        ("par", "u_par", ["parent"]),
        ("tea", "u_tea", ["teacher"]),
    ]:
        v.add(tok, uid, labels)
    app = create_app(
        Settings(_env_file=None, hs_env="dev", otp_pepper="x" * 40, **kw),
        repository=raw, verifier=v, trust_proxy_ip=True,
    )  # fmt: skip
    return TestClient(app), raw


def post(c, body, token="ops", **headers):
    h = {"Authorization": f"Bearer {token}", **headers} if token else headers
    return c.post(URL, json=body, headers=h)


class TestAuthorisation:
    def test_no_token_is_401_and_writes_nothing(self):
        c, raw = make()
        assert post(c, OK, token=None).status_code == 401
        assert raw.list("audit_log") == []

    @pytest.mark.parametrize("token", ["par", "tea"])
    def test_non_admin_roles_are_403(self, token):
        c, raw = make()
        assert post(c, OK, token=token).status_code == 403
        assert raw.list("audit_log") == []

    @pytest.mark.parametrize("token", ["ops", "cur", "sg", "sup"])
    def test_admin_roles_are_accepted(self, token):
        c, raw = make()
        assert post(c, OK, token=token).status_code == 200
        assert len(raw.list("audit_log")) == 1


class TestWhatIsWritten:
    def test_one_row_with_server_derived_actor_ip_and_request_id(self):
        c, raw = make()
        r = post(
            c, {**OK, "actor_id": "u_evil", "ip": "1.1.1.1"}, **{"Fly-Client-IP": "203.0.113.7",
            "X-Request-ID": "corr-1"},
        )  # fmt: skip
        assert r.status_code == 422  # unknown fields are refused, never trusted
        r = post(c, OK, **{"Fly-Client-IP": "203.0.113.7", "X-Request-ID": "corr-1"})
        assert r.status_code == 200 and r.json() == {"recorded": True}
        (row,) = audit_rows(raw)
        assert row["actor_id"] == "u_ops" and row["actor_roles"] == "opsadmin"
        assert row["action"] == "admin.search" and row["ip"] == "203.0.113.7"
        assert row["request_id"] == "corr-1"
        assert json.loads(row["after"])["fields"] == {
            "entity_types": ["parent", "payment"],
            "query_kind": "phone",
            "results": {"parent": 3, "payment": 0},
        }

    def test_two_searches_with_one_client_request_id_are_two_rows(self):
        c, raw = make()
        post(c, OK, **{"X-Request-ID": "fixed"})
        post(c, {**OK, "results": {"parent": 9, "payment": 1}}, **{"X-Request-ID": "fixed"})
        assert len(audit_rows(raw)) == 2

    def test_two_admins_with_one_client_request_id_are_two_rows(self):
        c, raw = make()
        post(c, OK, token="ops", **{"X-Request-ID": "fixed"})
        post(c, OK, token="sg", **{"X-Request-ID": "fixed"})
        assert {r["actor_id"] for r in audit_rows(raw)} == {"u_ops", "u_sg"}


class TestOnlyIdsCountsAndTypes:
    @pytest.mark.parametrize(
        "body",
        [
            {**OK, "query": "Grace Namuli"},
            {**OK, "q": "0772123456"},
            {**OK, "results": {"parent": [{"name": "Grace"}]}},
            {**OK, "results": {"parent": "3"}},
            {**OK, "results": {"parent": -1}},
            {**OK, "results": {"parent": 10**9}},
            {**OK, "results": {"phone": 1}},
            {**OK, "entity_types": ["parent", "Grace"]},
            {**OK, "entity_types": ["parent"] * 50},
            {**OK, "query_kind": "Grace Namuli"},
            {**OK, "event": "admin.delete_everything"},
            {**OK, "event": "tier.update"},
            {"event": "admin.search"},
            {**OK, "entity_types": "parent"},
            {**OK, "ids": ["p1"]},
            [],
            "text",
        ],
    )
    def test_anything_else_is_refused_and_nothing_is_written(self, body):
        c, raw = make()
        assert post(c, body).status_code == 422
        assert raw.list("audit_log") == []

    def test_a_query_text_canary_never_reaches_the_log(self):
        c, raw = make()
        post(c, {**OK, "query": "CANARY-GRACE"})
        post(c, OK)
        assert "CANARY-GRACE" not in json.dumps(raw.list("audit_log"))

    def test_oversized_body_is_refused(self):
        c, raw = make()
        headers = {"Authorization": "Bearer ops", "Content-Type": "application/json"}
        r = c.post(
            URL, content=b'{"event":"admin.search","x":"' + b"a" * 20_000 + b'"}', headers=headers
        )
        assert r.status_code in (413, 422) and raw.list("audit_log") == []


class TestFailsClosed:
    def test_audit_store_down_is_503_not_200(self):
        c, raw = make()
        raw.available = False
        r = post(c, OK)
        assert r.status_code in (503,)
        raw.available = True
        assert raw.list("audit_log") == []

    def test_the_error_body_does_not_echo_the_input(self):
        c, _ = make()
        r = post(c, {**OK, "query": "CANARY-GRACE"})
        assert "CANARY-GRACE" not in r.text


class TestResultsMatchTheTypes:
    def test_results_for_a_type_not_searched_are_refused(self):
        c, raw = make()
        body = {**OK, "results": {"parent": 1, "child": 2}}
        assert post(c, body).status_code == 422 and raw.list("audit_log") == []


class TestRateLimit:
    def test_an_admin_is_limited_per_minute_and_others_are_not(self):
        c, raw = make(admin_audit_rate_per_min=3)
        assert [post(c, OK).status_code for _ in range(5)] == [200, 200, 200, 429, 429]
        assert len(raw.list("audit_log")) == 3
        assert post(c, OK, token="sg").status_code == 200  # another admin

    def test_the_429_has_retry_after_and_writes_nothing(self):
        c, raw = make(admin_audit_rate_per_min=1)
        post(c, OK)
        r = post(c, OK)
        assert r.status_code == 429 and int(r.headers["Retry-After"]) >= 1
        assert len(raw.list("audit_log")) == 1

    def test_the_window_slides(self):
        from hs_core.audit.admin_events import RateLimiter

        t = [0.0]
        rl = RateLimiter(2, now=lambda: t[0])
        assert rl.check("a") == 0 and rl.check("a") == 0 and rl.check("a") > 0
        t[0] = 61
        assert rl.check("a") == 0

    def test_default_is_sixty_a_minute(self):
        assert Settings(_env_file=None).admin_audit_rate_per_min == 60

    def test_setting_is_range_checked(self):
        assert any(
            "ADMIN_AUDIT_RATE_PER_MIN" in p
            for p in Settings(_env_file=None, admin_audit_rate_per_min=0).problems()
        )
