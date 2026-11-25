"""F03 review follow-ups folded into F04: Conflict, query support, timeouts, config hardening."""

import json
import re
from unittest.mock import MagicMock

import pytest
from appwrite.query import Query

from hs_core.repository import (
    AppwriteRepository,
    Conflict,
    InMemoryRepository,
    RepositoryUnavailable,
    build_repository,
    install_request_timeout,
)
from hs_core.settings import ConfigError, Settings


# ---- Conflict is typed (the runner's atomic claim depends on it)
def test_duplicate_create_raises_conflict_which_is_still_a_valueerror():
    r = InMemoryRepository()
    r.create("t", "1", {})
    with pytest.raises(Conflict):
        r.create("t", "1", {})
    with pytest.raises(ValueError):
        r.create("t", "1", {})


def test_adapter_maps_409_to_conflict_and_other_errors_to_unavailable():
    from appwrite.exception import AppwriteException

    tables = MagicMock()
    repo = AppwriteRepository(client=MagicMock(), database_id="main", tables=tables)
    tables.create_row.side_effect = AppwriteException("exists", 409, "document_already_exists", "")
    with pytest.raises(Conflict):
        repo.create("t", "1", {})
    tables.create_row.side_effect = AppwriteException("boom", 500, "x", "")
    with pytest.raises(RepositoryUnavailable):
        repo.create("t", "1", {})


def test_inmemory_create_is_atomic_under_threads():
    import threading

    r = InMemoryRepository()
    wins = []

    def go():
        try:
            r.create("t", "same", {})
            wins.append(1)
        except Conflict:
            pass

    ts = [threading.Thread(target=go) for _ in range(16)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert wins == [1]


# ---- list queries in the fake follow Appwrite semantics
def seeded():
    r = InMemoryRepository()
    for i, (status, run_at) in enumerate(
        [
            ("queued", "2027-01-01T00:03"),
            ("queued", "2027-01-01T00:01"),
            ("done", "2027-01-01T00:02"),
            ("queued", "2027-01-01T00:09"),
        ]
    ):
        r.create("t", f"r{i}", {"status": status, "run_at": run_at})
    return r


def test_inmemory_list_supports_equal_compare_order_and_limit():
    r = seeded()
    q = [
        Query.equal("status", ["queued"]),
        Query.less_than_equal("run_at", "2027-01-01T00:05"),
        Query.order_asc("run_at"),
        Query.limit(5),
    ]
    assert [x["$id"] for x in r.list("t", q)] == ["r1", "r0"]
    q = [Query.equal("status", ["queued"]), Query.order_desc("run_at"), Query.limit(1)]
    assert [x["$id"] for x in r.list("t", q)] == ["r3"]
    assert len(r.list("t")) == 4


def test_inmemory_list_rejects_unsupported_queries_loudly():
    with pytest.raises(ValueError):
        seeded().list("t", [json.dumps({"method": "search", "attribute": "a", "values": ["x"]})])


# ---- follow-up 1: outbound Appwrite calls have a timeout
def test_sdk_client_calls_carry_a_request_timeout(monkeypatch):
    import appwrite.client as ac
    import requests

    install_request_timeout((1.5, 4.0))
    seen = {}

    def fake_request(**kw):
        seen.update(kw)
        resp = MagicMock()
        resp.status_code = 200
        resp.headers = {"Content-Type": "application/json"}
        resp.json.return_value = {"status": "pass"}
        return resp

    monkeypatch.setattr(requests, "request", fake_request)
    client = ac.Client().set_endpoint("https://x.example/v1").set_project("p").set_key("k" * 40)
    client.call("get", "/health")
    assert seen["timeout"] == (1.5, 4.0)


def test_a_hung_appwrite_call_becomes_repository_unavailable(monkeypatch):
    import appwrite.client as ac
    import requests

    install_request_timeout((1.0, 1.0))

    def hang(**kw):
        raise requests.exceptions.ReadTimeout("read timed out")

    monkeypatch.setattr(requests, "request", hang)
    client = ac.Client().set_endpoint("https://x.example/v1").set_project("p").set_key("k" * 40)
    with pytest.raises(RepositoryUnavailable):
        AppwriteRepository(client, "main", tables=MagicMock()).ping()


def test_install_request_timeout_is_idempotent():
    import appwrite.client as ac

    install_request_timeout((2.0, 3.0))
    first = ac.requests
    install_request_timeout((2.0, 3.0))
    assert ac.requests is first


# ---- follow-up 2: no silent in-memory fallback outside dev
@pytest.mark.parametrize("env", ["staging", "production"])
def test_build_repository_refuses_in_memory_fallback_outside_dev(env):
    with pytest.raises(ConfigError):
        build_repository(Settings(_env_file=None, hs_env=env))


def test_build_repository_dev_still_falls_back_to_memory():
    assert isinstance(build_repository(Settings(_env_file=None)), InMemoryRepository)


# ---- follow-up 3: request id uses fullmatch
def test_request_id_with_trailing_newline_is_not_safe():
    from hs_core.main import is_safe_request_id

    assert is_safe_request_id("abc-123")
    assert not is_safe_request_id("abc\n")
    assert not is_safe_request_id("")
    assert not is_safe_request_id("a" * 65)


# ---- follow-up 4: localhost variants rejected
BASE = dict(
    hs_env="staging",
    appwrite_project_id="p",
    appwrite_api_key="k1" * 20,
    appwrite_database_id="main",
    job_tick_secret="s1" * 20,
    otp_pepper="o1" * 20,
)


@pytest.mark.parametrize(
    "host",
    [
        "localhost",
        "LOCALHOST",
        "127.0.0.1",
        "127.0.0.2",
        "127.255.0.9",
        "[::1]",
        "0.0.0.0",
        "[::]",
        "foo.localhost",
        "localhost.",
    ],
)  # noqa: S104
def test_localhost_variants_are_rejected_for_the_appwrite_endpoint(host):
    s = Settings(_env_file=None, appwrite_endpoint=f"https://{host}/v1", **BASE)
    assert any("localhost" in p for p in s.problems())


def test_real_endpoint_is_not_flagged():
    s = Settings(_env_file=None, appwrite_endpoint="https://fra.cloud.appwrite.io/v1", **BASE)
    assert s.problems() == []


# ---- job secret config
@pytest.mark.parametrize("env", ["staging", "production"])
def test_job_tick_secret_is_required_and_strong_outside_dev(env):
    good = {**BASE, "hs_env": env, "appwrite_endpoint": "https://fra.cloud.appwrite.io/v1"}
    s = Settings(_env_file=None, **{**good, "job_tick_secret": ""})
    assert any("JOB_TICK_SECRET" in p for p in s.problems())
    s = Settings(_env_file=None, **{**good, "job_tick_secret": "short"})
    assert any("JOB_TICK_SECRET" in p for p in s.problems())
    s = Settings(_env_file=None, **good)
    assert s.problems() == []


def test_job_tick_secret_never_in_repr_or_error():
    s = Settings(_env_file=None, job_tick_secret="z" * 40)
    assert "z" * 40 not in repr(s)
    assert re.search(
        "JOB_TICK_SECRET",
        Settings(
            _env_file=None,
            **{**BASE, "job_tick_secret": "x", "appwrite_endpoint": "https://a.example"},
        ).problems()[0],
    )
