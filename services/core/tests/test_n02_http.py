"""N02 (#127): shared provider HTTP hygiene: Retry-After clamping and NO redirect following.

The redirect tests use two real servers on 127.0.0.1 (loopback only, no external network): the
first answers 302 to the second, which records whatever credential header reaches it."""

import http.server
import threading
from datetime import UTC, datetime

import httpx
import pytest

from hs_core.notifications.httpsafe import MAX_RETRY_AFTER_S, build_client, parse_retry_after

NOW = datetime(2027, 1, 18, 9, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("0", 0.0),
        ("5", 5.0),
        ("5.5", 5.5),
        (" 30 ", 30.0),
        ("-1", 0.0),
        ("-999999", 0.0),
        ("99999999999", MAX_RETRY_AFTER_S),
        ("1e9", MAX_RETRY_AFTER_S),
        ("nan", None),
        ("NaN", None),
        ("inf", None),
        ("-inf", None),
        ("Infinity", None),
        ("", None),
        ("garbage", None),
        ("12abc", None),
        ("0x10", None),
        ("١٢", None),  # arabic-indic digits are not delta-seconds
        ("5\n6", None),
        (None, None),
    ],
)
def test_retry_after_delta_seconds_matrix(raw, expected):
    assert parse_retry_after(raw, NOW) == expected


def test_retry_after_http_date_is_relative_to_the_injected_clock():
    assert parse_retry_after("Mon, 18 Jan 2027 09:00:30 GMT", NOW) == 30.0
    assert parse_retry_after("Mon, 18 Jan 2027 08:00:00 GMT", NOW) == 0.0  # in the past
    assert parse_retry_after("Mon, 18 Jan 2030 09:00:00 GMT", NOW) == MAX_RETRY_AFTER_S
    assert parse_retry_after("Mon, 99 Foo 2027 09:00:00 GMT", NOW) is None


def test_custom_cap():
    assert parse_retry_after("500", NOW, cap=60) == 60.0


class _Recorder(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):  # silence
        pass

    def _go(self):
        self.server.seen.append((self.path, dict(self.headers)))  # type: ignore[attr-defined]
        target = self.server.redirect_to  # type: ignore[attr-defined]
        if target:
            self.send_response(self.server.redirect_status)  # type: ignore[attr-defined]
            self.send_header("Location", target)
            self.send_header("Content-Length", "0")
            self.end_headers()
        else:
            body = b'{"ok": true}'
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    do_GET = do_POST = _go


def _serve(redirect_to=None, status=302):
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Recorder)
    srv.seen, srv.redirect_to, srv.redirect_status = [], redirect_to, status  # type: ignore
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


@pytest.fixture
def two_servers():
    target = _serve()
    origin = _serve(f"http://127.0.0.1:{target.server_port}/stolen")
    yield origin, target
    for s in (origin, target):
        s.shutdown()
        s.server_close()


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
def test_built_client_never_follows_redirects_or_leaks_credentials(two_servers, status):
    origin, target = two_servers
    origin.redirect_status = status
    client = build_client()
    resp = client.post(
        f"http://127.0.0.1:{origin.server_port}/send",
        headers={"Authorization": "Bearer SECRET-CANARY"},
        content=b"{}",
    )
    assert resp.status_code == status
    assert len(origin.seen) == 1
    assert target.seen == [], "the redirect target must never be contacted"
    assert not any("SECRET-CANARY" in str(h) for _, h in target.seen)


def test_client_defaults():
    c = build_client()
    assert c.follow_redirects is False
    assert c.timeout.connect and c.timeout.read and c.timeout.read <= 15


def test_mutation_following_redirects_would_leak(two_servers):
    """The control: a client that follows redirects DOES reach the second server."""
    origin, target = two_servers
    c = httpx.Client(follow_redirects=True)
    c.post(
        f"http://127.0.0.1:{origin.server_port}/send",
        headers={"Authorization": "Bearer SECRET-CANARY"},
        content=b"{}",
    )
    assert len(target.seen) == 1  # proves the harness would catch a regression
