"""N02 (#127): which adapter the deployed config selects (never fake success), startup validation,
device token endpoints (registered on login, removed on logout), schedule and permissions."""

import json
import logging

import pytest
from fastapi.testclient import TestClient

from hs_core.jobs.alerts import FakeAlertSink
from hs_core.jobs.schedule import JOBS
from hs_core.main import create_app
from hs_core.notifications import build_notification_service
from hs_core.notifications.channels import UnconfiguredChannel, UnconfiguredEmailSender
from hs_core.notifications.fcm import FcmPushChannel
from hs_core.notifications.resend import ResendEmailSender
from hs_core.notifications.templates import TemplateStore
from hs_core.permissions.rules import SPECS
from hs_core.repository import InMemoryRepository
from hs_core.settings import ConfigError, Settings, load_settings
from n02_helpers import (
    API_KEY,
    CANARY_TOKEN,
    NOW,
    WEBHOOK_SECRET,
    service_account_json,
)
from notify_helpers import PARENT, TEACHER

DEPLOYED = dict(
    appwrite_endpoint="https://appwrite.example.org/v1",
    appwrite_project_id="p",
    appwrite_database_id="d",
    appwrite_api_key="k" * 40,
    job_tick_secret="t" * 40,
    otp_pepper="p" * 40,
)
EMAIL = dict(
    resend_api_key=API_KEY,
    resend_webhook_secret=WEBHOOK_SECRET,
    email_from="no-reply@mail.example.org",
)


def settings(env="dev", **kw):
    base = dict(DEPLOYED) if env != "dev" else {}
    base.update(kw)
    return Settings(_env_file=None, hs_env=env, **base)


def build(s):
    repo = InMemoryRepository()
    svc = build_notification_service(s, repo, FakeAlertSink(), clock=lambda: NOW)
    TemplateStore(repo, clock=lambda: NOW).seed_defaults()
    return svc, repo


# ------------------------------------------------------------------ adapter selection
@pytest.mark.parametrize("env", ["dev", "staging", "production"])
def test_no_credentials_keeps_the_placeholders_everywhere(env):
    svc, _ = build(settings(env))
    assert isinstance(svc.push, UnconfiguredChannel)
    assert isinstance(svc.email.sender, UnconfiguredEmailSender)


@pytest.mark.parametrize("env", ["staging", "production"])
def test_deployed_without_credentials_never_fakes_success(env):
    svc, repo = build(settings(env))
    svc.devices.register("u_teacher", "teacher", "android", CANARY_TOKEN)
    svc.directory = type("D", (), {"email_for": lambda self, u: "t@example.org"})()
    from notify_helpers import CANCEL_VARS

    svc.send("session_cancelled_teacher", TEACHER, CANCEL_VARS, "k-deployed")
    rows = {r["channel"]: r for r in repo.list("notification")}
    assert rows["push"]["status"] == "fallback" and rows["push"]["last_error"].startswith(
        "permanent"
    )
    assert rows["email"]["status"] == "failed"  # the fallback email also fails: nothing "sent"
    assert not any(r["status"] in ("sent",) for r in rows.values())
    assert any("notification_delivery_failed" in str(a) for a in svc.alerts.sent)


@pytest.mark.parametrize("env", ["dev", "staging", "production"])
def test_complete_credentials_select_the_real_adapters(env):
    svc, _ = build(settings(env, fcm_service_account_json=service_account_json(), **EMAIL))
    assert isinstance(svc.push, FcmPushChannel)
    assert isinstance(svc.email.sender, ResendEmailSender)
    assert svc.email.suppression is not None  # suppression is wired in


def test_each_provider_is_independent():
    svc, _ = build(settings("staging", **EMAIL))
    assert isinstance(svc.email.sender, ResendEmailSender)
    assert isinstance(svc.push, UnconfiguredChannel)
    svc, _ = build(settings("staging", fcm_service_account_json=service_account_json()))
    assert isinstance(svc.push, FcmPushChannel)
    assert isinstance(svc.email.sender, UnconfiguredEmailSender)


# ------------------------------------------------------------------ startup validation
@pytest.mark.parametrize(
    "kw,needle",
    [
        (dict(resend_api_key=API_KEY), "half configured"),
        (dict(resend_webhook_secret=WEBHOOK_SECRET), "half configured"),
        (dict(email_from="no-reply@mail.example.org"), "half configured"),
        (dict(**{**EMAIL, "resend_webhook_secret": "whsec_!!"}), "RESEND_WEBHOOK_SECRET"),
        (dict(**{**EMAIL, "email_from": "Name <a@b.co>"}), "EMAIL_FROM"),
        (dict(**{**EMAIL, "email_reply_to": "a@b.co,c@d.co"}), "EMAIL_REPLY_TO"),
        (dict(email_reply_to="support@example.org"), "EMAIL_REPLY_TO"),
        (dict(fcm_service_account_json="{nope"), "FCM_SERVICE_ACCOUNT_JSON"),
        (dict(fcm_project_id="my-project-1"), "FCM_PROJECT_ID"),
    ],
)
@pytest.mark.parametrize("env", ["dev", "production"])
def test_half_set_or_invalid_provider_config_fails_startup(env, kw, needle):
    s = settings(env, **kw)
    assert any(needle in p for p in s.problems())


def test_problems_never_contain_values():
    s = settings(
        "production",
        resend_api_key=API_KEY,
        resend_webhook_secret="whsec_!!notbase64" + API_KEY,
        email_from="x@y.co",
        fcm_service_account_json='{"type":"service_account","private_key":"CANARY-PEM"}',
    )
    text = " ".join(s.problems())
    assert API_KEY not in text and "CANARY-PEM" not in text and "notbase64" not in text


def test_load_settings_raises_config_error_without_values(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text(f"RESEND_API_KEY={API_KEY}\n")
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ConfigError) as e:
        load_settings(str(env))
    assert API_KEY not in str(e.value) and "half configured" in str(e.value)


def test_settings_repr_hides_secrets():
    s = settings(**EMAIL, fcm_service_account_json=service_account_json())
    text = repr(s) + str(s.model_dump())
    assert API_KEY not in text and WEBHOOK_SECRET not in text and "PRIVATE KEY" not in text


def test_a_valid_deployed_config_has_no_problems():
    s = settings("production", fcm_service_account_json=service_account_json(), **EMAIL)
    assert s.problems() == []


# ------------------------------------------------------------------ device tokens (login/logout)
@pytest.fixture
def api(repo, verifier):
    from hs_core.notifications.devices import DeviceRegistry

    app = create_app(
        settings=Settings(_env_file=None, hs_env="dev"), repository=repo, verifier=verifier
    )
    return TestClient(app), repo, DeviceRegistry(repo)


def auth(tok):
    return {"Authorization": f"Bearer {tok}"}


def test_token_registered_on_login_and_removed_on_logout(api):
    client, repo, devices = api
    body = {"app": "parent", "platform": "android", "token": CANARY_TOKEN}
    assert client.put("/v1/me/devices", json=body, headers=auth("parent-token")).status_code == 204
    assert [d.token for d in devices.active_tokens("u_parent")] == [CANARY_TOKEN]
    r = client.request(
        "DELETE", "/v1/me/devices", json={"token": CANARY_TOKEN}, headers=auth("parent-token")
    )
    assert r.status_code == 204 and devices.active_tokens("u_parent") == []
    assert (
        client.request(
            "DELETE", "/v1/me/devices", json={"token": CANARY_TOKEN}, headers=auth("parent-token")
        ).status_code
        == 204
    )  # idempotent


def test_register_requires_a_session_and_the_matching_role(api):
    client, _, devices = api
    body = {"app": "parent", "platform": "ios", "token": "t1"}
    assert client.put("/v1/me/devices", json=body).status_code == 401
    assert client.put("/v1/me/devices", json=body, headers=auth("teacher-token")).status_code == 403
    assert (
        client.put("/v1/me/devices", json=body, headers=auth("unlabelled-token")).status_code == 403
    )
    admin = {"app": "admin", "platform": "web", "token": "t2"}
    assert client.put("/v1/me/devices", json=admin, headers=auth("parent-token")).status_code == 403
    assert client.put("/v1/me/devices", json=admin, headers=auth("admin-token")).status_code == 204


@pytest.mark.parametrize(
    "body",
    [
        {"app": "parent", "platform": "android"},
        {"app": "parent", "platform": "android", "token": ""},
        {"app": "parent", "platform": "android", "token": "x" * 1025},
        {"app": "nope", "platform": "android", "token": "t"},
        {"app": "parent", "platform": "palm", "token": "t"},
        {"app": "parent", "platform": "android", "token": 5},
        {"app": "parent", "platform": "android", "token": "t", "user_id": "u_other"},
    ],
)
def test_register_validates_the_body(api, body):
    client, repo, _ = api
    assert client.put("/v1/me/devices", json=body, headers=auth("parent-token")).status_code in (
        422,
        400,
    )
    assert repo.list("device_token") == []


def test_logout_cannot_remove_someone_elses_token(api):
    client, _, devices = api
    client.put(
        "/v1/me/devices",
        json={"app": "parent", "platform": "android", "token": "shared"},
        headers=auth("parent-token"),
    )
    client.request(
        "DELETE", "/v1/me/devices", json={"token": "shared"}, headers=auth("teacher-token")
    )
    assert len(devices.active_tokens("u_parent")) == 1


def test_a_token_that_changes_user_on_a_shared_phone_moves_to_the_new_user(api):
    client, _, devices = api
    client.put(
        "/v1/me/devices",
        json={"app": "parent", "platform": "android", "token": "shared"},
        headers=auth("parent-token"),
    )
    client.put(
        "/v1/me/devices",
        json={"app": "teacher", "platform": "android", "token": "shared"},
        headers=auth("teacher-token"),
    )
    assert devices.active_tokens("u_parent") == []
    assert [d.token for d in devices.active_tokens("u_teacher")] == ["shared"]


def test_token_not_in_logs(api, caplog):
    client, _, _ = api
    with caplog.at_level(logging.DEBUG):
        client.put(
            "/v1/me/devices",
            json={"app": "parent", "platform": "android", "token": CANARY_TOKEN},
            headers=auth("parent-token"),
        )
    assert CANARY_TOKEN not in caplog.text


# ------------------------------------------------------------------ schedule, permissions
def test_cleanup_job_is_scheduled_daily():
    job = next(j for j in JOBS if j.name == "webhook-event-cleanup")
    assert job.schedule.every.total_seconds() == 86400


def test_cleanup_job_refuses_to_run_unbound():
    from hs_core.jobs.model import JobContext
    from hs_core.notifications import dispatch

    dispatch._bound_repo.clear()
    with pytest.raises(RuntimeError):
        dispatch.webhook_event_cleanup(JobContext)  # type: ignore[arg-type]


@pytest.mark.parametrize("table", ["email_suppression", "webhook_event"])
def test_new_tables_have_an_m05_decision_and_are_server_only(table):
    spec = SPECS[table]
    assert spec.service_only


def test_new_tables_are_not_audited_tables():
    from hs_core.audit.registry import AUDITED_TABLES

    assert not {"email_suppression", "webhook_event"} & AUDITED_TABLES


def test_fixture_json_files_are_valid():
    from n02_helpers import FIXTURES

    for f in FIXTURES.glob("*.json"):
        json.loads(f.read_text())
    assert PARENT
