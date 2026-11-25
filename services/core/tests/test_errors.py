def test_404_uses_error_format(client):
    r = client.get("/nope", headers={"X-Request-ID": "r1"})
    assert r.status_code == 404
    assert r.json() == {"error": {"code": "not_found", "message": "Not found", "request_id": "r1"}}


def test_405_uses_error_format(client):
    r = client.post("/health")
    assert r.status_code == 405
    assert r.json()["error"]["code"] == "method_not_allowed"


def test_unhandled_exception_is_500_without_leak(settings, repo, verifier):
    from fastapi.testclient import TestClient

    from hs_core.main import create_app

    app = create_app(settings=settings, repository=repo, verifier=verifier)

    @app.get("/boom")
    def boom():
        raise RuntimeError("db password=hunter2")

    r = TestClient(app, raise_server_exceptions=False).get("/boom", headers={"X-Request-ID": "r9"})
    assert r.status_code == 500
    assert r.json()["error"] == {
        "code": "internal_error",
        "message": "Internal server error",
        "request_id": "r9",
    }
    assert "hunter2" not in r.text
    assert r.headers["X-Request-ID"] == "r9"


def test_validation_error_422_format(client):
    r = client.get("/v1/echo")
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "validation_error"
    assert "request_id" in r.json()["error"]
