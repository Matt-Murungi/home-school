"""Money-path contract of `_create` (round 2).

After a POST that MAY have been accepted, only three things may escape: a PaymentResult,
OutcomeUnknown, or a definite 4xx rejection of the POST itself. A second POST (same reference)
is allowed ONLY after the status query returned a definite 404.
"""

import json

import pytest

from py_momo import HttpCollectionsClient, HttpDisbursementsClient, new_reference_id
from py_momo.errors import (
    AuthError,
    OutcomeUnknown,
    RateLimited,
    RequestRejected,
    TransportError,
    TransportTimeout,
)
from py_momo.fake import FakeMomo, Scenario
from py_momo.status import Status
from py_momo.transport import HttpResponse

MSISDN = "0772123456"


class Intercept:
    """Transport wrapper: scripted answers for status GETs (and POSTs), else the fake."""

    def __init__(self, fake: FakeMomo, get=None, post=None):
        self.fake, self.get, self.post = fake, get, post

    def send(self, req):
        hook = self.get if req.method == "GET" else self.post
        if hook is not None and "/token/" not in req.url:
            out = hook(req)
            if out is not None:
                if isinstance(out, Exception):
                    raise out
                return out
        return self.fake.send(req)

    def client(self, cls=HttpCollectionsClient, **cfg):
        return cls(self.fake.config(**cfg), self, **self.fake._kw())


def _http(status, body=b"{}", headers=None):
    return HttpResponse(status, headers or {}, body)


STATUS_FAILURES = {
    "401": lambda: _http(401),
    "403": lambda: _http(403, b'{"code":"FORBIDDEN"}'),
    "400": lambda: _http(400, b'{"code":"BAD"}'),
    "500": lambda: _http(500),
    "503": lambda: _http(503),
    "429": lambda: _http(429, headers={"Retry-After": "1"}),
    "timeout": lambda: TransportTimeout("status timeout"),
    "conn": lambda: TransportError("status reset"),
    "garbage": lambda: _http(200, b"<html>not json</html>"),
}


def _create(client, ref, cls_call="rtp"):
    if cls_call == "rtp":
        return client.request_to_pay(
            reference_id=ref, amount_ugx=1000, payer_msisdn=MSISDN, external_id=ref
        )
    return client.transfer(reference_id=ref, amount_ugx=1000, payee_msisdn=MSISDN, external_id=ref)


def _status_always(kind):
    return lambda req: STATUS_FAILURES[kind]()


# ---- (1) never re-POST unless status is a definite 404 -------------------------------
@pytest.mark.parametrize("fault", ["timeout_before", "timeout_after", "5xx", "5xx_after"])
@pytest.mark.parametrize("status_kind", list(STATUS_FAILURES))
def test_inconclusive_status_raises_outcome_unknown_and_never_reposts(fault, status_kind):
    fake = FakeMomo()
    ref = new_reference_id()
    fake.script(ref, Scenario(post_faults=[fault]))
    c = Intercept(fake, get=_status_always(status_kind)).client()
    with pytest.raises(OutcomeUnknown) as e:
        _create(c, ref)
    assert e.value.reference_id == ref
    assert e.value.detail  # says what is known
    assert fake.post_count == 1  # NEVER a second POST while status is inconclusive


def test_original_repro_timeout_before_and_status_timeouts_forever():
    fake = FakeMomo()
    ref = new_reference_id()
    fake.script(ref, Scenario(post_faults=["timeout_before"]))
    c = Intercept(fake, get=lambda r: TransportTimeout("t")).client()
    with pytest.raises(OutcomeUnknown):
        _create(c, ref)
    assert fake.post_count == 1


def test_outcome_unknown_preserves_cause():
    fake = FakeMomo()
    ref = new_reference_id()
    fake.script(ref, Scenario(post_faults=["timeout_after"]))
    c = Intercept(fake, get=_status_always("403")).client()
    with pytest.raises(OutcomeUnknown) as e:
        _create(c, ref)
    assert isinstance(e.value.__cause__, RequestRejected)
    assert fake.accepted_count() == 1  # the payment WAS created: that is why this matters


def test_definite_404_allows_repost_with_same_reference():
    fake = FakeMomo()
    ref = new_reference_id()
    fake.script(ref, Scenario(post_faults=["5xx"]))  # not accepted
    c = Intercept(fake).client()
    res = _create(c, ref)
    assert res.status is Status.PENDING
    assert fake.post_count == 2 and fake.accepted_count() == 1
    posts = [r for r in fake.requests if r.method == "POST" and "requesttopay" in r.url]
    assert {p.headers["X-Reference-Id"] for p in posts} == {ref}


def test_status_503_on_every_query_never_reposts():
    fake = FakeMomo()
    ref = new_reference_id()
    fake.script(ref, Scenario(post_faults=["timeout_before"]))
    calls = {"n": 0}

    def get(req):  # first status query: all attempts 503; never a 404 -> no repost
        calls["n"] += 1
        return _http(503)

    c = Intercept(fake, get=get).client(max_attempts=3)
    with pytest.raises(OutcomeUnknown):
        _create(c, ref)
    assert fake.post_count == 1


def test_repeated_404_exhausts_attempts_with_bounded_posts():
    fake = FakeMomo()
    ref = new_reference_id()
    fake.script(ref, Scenario(post_faults=["5xx"] * 10))
    c = Intercept(fake).client(max_attempts=3)
    with pytest.raises(OutcomeUnknown):
        _create(c, ref)
    assert fake.post_count == 3 and fake.accepted_count() == 0


# ---- (2) only result / OutcomeUnknown / 4xx-of-the-POST may escape --------------------
@pytest.mark.parametrize("status_kind", ["401", "403", "400", "500", "timeout", "garbage", "429"])
def test_accepted_but_response_lost_then_status_fails_is_outcome_unknown(status_kind):
    fake = FakeMomo()
    ref = new_reference_id()
    fake.script(ref, Scenario(post_faults=["timeout_after"]))
    c = Intercept(fake, get=_status_always(status_kind)).client()
    with pytest.raises(OutcomeUnknown):
        _create(c, ref)
    assert fake.post_count == 1 and fake.accepted_count() == 1


def test_status_401_after_refresh_does_not_leak_auth_error():
    fake = FakeMomo()
    ref = new_reference_id()
    fake.script(ref, Scenario(post_faults=["timeout_after"]))
    c = Intercept(fake, get=_status_always("401")).client()
    with pytest.raises(OutcomeUnknown) as e:
        _create(c, ref)
    assert isinstance(e.value.__cause__, AuthError)


@pytest.mark.parametrize("status_kind", ["401", "403", "500", "timeout", "garbage", "429"])
def test_409_then_status_fails_is_outcome_unknown(status_kind):
    fake = FakeMomo()
    ref = new_reference_id()
    c0 = fake.collections()
    _create(c0, ref)  # payment exists
    c = Intercept(fake, get=_status_always(status_kind)).client()
    with pytest.raises(OutcomeUnknown) as e:
        _create(c, ref)
    assert e.value.reference_id == ref
    assert fake.post_count == 2  # one real + the 409; no third


def test_409_then_status_404_is_outcome_unknown_not_notfound():
    fake = FakeMomo()
    ref = new_reference_id()
    c = Intercept(
        fake,
        post=lambda r: _http(409, b'{"code":"RESOURCE_ALREADY_EXIST"}'),
        get=lambda r: _http(404, b'{"code":"RESOURCE_NOT_FOUND"}'),
    ).client()
    with pytest.raises(OutcomeUnknown) as e:
        _create(c, ref)
    assert e.value.reference_id == ref
    assert fake.post_count == 0  # intercepted 409 never reached the fake; nothing re-sent


@pytest.mark.parametrize("code", [400, 403, 404, 405, 422])
def test_post_rejected_4xx_with_response_is_definite_and_not_retried(code):
    fake = FakeMomo()
    ref = new_reference_id()
    c = Intercept(fake, post=lambda r: _http(code, b'{"code":"NOPE","message":"m"}')).client()
    with pytest.raises(RequestRejected) as e:
        _create(c, ref)
    assert e.value.status_code == code
    assert [r.method for r in fake.requests if "requesttopay" in r.url] == []  # no status query


def test_post_401_after_refresh_is_auth_error_not_unknown():
    fake = FakeMomo()
    ref = new_reference_id()
    c = Intercept(fake, post=lambda r: _http(401)).client()
    with pytest.raises(AuthError):
        _create(c, ref)


def test_accepted_response_lost_status_ok_returns_real_status():
    fake = FakeMomo()
    ref = new_reference_id()
    fake.script(ref, Scenario.failure("NOT_ENOUGH_FUNDS", post_faults=["timeout_after"]))
    res = _create(fake.collections(), ref)
    assert res.status is Status.FAILED and fake.post_count == 1


def test_transfer_same_contract_for_disbursements():
    fake = FakeMomo()
    ref = new_reference_id()
    fake.script(ref, Scenario(post_faults=["timeout_after"]))
    c = Intercept(fake, get=_status_always("403")).client(HttpDisbursementsClient)
    with pytest.raises(OutcomeUnknown):
        _create(c, ref, "transfer")
    assert fake.post_count == 1 and fake.accepted_count() == 1


# ---- 429 on the POST itself -----------------------------------------------------------
def test_429_on_post_is_definite_not_accepted_and_retries_same_reference():
    """A received 429 means MTN throttled before processing: safe to wait and POST again, with
    the same reference, WITHOUT a status query. If throttling persists: RateLimited (nothing
    was created), not OutcomeUnknown."""
    fake = FakeMomo()
    ref = new_reference_id()
    fake.script(ref, Scenario(post_faults=["429", "429"]))
    res = _create(fake.collections(), ref)
    assert res.status is Status.PENDING and fake.post_count == 3 and fake.accepted_count() == 1
    assert fake.sleeps[:2] == [2.0, 2.0]  # Retry-After honoured
    assert not [r for r in fake.requests if r.method == "GET" and "requesttopay" in r.url]


def test_persistent_429_raises_rate_limited_nothing_created():
    fake = FakeMomo()
    ref = new_reference_id()
    fake.script(ref, Scenario(post_faults=["429"] * 10))
    with pytest.raises(RateLimited):
        _create(fake.collections(max_attempts=3), ref)
    assert fake.post_count == 3 and fake.accepted_count() == 0


# ---- (3) verify_callback fails closed on amount/currency ------------------------------
def _cb(fake, status_body):
    c = Intercept(fake, get=lambda r: _http(200, json.dumps(status_body).encode())).client()
    ref = new_reference_id()
    raw = json.dumps({"externalId": ref, "status": "SUCCESSFUL"}).encode()
    return c, ref, raw


GOOD = {"status": "SUCCESSFUL", "amount": "1000", "currency": "EUR"}


def test_callback_good_amount_and_currency_accepted():
    c, ref, raw = _cb(FakeMomo(), GOOD)
    assert c.verify_callback(raw, reference_id=ref, expected_amount_ugx=1000).agrees


@pytest.mark.parametrize(
    "mutate",
    [
        {"amount": None},  # missing amount
        {"amount": "12.5"},  # fractional: unparseable as UGX
        {"amount": "abc"},  # garbage
        {"amount": ""},
        {"currency": "USD"},  # wrong currency
        {"currency": None},  # missing currency
        {"amount": "2000"},  # mismatch (pre-existing behaviour)
    ],
)
def test_callback_fails_closed_when_mtn_amount_or_currency_unusable(mutate):
    from py_momo.errors import CallbackRejected

    body = {**GOOD, **mutate}
    body = {k: v for k, v in body.items() if v is not None}
    c, ref, raw = _cb(FakeMomo(), body)
    with pytest.raises(CallbackRejected):
        c.verify_callback(raw, reference_id=ref, expected_amount_ugx=1000)


def test_callback_without_expected_amount_does_not_require_amount():
    c, ref, raw = _cb(FakeMomo(), {"status": "SUCCESSFUL"})
    assert c.verify_callback(raw, reference_id=ref).result.status is Status.SUCCESSFUL


# ---- unexpected non-error POST statuses are ambiguous, never a silent success or re-POST ----
@pytest.mark.parametrize("code", [100, 203, 204, 301, 302, 304])
def test_unexpected_post_status_is_probed_not_assumed_success_and_not_reposted(code):
    fake = FakeMomo()
    ref = new_reference_id()
    sent = []

    def post(req):
        sent.append(req)
        return _http(code)

    c = Intercept(fake, post=post, get=_status_always("503")).client()
    with pytest.raises(OutcomeUnknown):
        _create(c, ref)
    assert len(sent) == 1  # one POST only; status inconclusive


@pytest.mark.parametrize("code", [200, 201, 202])
def test_documented_accept_codes_are_pending(code):
    c = Intercept(FakeMomo(), post=lambda r: _http(code)).client()
    assert _create(c, new_reference_id()).status is Status.PENDING
