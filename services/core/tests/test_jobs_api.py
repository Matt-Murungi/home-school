"""POST /internal/jobs/tick: signed requests only, never user JWTs."""

import threading
import time
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from hs_core.auth import FakeTokenVerifier
from hs_core.jobs.alerts import FakeAlertSink
from hs_core.jobs.model import JobSpec, Schedule
from hs_core.jobs.runner import Runner
from hs_core.jobs.signing import MAX_SKEW_SECONDS, sign
from hs_core.main import create_app
from hs_core.settings import Settings
from jobs_helpers import T0, Effects, FakeClock, HookRepository

SECRET = "s" * 40
URL = "/internal/jobs/tick"


def build(secret=SECRET, handler=None):
    repo = HookRepository()
    clock = FakeClock()
    fx = Effects()
    spec = JobSpec(
        "heartbeat",
        handler or fx.handler,
        schedule=Schedule(timedelta(minutes=5), anchor=T0),
        catch_up=timedelta(0),
    )
    runner = Runner(repo, [spec], alert=FakeAlertSink(), clock=clock)
    verifier = FakeTokenVerifier()
    verifier.add("admin-token", user_id="u_admin", labels=["opsadmin", "superadmin"])
    settings = Settings(_env_file=None, hs_env="dev", job_tick_secret=secret)
    app = create_app(settings=settings, repository=repo, verifier=verifier, runner=runner)
    return TestClient(app), repo, fx


def post(c, body=b"{}", ts=None, secret=SECRET, sig=None, headers=None):
    ts = ts if ts is not None else str(int(time.time()))
    sig = sig if sig is not None else sign(secret, ts, body)
    h = {"X-Job-Timestamp": ts, "X-Job-Signature": sig, **(headers or {})}
    return c.post(URL, content=body, headers=h)


def test_signed_tick_runs_due_jobs_and_reports_the_result():
    c, repo, fx = build()
    r = post(c)
    assert r.status_code == 200
    assert len(r.json()["completed"]) == 1 and fx.invocations
    assert post(c).json()["completed"] == []  # second tick: nothing new due


@pytest.mark.parametrize(
    "case",
    [
        "no_headers",
        "wrong_secret",
        "tampered_body",
        "stale",
        "future",
        "non_ascii_sig",
        "non_numeric_ts",
        "empty_sig",
        "huge_ts",
    ],
)
def test_rejects_bad_credentials_and_runs_nothing(case):
    c, repo, fx = build()
    now = int(time.time())
    body = b"{}"
    if case == "no_headers":
        r = c.post(URL, content=body)
    elif case == "wrong_secret":
        r = post(c, secret="w" * 40)
    elif case == "tampered_body":
        r = post(c, body=b'{"x":1}', ts=str(now), sig=sign(SECRET, str(now), body))
    elif case == "stale":
        r = post(c, ts=str(now - MAX_SKEW_SECONDS - 5))
    elif case == "future":
        r = post(c, ts=str(now + MAX_SKEW_SECONDS + 5))
    elif case == "non_ascii_sig":
        r = post(c, sig=("é" * 64).encode())  # non-ASCII must be a 401, not a 500
    elif case == "non_numeric_ts":
        r = post(c, ts="abc", sig="0" * 64)
    elif case == "empty_sig":
        r = post(c, sig=" ")
    else:
        r = post(c, ts="9" * 400, sig="0" * 64)
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "unauthenticated"
    assert repo.list("job_run") == [] and fx.invocations == []


def test_user_jwt_is_never_accepted_even_for_an_admin():
    c, repo, fx = build()
    r = c.post(URL, content=b"{}", headers={"Authorization": "Bearer admin-token"})
    assert r.status_code == 401 and fx.invocations == []


def test_a_valid_signature_does_not_need_or_use_a_jwt():
    c, _, fx = build()
    r = post(c, headers={"Authorization": "Bearer garbage"})
    assert r.status_code == 200


def test_unconfigured_secret_fails_closed():
    c, repo, fx = build(secret="")
    assert post(c, secret="").status_code == 503
    assert repo.list("job_run") == []


def test_oversized_body_rejected_before_anything_runs():
    c, repo, fx = build()
    r = post(c, body=b"x" * 70_000)
    assert r.status_code == 413 and fx.invocations == []


def test_error_does_not_reveal_why_the_credential_failed():
    c, *_ = build()
    a = post(c, secret="w" * 40).json()["error"]["message"]
    b = post(c, ts="1").json()["error"]["message"]
    assert a == b


def test_signature_compare_is_constant_time():
    import inspect

    from hs_core.jobs import signing

    assert "compare_digest" in inspect.getsource(signing)


def test_overlapping_ticks_of_the_same_period_run_the_job_once():
    gate = threading.Event()
    c, repo, fx = build(handler=None)

    def slow(ctx):
        gate.wait(2)
        return fx.handler(ctx)

    c, repo, fx = build(handler=slow)
    out = []
    ts = [threading.Thread(target=lambda: out.append(post(c).status_code)) for _ in range(4)]
    for t in ts:
        t.start()
    time.sleep(0.2)
    gate.set()
    for t in ts:
        t.join()
    assert out == [200] * 4 and len(fx.invocations) == 1


def test_tick_route_is_not_in_the_public_schema():
    c, *_ = build()
    assert URL not in c.app.openapi()["paths"]
