def test_health_returns_version_and_sha(client):
    for path in ("/health", "/healthz"):
        r = client.get(path)
        assert r.status_code == 200
        assert r.json() == {"status": "ok", "version": "1.2.3", "git_sha": "abc1234"}


def test_ready_ok_when_repository_up(client):
    for path in ("/ready", "/readyz"):
        r = client.get(path)
        assert r.status_code == 200
        assert r.json()["status"] == "ready"


def test_ready_503_when_repository_down_and_recovers(client, repo):
    repo.available = False
    for path in ("/ready", "/readyz"):
        r = client.get(path)
        assert r.status_code == 503
        assert r.json()["error"]["code"] == "not_ready"
    # liveness is unaffected: process keeps running
    assert client.get("/health").status_code == 200
    repo.available = True  # retried on next probe
    assert client.get("/ready").status_code == 200


def test_ready_503_when_ping_raises(client, repo):
    def boom():
        raise RuntimeError("secret-host:5432 refused")

    repo.ping = boom
    r = client.get("/ready")
    assert r.status_code == 503
    assert "secret-host" not in r.text


def test_app_starts_with_unreachable_repository(settings, verifier, repo):
    from fastapi.testclient import TestClient

    from hs_core.main import create_app

    repo.available = False
    with TestClient(create_app(settings=settings, repository=repo, verifier=verifier)) as c:
        assert c.get("/ready").status_code == 503
        assert c.get("/health").status_code == 200
