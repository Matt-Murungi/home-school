"""Round 3: redirects must never be followed (credentials!), Retry-After must be sanitised."""

import http.server
import threading

import pytest

from py_momo import HttpCollectionsClient, HttpRequest, UrllibTransport, new_reference_id
from py_momo.errors import OutcomeUnknown, RateLimited
from py_momo.fake import FAKE_API_KEY, FAKE_API_USER, FAKE_SUB_KEY, FakeMomo
from py_momo.status import Status
from py_momo.transport import HttpResponse

CODES = [301, 302, 303, 307, 308]


class _Servers:
    def __init__(self):
        self.seen_b: list[tuple[str, str, dict]] = []
        self.seen_a: list[tuple[str, str]] = []
        outer = self

        class B(http.server.BaseHTTPRequestHandler):  # the "other host": must never be hit
            def _h(self):
                outer.seen_b.append((self.command, self.path, dict(self.headers.items())))
                body = b'{"status":"SUCCESSFUL"}'
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            do_GET = do_POST = _h  # noqa: N815

            def log_message(self, *a):
                pass

        self.b = http.server.ThreadingHTTPServer(("127.0.0.1", 0), B)
        self.b_url = f"http://127.0.0.1:{self.b.server_port}"

        class A(http.server.BaseHTTPRequestHandler):
            def _h(self):
                outer.seen_a.append((self.command, self.path))
                n = int(self.headers.get("Content-Length") or 0)
                if n:
                    self.rfile.read(n)
                code = int(self.path.split("/")[1])
                self.send_response(code)
                self.send_header("Location", outer.b_url + "/stolen")
                self.send_header("Content-Length", "0")
                self.end_headers()

            do_GET = do_POST = _h  # noqa: N815

            def log_message(self, *a):
                pass

        self.a = http.server.ThreadingHTTPServer(("127.0.0.1", 0), A)
        self.a_url = f"http://127.0.0.1:{self.a.server_port}"
        for s in (self.a, self.b):
            threading.Thread(target=s.serve_forever, daemon=True).start()

    def close(self):
        for s in (self.a, self.b):
            s.shutdown()
            s.server_close()


@pytest.fixture
def servers():
    s = _Servers()
    yield s
    s.close()


CREDS = {
    "Authorization": f"Bearer secret-token {FAKE_API_KEY}",
    "Ocp-Apim-Subscription-Key": FAKE_SUB_KEY,
    "X-Reference-Id": "r",
}


@pytest.mark.parametrize("method", ["GET", "POST"])
@pytest.mark.parametrize("code", CODES)
def test_transport_never_follows_redirects_and_never_leaks_credentials(servers, method, code):
    body = b"{}" if method == "POST" else None
    resp = UrllibTransport().send(
        HttpRequest(method, f"{servers.a_url}/{code}/x", dict(CREDS), body, 3)
    )
    assert resp.status == code  # returned to the client as a response
    assert servers.seen_a == [(method, f"/{code}/x")]
    assert servers.seen_b == []  # the other host got NO request, hence no credentials


@pytest.mark.parametrize("code", CODES)
def test_token_endpoint_cannot_be_redirected(servers, code):
    """The token call carries Basic API user:key. A 3xx must yield AuthError, not a hop."""
    from py_momo import Environment, MomoConfig
    from py_momo.errors import AuthError

    fake = FakeMomo()

    cfg = MomoConfig(
        environment=Environment.SANDBOX,
        subscription_key=FAKE_SUB_KEY,
        api_user=FAKE_API_USER,
        api_key=FAKE_API_KEY,
        base_url=f"{servers.a_url}/{code}",
        min_amount_ugx=None,
        max_amount_ugx=None,
    )
    c = HttpCollectionsClient(cfg, UrllibTransport(), **fake._kw())
    with pytest.raises(AuthError):
        c.request_to_pay(
            reference_id=new_reference_id(),
            amount_ugx=1000,
            payer_msisdn="0772123456",
            external_id="e",
        )
    assert servers.seen_b == []
    assert all(m == "POST" and "token" in p for m, p in servers.seen_a)


@pytest.mark.parametrize("code", CODES)
def test_post_answered_with_3xx_is_ambiguous_probed_never_success_never_reposted(code):
    class T:  # redirect responses exactly as the no-redirect transport now returns them
        def __init__(self):
            self.fake = FakeMomo()
            self.posts = 0

        def send(self, req):
            if req.method == "POST" and "/token/" not in req.url:
                self.posts += 1
                return HttpResponse(code, {"Location": "http://other.invalid/x"}, b"")
            if req.method == "GET" and "/token/" not in req.url:
                return HttpResponse(503, {}, b"")
            return self.fake.send(req)

    t = T()
    c = HttpCollectionsClient(t.fake.config(), t, **t.fake._kw())
    with pytest.raises(OutcomeUnknown):
        c.request_to_pay(
            reference_id=new_reference_id(),
            amount_ugx=1000,
            payer_msisdn="0772123456",
            external_id="e",
        )
    assert t.posts == 1


# ---- Retry-After sanitising ---------------------------------------------------------------
BAD = ["-5", "nan", "-inf", "inf", "1e999", "-1e999", "999999999", "garbage", "", " ", "0x10",
       "Wed, 21 Oct 2026 07:28:00 GMT", "1,5"]  # fmt: skip


class _StrictSleepFake(FakeMomo):
    """Sleep rejects what time.sleep would choke on or what would stall a worker."""

    def sleep(self, s):
        assert isinstance(s, float | int) and s == s and s not in (float("inf"), float("-inf"))
        assert s >= 0, f"negative sleep {s}"
        assert s <= 30.0, f"sleep above backoff cap: {s}"
        super().sleep(s)


def _fake_with_429(value, n=1, on="post"):
    fake = _StrictSleepFake()

    class T:
        def __init__(self):
            self.left = n

        def send(self, req):
            hit = (req.method == "POST") if on == "post" else (req.method == "GET")
            if hit and "/token/" not in req.url and self.left > 0:
                self.left -= 1
                return HttpResponse(429, {"Retry-After": value}, b"")
            return fake.send(req)

    return fake, HttpCollectionsClient(fake.config(), T(), **fake._kw())


@pytest.mark.parametrize("value", BAD + ["2", "0", "2.5", "30"])
def test_post_429_retry_after_never_reaches_sleep_unsanitised(value):
    fake, c = _fake_with_429(value)
    ref = new_reference_id()
    res = c.request_to_pay(
        reference_id=ref, amount_ugx=1000, payer_msisdn="0772123456", external_id=ref
    )
    assert res.status is Status.PENDING
    assert fake.sleeps and all(0 <= s <= 30.0 for s in fake.sleeps)


@pytest.mark.parametrize("value", BAD)
def test_persistent_post_429_raises_rate_limited_not_value_error(value):
    fake, c = _fake_with_429(value, n=99)
    ref = new_reference_id()
    with pytest.raises(RateLimited) as e:
        c.request_to_pay(
            reference_id=ref, amount_ugx=1000, payer_msisdn="0772123456", external_id=ref
        )
    ra = e.value.retry_after
    assert ra is None or 0 <= ra <= 30.0
    assert all(0 <= s <= 30.0 for s in fake.sleeps)


@pytest.mark.parametrize("value", BAD)
def test_get_status_429_retry_after_sanitised(value):
    fake, c = _fake_with_429(value, n=1, on="get")
    ref = new_reference_id()
    c.request_to_pay(reference_id=ref, amount_ugx=1000, payer_msisdn="0772123456", external_id=ref)
    assert c.get_status(ref).status is Status.SUCCESSFUL
    assert fake.sleeps and all(0 <= s <= 30.0 for s in fake.sleeps)


@pytest.mark.parametrize("value", BAD)
def test_get_status_persistent_429_raises_rate_limited(value):
    fake, c = _fake_with_429(value, n=99, on="get")
    with pytest.raises(RateLimited) as e:
        c.get_status(new_reference_id())
    ra = e.value.retry_after
    assert ra is None or 0 <= ra <= 30.0


def test_retry_after_is_clamped_and_stored_clamped():
    fake, c = _fake_with_429("999999", n=99)
    with pytest.raises(RateLimited) as e:
        c.request_to_pay(
            reference_id=new_reference_id(),
            amount_ugx=1000,
            payer_msisdn="0772123456",
            external_id="e",
        )
    assert e.value.retry_after == c.config.backoff_max_seconds
    assert set(fake.sleeps) == {c.config.backoff_max_seconds}


def test_retry_after_negative_clamped_to_zero():
    fake, c = _fake_with_429("-5", n=99, on="get")
    with pytest.raises(RateLimited) as e:
        c.get_status(new_reference_id())
    assert e.value.retry_after in (0.0, None)
    assert all(s >= 0 for s in fake.sleeps)
