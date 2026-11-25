import importlib.util
import json
import pathlib
import time
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from jobs_demo.api import sign
from jobs_demo.api import create_app
from jobs_demo.runner import Done, JobSpec, Runner, Schedule
from jobs_demo.store import MemoryStore

SECRET = "test-secret"


def client():
    store = MemoryStore()
    now = datetime.now(UTC)
    spec = JobSpec("heartbeat", lambda ctx: Done(), schedule=Schedule(timedelta(minutes=5), anchor=now - timedelta(minutes=1)),
                   catch_up=timedelta(minutes=2))
    return TestClient(create_app(Runner(store, [spec]), secret=SECRET)), store


def post(c, body=b"{}", ts=None, secret=SECRET, sig=None):
    ts = ts or str(int(time.time()))
    sig = sig or sign(secret, ts, body)
    return c.post("/internal/jobs/tick", content=body, headers={"X-Job-Timestamp": ts, "X-Job-Signature": sig})


def test_signed_tick_runs_due_jobs():
    c, store = client()
    r = post(c)
    assert r.status_code == 200 and len(r.json()["completed"]) == 1
    assert post(c).json()["completed"] == []          # second tick: nothing new due


def test_rejects_unsigned_wrong_secret_and_stale_requests():
    c, store = client()
    assert c.post("/internal/jobs/tick", content=b"{}").status_code == 401
    assert post(c, secret="wrong").status_code == 401
    assert post(c, ts=str(int(time.time()) - 600)).status_code == 401
    assert post(c, sig=("é" * 64).encode()).status_code == 401  # non-ASCII must be a 401, not a 500
    body, ts = b"{}", str(int(time.time()))
    assert post(c, body=b'{"tampered":1}', ts=ts, sig=sign(SECRET, ts, body)).status_code == 401
    assert store.list_rows("job_run") == []           # nothing ran


def test_appwrite_function_signs_requests_the_api_accepts(monkeypatch):
    path = pathlib.Path(__file__).parents[1] / "appwrite-function" / "src" / "main.py"
    spec = importlib.util.spec_from_file_location("fn_main", path)
    fn = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fn)
    c, _ = client()

    class FakeResp:
        def __init__(self, r): self.r = r
        def read(self): return self.r.content
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def fake_urlopen(req, timeout):
        r = c.post("/internal/jobs/tick", content=req.data, headers=dict(req.header_items()))
        assert r.status_code == 200, r.text
        return FakeResp(r)

    class Ctx:
        logs = []
        def log(self, m): self.logs.append(m)
        def error(self, m): self.logs.append("ERR " + m)
        class res:
            @staticmethod
            def json(data, status=200): return (status, data)

    monkeypatch.setenv("CORE_SERVICE_URL", "http://core.internal")
    monkeypatch.setenv("JOB_TICK_SECRET", SECRET)
    monkeypatch.setattr(fn.urllib.request, "urlopen", fake_urlopen)
    status, data = fn.main(Ctx())
    assert status == 200 and data["ok"] and len(data["result"]["completed"]) == 1
    json.dumps(data)
