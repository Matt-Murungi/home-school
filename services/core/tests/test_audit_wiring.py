"""M06 (#41): wiring: app state, request context (trusted IP, verified actor), F04 job context,
A01 sign-up rows, and the new settings."""

import json

import pytest
from fastapi import Depends, Request
from fastapi.testclient import TestClient

from audit_helpers import audit_rows
from hs_core.audit import AuditContext, AuditContextMissing, AuditedRepository, AuditService
from hs_core.audit.api import audit_context
from hs_core.auth import FakeTokenVerifier
from hs_core.auth_otp.accounts import FakeUserDirectory
from hs_core.auth_otp.sms import FakeSmsSender
from hs_core.jobs.model import Done, JobSpec
from hs_core.jobs.runner import Runner
from hs_core.main import create_app
from hs_core.repository import InMemoryRepository
from hs_core.settings import ConfigError, Settings, load_settings


def meta(row):
    return json.loads(row["after"])["meta"]


def make_app(trust_proxy_ip=False, **settings_kw):
    raw = InMemoryRepository()
    v = FakeTokenVerifier()
    v.add("admin", "u_admin", ["opsadmin"])
    v.add("parent", "u_parent", ["parent"])
    sms = FakeSmsSender()
    app = create_app(
        Settings(_env_file=None, hs_env="dev", otp_pepper="x" * 40, **settings_kw),
        repository=raw,
        verifier=v,
        trust_proxy_ip=trust_proxy_ip,
        sms_sender=sms,
        user_directory=FakeUserDirectory(),
    )
    app.state.test_sms = sms
    raw.create("tier", "t1", {"rate": 1})

    @app.put("/test/tier/{tid}")
    def put_tier(
        tid: str,
        body: dict,
        request: Request,
        ctx: AuditContext = Depends(audit_context),  # noqa: B008
    ):
        return request.app.state.repository.update("tier", tid, body, ctx=ctx)

    return app, raw


class TestAppState:
    def test_the_app_repository_is_the_audited_choke_point(self):
        app, raw = make_app()
        assert isinstance(app.state.repository, AuditedRepository)
        assert isinstance(app.state.audit, AuditService)
        assert app.state.repository._inner is raw

    def test_audited_tables_cannot_be_written_without_a_context_through_the_app_repository(self):
        app, raw = make_app()
        with pytest.raises(AuditContextMissing):
            app.state.repository.update("tier", "t1", {"rate": 5})
        assert raw.get("tier", "t1")["rate"] == 1

    def test_other_tables_still_work_for_existing_code(self):
        app, raw = make_app()
        app.state.repository.create("job_run", "j", {"n": 1})
        assert raw.get("job_run", "j")["n"] == 1


class TestRequestContext:
    def test_actor_roles_request_id_and_trusted_ip_come_from_the_verified_request(self):
        app, raw = make_app(trust_proxy_ip=True)
        c = TestClient(app)
        r = c.put(
            "/test/tier/t1",
            json={"rate": 7, "actor_id": "u_evil"},
            headers={"Authorization": "Bearer admin", "X-Request-ID": "rid-42",
                     "Fly-Client-IP": "198.51.100.77"},
        )  # fmt: skip
        assert r.status_code == 200
        row = audit_rows(raw)[0]
        assert row["actor_id"] == "u_admin" and row["actor_roles"] == "opsadmin"
        assert row["request_id"] == "rid-42" and row["ip"] == "198.51.100.77"
        assert meta(row)["source"] == "admin"

    def test_fly_client_ip_is_ignored_when_not_behind_the_proxy(self):
        app, raw = make_app(trust_proxy_ip=False)
        TestClient(app).put(
            "/test/tier/t1", json={"rate": 7},
            headers={"Authorization": "Bearer admin", "Fly-Client-IP": "198.51.100.77"},
        )  # fmt: skip
        assert audit_rows(raw)[0]["ip"] != "198.51.100.77"

    def test_unauthenticated_requests_write_nothing(self):
        app, raw = make_app()
        r = TestClient(app).put("/test/tier/t1", json={"rate": 9})
        assert r.status_code == 401
        assert raw.get("tier", "t1")["rate"] == 1 and raw.list("audit_log") == []

    def test_a_retried_http_request_with_the_same_request_id_audits_once(self):
        app, raw = make_app()
        c = TestClient(app)
        h = {"Authorization": "Bearer admin", "X-Request-ID": "same-id"}
        c.put("/test/tier/t1", json={"rate": 7}, headers=h)
        c.put("/test/tier/t1", json={"rate": 7}, headers=h)
        assert len(audit_rows(raw)) == 1


class TestJobContext:
    def test_a_job_that_writes_an_audited_table_is_logged_as_system_with_its_job_name(self):
        raw = InMemoryRepository()
        raw.create("tier", "t1", {"rate": 1})
        svc = AuditService(raw)
        audited = AuditedRepository(raw, svc)

        def handler(ctx):
            audited.update("tier", "t1", {"rate": 2}, action="tier.evaluate")
            return Done(items=1)

        from jobs_helpers import FakeClock

        clock = FakeClock()
        spec = JobSpec("tier-evaluation", handler)
        runner = Runner(raw, [spec], clock=clock)
        runner.enqueue("tier-evaluation", "tier-evaluation:2027-01", run_at=clock())
        res = runner.tick()
        assert res.completed, res
        row = audit_rows(raw, entity="tier")[0]
        assert row["actor_id"] is None and row["actor_roles"] == "system"
        assert meta(row)["job"] == "tier-evaluation" and meta(row)["source"] == "system"
        assert raw.get("tier", "t1")["rate"] == 2

    def test_job_context_does_not_leak_outside_the_job(self):
        raw = InMemoryRepository()
        audited = AuditedRepository(raw, AuditService(raw))
        raw.create("tier", "t1", {"rate": 1})
        with pytest.raises(AuditContextMissing):
            audited.update("tier", "t1", {"rate": 3})

    def test_a_retried_job_chunk_audits_once(self):
        raw = InMemoryRepository()
        raw.create("tier", "t1", {"rate": 1})
        svc = AuditService(raw)
        audited = AuditedRepository(raw, svc)
        calls = {"n": 0}

        def handler(ctx):
            calls["n"] += 1
            audited.update("tier", "t1", {"rate": 2})
            if calls["n"] == 1:
                raise RuntimeError("crash after the write, before the job is marked done")
            return Done(items=1)

        from jobs_helpers import FakeClock

        clock = FakeClock()
        spec = JobSpec("j", handler, max_attempts=3)
        runner = Runner(raw, [spec], clock=clock)
        runner.enqueue("j", "j:1", run_at=clock())
        runner.tick()
        clock.advance(minutes=10)
        runner.tick()
        assert calls["n"] == 2
        assert len(audit_rows(raw, entity="tier")) == 1


class TestSignUpIsAudited:
    def test_otp_sign_up_writes_profile_and_role_rows_without_the_phone_number(self):
        app, raw = make_app()
        c = TestClient(app)
        sms = app.state.test_sms
        c.post("/v1/auth/otp/request", json={"phone": "0772123456", "app": "parent"})
        import re

        code = re.search(r"(?<!\d)(\d{6})(?!\d)", sms.sent[-1].body).group(1)
        r = c.post(
            "/v1/auth/otp/verify", json={"phone": "0772123456", "code": code, "app": "parent"}
        )
        assert r.status_code == 200
        rows = audit_rows(raw)
        entities = sorted(x["entity"] for x in rows)
        assert entities == ["role_assignment", "user_profile"]
        assert all(x["actor_id"] == r.json()["user_id"] for x in rows)
        assert "772123456" not in json.dumps(rows)


class TestSettings:
    def test_defaults(self):
        s = Settings(_env_file=None)
        assert s.audit_lead_blanket_read is True
        assert s.audit_recover_lookback_hours == 6 and s.audit_recover_grace_s == 300

    def test_flag_is_read_from_the_environment(self, monkeypatch):
        monkeypatch.setenv("AUDIT_LEAD_BLANKET_READ", "false")
        assert load_settings(env_file=None).audit_lead_blanket_read is False

    @pytest.mark.parametrize(
        ("name", "bad"),
        [("audit_recover_lookback_hours", 0), ("audit_recover_lookback_hours", 10_000),
         ("audit_recover_grace_s", 5), ("audit_recover_grace_s", 100_000)],
    )  # fmt: skip
    def test_out_of_range_values_fail_startup_naming_the_variable(self, name, bad, monkeypatch):
        monkeypatch.setenv(name.upper(), str(bad))
        with pytest.raises(ConfigError, match=name.upper()):
            load_settings(env_file=None)

    def test_flag_off_removes_the_lead_grant_in_the_app(self):
        app, raw = make_app(audit_lead_blanket_read=False)
        TestClient(app).put(
            "/test/tier/t1", json={"rate": 2}, headers={"Authorization": "Bearer admin"}
        )
        assert 'read("label:safeguardinglead")' not in audit_rows(raw)[0]["$permissions"]
