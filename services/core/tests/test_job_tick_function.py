"""The Appwrite Function job-tick: stdlib only, every minute, accepted by the endpoint."""

import ast
import importlib.util
import json
import pathlib
import sys

import pytest
from fastapi.testclient import TestClient

from hs_core.jobs.alerts import FakeAlertSink
from hs_core.jobs.model import Done, JobSpec, Schedule
from hs_core.jobs.runner import Runner
from hs_core.main import create_app
from hs_core.settings import Settings
from jobs_helpers import FakeClock, HookRepository

ROOT = pathlib.Path(__file__).resolve().parents[3]
FN_DIR = ROOT / "appwrite" / "functions" / "job-tick"
SECRET = "f" * 40


def load_fn():
    spec = importlib.util.spec_from_file_location("job_tick_main", FN_DIR / "src" / "main.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_function_config_entry():
    cfg = json.loads((ROOT / "appwrite" / "appwrite.config.json").read_text())
    (fn,) = [f for f in cfg["functions"] if f["$id"] == "job-tick"]
    assert fn["runtime"] == "python-3.12"
    assert fn["schedule"] == "* * * * *"
    assert fn["entrypoint"] == "src/main.py"
    assert fn["timeout"] > 25  # above the 25 s HTTP timeout used by the function
    assert fn["execute"] == [] and fn["events"] == []  # no client can call it; cron only
    assert (ROOT / "appwrite" / fn["path"] / fn["entrypoint"]).is_file()
    assert fn["scopes"] == []  # the function needs no Appwrite API access


def test_function_uses_only_the_standard_library():
    tree = ast.parse((FN_DIR / "src" / "main.py").read_text())
    mods = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            mods |= {a.name.split(".")[0] for a in n.names}
        elif isinstance(n, ast.ImportFrom):
            mods.add((n.module or "").split(".")[0])
    assert mods and mods <= set(sys.stdlib_module_names)
    assert not (FN_DIR / "requirements.txt").exists()


def client():
    repo, clock, calls = HookRepository(), FakeClock(), []
    spec = JobSpec(
        "heartbeat",
        lambda ctx: calls.append(1) or Done(),
        schedule=Schedule(__import__("datetime").timedelta(minutes=5), anchor=clock()),
        catch_up=__import__("datetime").timedelta(0),
    )
    runner = Runner(repo, [spec], alert=FakeAlertSink(), clock=clock)
    app = create_app(
        Settings(_env_file=None, job_tick_secret=SECRET), repository=repo, runner=runner
    )
    return TestClient(app), calls


class Ctx:
    def __init__(self):
        self.logs = []

    def log(self, m):
        self.logs.append(m)

    def error(self, m):
        self.logs.append("ERR " + m)

    class res:
        @staticmethod
        def json(data, status=200):
            return status, data


class FakeResp:
    def __init__(self, r):
        self.r = r

    def read(self):
        return self.r.content

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_function_signature_is_accepted_by_the_endpoint(monkeypatch):
    fn = load_fn()
    c, calls = client()
    seen = {}

    def fake_urlopen(req, timeout):
        seen["timeout"] = timeout
        seen["url"] = req.full_url
        r = c.post("/internal/jobs/tick", content=req.data, headers=dict(req.header_items()))
        assert r.status_code == 200, r.text
        return FakeResp(r)

    monkeypatch.setenv("CORE_SERVICE_URL", "https://core.example/")
    monkeypatch.setenv("JOB_TICK_SECRET", SECRET)
    monkeypatch.setattr(fn.urllib.request, "urlopen", fake_urlopen)
    ctx = Ctx()
    status, data = fn.main(ctx)
    assert status == 200 and data["ok"] and len(data["result"]["completed"]) == 1
    assert seen["url"] == "https://core.example/internal/jobs/tick" and seen["timeout"] == 25
    assert calls == [1]


def test_function_with_the_wrong_secret_is_rejected_and_reports_502(monkeypatch):
    import urllib.error

    fn = load_fn()
    c, calls = client()

    def fake_urlopen(req, timeout):
        r = c.post("/internal/jobs/tick", content=req.data, headers=dict(req.header_items()))
        raise urllib.error.HTTPError(req.full_url, r.status_code, "x", {}, None)

    monkeypatch.setenv("CORE_SERVICE_URL", "https://core.example")
    monkeypatch.setenv("JOB_TICK_SECRET", "w" * 40)
    monkeypatch.setattr(fn.urllib.request, "urlopen", fake_urlopen)
    ctx = Ctx()
    status, data = fn.main(ctx)
    assert status == 502 and not data["ok"] and calls == []
    assert not any("w" * 40 in line for line in ctx.logs)  # the secret is never logged


def test_function_survives_an_unreachable_core_service(monkeypatch):
    fn = load_fn()

    def boom(req, timeout):
        raise OSError("connection refused")

    monkeypatch.setenv("CORE_SERVICE_URL", "https://core.example")
    monkeypatch.setenv("JOB_TICK_SECRET", SECRET)
    monkeypatch.setattr(fn.urllib.request, "urlopen", boom)
    status, data = fn.main(Ctx())
    assert status == 502 and not data["ok"]


def test_function_fails_clearly_when_not_configured(monkeypatch):
    fn = load_fn()
    monkeypatch.delenv("CORE_SERVICE_URL", raising=False)
    monkeypatch.delenv("JOB_TICK_SECRET", raising=False)
    ctx = Ctx()
    status, data = fn.main(ctx)
    assert status == 500 and not data["ok"]
    assert any("CORE_SERVICE_URL" in line for line in ctx.logs)


@pytest.mark.parametrize("url", ["http://core.example", "ftp://x"])
def test_function_refuses_non_https_core_url_outside_localhost(monkeypatch, url):
    fn = load_fn()
    monkeypatch.setenv("CORE_SERVICE_URL", url)
    monkeypatch.setenv("JOB_TICK_SECRET", SECRET)
    status, data = fn.main(Ctx())
    assert status == 500 and not data["ok"]
