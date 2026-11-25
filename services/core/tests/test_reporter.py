"""ErrorReporter interface, Sentry adapter (fake transport only), wiring and log integration."""

import json
import logging

import pytest
from fastapi.testclient import TestClient
from sentry_sdk.transport import Transport

from hs_core.auth import FakeTokenVerifier
from hs_core.logging_setup import JsonFormatter
from hs_core.main import create_app
from hs_core.observability import (
    FakeReporter,
    NoopReporter,
    SentryReporter,
    build_reporter,
)
from hs_core.repository import InMemoryRepository
from hs_core.settings import Settings

NAME = "".join(
    ["Am", "ara Na", "kato"]
)  # built at runtime so source context lines never contain it
DSN = "https://publickey@o0.ingest.example.invalid/1"


class CaptureTransport(Transport):
    """Stands in for the network: records envelopes, sends nothing."""

    def __init__(self):
        super().__init__({"dsn": None})
        self.items = []

    def capture_envelope(self, envelope):
        self.items.extend(envelope.items)


def make_sentry(transport=None, **kw):
    t = transport or CaptureTransport()
    return SentryReporter(DSN, environment="staging", release="abc1234", transport=t, **kw), t


def events(t):
    return [i.payload.json for i in t.items if i.headers.get("type") == "event"]


def boom_app(reporter):
    app = create_app(
        Settings(_env_file=None),
        repository=InMemoryRepository(),
        verifier=FakeTokenVerifier(),
        reporter=reporter,
    )

    @app.get("/boom")
    def boom():
        raise RuntimeError("payment failed for 0772123456 mary@example.com")

    return TestClient(app, raise_server_exceptions=False)


def test_no_dsn_means_noop_reporter():
    r = build_reporter(Settings(_env_file=None))
    assert isinstance(r, NoopReporter) and r.enabled is False
    r.capture_exception(ValueError("x"))  # does nothing, does not raise


def test_dsn_builds_sentry_reporter_only_with_transport_in_tests():
    s = Settings(_env_file=None, sentry_dsn=DSN)
    with pytest.raises(RuntimeError, match="test"):  # real network transport refused under pytest
        build_reporter(s)
    assert build_reporter(s, transport=CaptureTransport()).enabled is True


def test_dsn_is_secret_and_validated():
    s = Settings(_env_file=None, sentry_dsn=DSN)
    assert "publickey" not in repr(s)
    bad = Settings(_env_file=None, sentry_dsn="not-a-dsn")
    assert any("SENTRY_DSN" in p for p in bad.problems())
    assert "not-a-dsn" not in " ".join(bad.problems())


def test_sentry_event_has_tags_and_is_scrubbed():
    rep, t = make_sentry()
    try:
        raise ValueError("notify failed for +256772123456 and mary@example.com")
    except ValueError as e:
        rep.capture_exception(e, request_id="req-9", tags={"job": "tick"})
    rep.flush()
    [ev] = events(t)
    assert ev["environment"] == "staging" and ev["release"] == "abc1234"
    assert ev["tags"]["request_id"] == "req-9" and ev["tags"]["job"] == "tick"
    blob = json.dumps(ev)
    assert "772123456" not in blob and "mary@example.com" not in blob
    assert "notify failed for" in blob


def test_sentry_event_has_no_request_body_or_locals():
    rep, t = make_sentry()

    def inner(child_name):
        raise ValueError("x")

    try:
        inner(NAME)
    except ValueError as e:
        rep.capture_exception(e)
    [ev] = events(t)
    assert NAME not in json.dumps(ev)


def test_capture_message_is_scrubbed():
    rep, t = make_sentry()
    rep.capture_message("payout failed for 0701234567", level="error", request_id="r1")
    [ev] = events(t)
    assert "0701234567" not in json.dumps(ev)


def test_reporter_failure_never_breaks_the_caller():
    class Exploding(CaptureTransport):
        def capture_envelope(self, envelope):
            raise OSError("network down")

    rep, _ = make_sentry(Exploding())
    rep.capture_exception(ValueError("x"))  # swallowed


def test_unhandled_exception_is_reported_once_with_request_id_and_no_pii():
    fake = FakeReporter()
    c = boom_app(fake)
    r = c.get("/boom", headers={"X-Request-ID": "trace-77"})
    assert r.status_code == 500 and r.json()["error"]["request_id"] == "trace-77"
    assert len(fake.events) == 1
    ev = fake.events[0]
    assert ev["request_id"] == "trace-77" and ev["type"] == "RuntimeError"
    assert "0772123456" not in ev["message"] and "mary@example.com" not in ev["message"]


def test_client_errors_are_not_reported():
    fake = FakeReporter()
    c = boom_app(fake)
    assert c.get("/nope").status_code == 404
    assert c.get("/v1/me").status_code == 401
    assert fake.events == []


def test_error_logs_carry_request_id_and_scrubbed_exception_text(capfd):
    from hs_core.logging_setup import configure_logging

    configure_logging("INFO")
    boom_app(FakeReporter()).get("/boom", headers={"X-Request-ID": "trace-5"})
    out = capfd.readouterr()
    recs = [json.loads(x) for x in (out.err + out.out).splitlines() if x.startswith("{")]
    errs = [r for r in recs if r["level"] == "error" and r["logger"].startswith("hs_core")]
    assert errs and all(r["request_id"] == "trace-5" for r in errs)
    assert "0772123456" not in json.dumps(errs) and "mary@example.com" not in json.dumps(errs)


def test_formatter_masks_phone_in_message():
    rec = logging.LogRecord("x", logging.WARNING, "f", 1, "sms to %s", ("0772123456",), None)
    assert "0772123456" not in JsonFormatter().format(rec)


def test_fake_reporter_applies_same_scrubber():
    f = FakeReporter()
    f.capture_exception(ValueError("call 0772123456"), request_id="r")
    assert "0772123456" not in json.dumps(f.events)


def test_dev_app_defaults_to_noop(client):
    assert isinstance(client.app.state.reporter, NoopReporter)
