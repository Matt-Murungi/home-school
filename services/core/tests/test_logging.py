import json
import logging
import re

from hs_core.logging_setup import JsonFormatter, request_id_var


def lines(caplog_text):
    return [json.loads(x) for x in caplog_text.splitlines() if x.startswith("{")]


def test_request_id_generated_and_returned(client):
    r = client.get("/health")
    assert re.fullmatch(r"[0-9a-f]{32}", r.headers["X-Request-ID"])


def test_incoming_request_id_echoed(client):
    r = client.get("/health", headers={"X-Request-ID": "req-abc_123"})
    assert r.headers["X-Request-ID"] == "req-abc_123"


def test_unsafe_request_id_replaced(client):
    r = client.get("/health", headers={"X-Request-ID": "bad id\twith spaces" + "x" * 100})
    assert re.fullmatch(r"[0-9a-f]{32}", r.headers["X-Request-ID"])


def test_json_log_lines_carry_request_id(client, capfd):
    from hs_core.logging_setup import configure_logging

    configure_logging("INFO")
    client.get("/health", headers={"X-Request-ID": "trace-1"})
    out = capfd.readouterr()
    recs = [json.loads(x) for x in (out.err + out.out).splitlines() if x.startswith("{")]
    recs = [r for r in recs if r["logger"].startswith("hs_core")]  # not the test client's own
    assert recs, "no JSON log lines"
    assert all(r["request_id"] == "trace-1" for r in recs)
    assert any(r["message"] == "request" and r["status"] == 200 for r in recs)


def test_request_id_propagated_into_handler_logs(client, capfd):
    from hs_core.logging_setup import configure_logging

    configure_logging("INFO")
    client.get(
        "/v1/me", headers={"X-Request-ID": "trace-2", "Authorization": "Bearer parent-token"}
    )
    out = capfd.readouterr()
    recs = [json.loads(x) for x in (out.err + out.out).splitlines() if x.startswith("{")]
    recs = [r for r in recs if r["logger"].startswith("hs_core")]
    assert {r["request_id"] for r in recs} == {"trace-2"}
    assert any(r["message"] == "me requested" for r in recs)


def test_formatter_outside_request_uses_dash():
    rec = logging.LogRecord("x", logging.INFO, "f", 1, "hello %s", ("w",), None)
    d = json.loads(JsonFormatter().format(rec))
    assert d["request_id"] == "-" and d["message"] == "hello w" and d["level"] == "info"
    assert request_id_var.get() == "-"


def test_formatter_includes_exception():
    try:
        raise ValueError("x")
    except ValueError:
        import sys

        rec = logging.LogRecord("x", logging.ERROR, "f", 1, "m", (), sys.exc_info())
    assert "ValueError" in json.loads(JsonFormatter().format(rec))["exc"]
