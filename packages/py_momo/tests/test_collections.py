import json
import threading
import uuid

import pytest

from py_momo import (
    Environment,
    FailureReason,
    HttpCollectionsClient,
    MomoConfig,
    PollSchedule,
    Status,
    new_reference_id,
    poll_until_final,
)
from py_momo.errors import (
    AuthError,
    InvalidAmount,
    InvalidMsisdn,
    InvalidReference,
    MomoConfigError,
    NotFound,
    OutcomeUnknown,
    RateLimited,
    RequestRejected,
    ServerError,
)
from py_momo.fake import FAKE_API_KEY, FAKE_SUB_KEY, FakeMomo, Scenario

MSISDN = "0772123456"


def pay(client, ref=None, amount=1000, **kw):
    ref = ref or new_reference_id()
    kw.setdefault("payer_msisdn", MSISDN)
    kw.setdefault("external_id", ref)
    return ref, client.request_to_pay(reference_id=ref, amount_ugx=amount, **kw)


# ---- headers / request shape (AC: typed client, X-Reference-Id on every request) ---------
def test_request_headers_and_body(fake):
    c = fake.collections()
    ref, res = pay(c)
    assert res.status is Status.PENDING
    post = next(r for r in fake.requests if r.method == "POST" and "requesttopay" in r.url)
    assert post.url.endswith("/collection/v1_0/requesttopay")
    h = post.headers
    assert h["X-Reference-Id"] == ref and str(uuid.UUID(ref)) == ref
    assert h["X-Target-Environment"] == "sandbox"
    assert h["Ocp-Apim-Subscription-Key"] == FAKE_SUB_KEY
    assert h["Authorization"].startswith("Bearer ")
    assert h["X-Callback-Url"] == "https://example.invalid/hooks/momo"
    assert post.timeout == 15.0
    body = json.loads(post.body)
    assert body["amount"] == "1000" and isinstance(body["amount"], str)
    assert body["currency"] == "EUR"  # sandbox restriction
    assert body["payer"] == {"partyIdType": "MSISDN", "partyId": "256772123456"}


def test_token_request_shape(fake):
    pay(fake.collections())
    tok = fake.requests[0]
    assert tok.method == "POST" and tok.url.endswith("/collection/token/")
    assert tok.headers["Authorization"].startswith("Basic ")


def test_status_get_has_target_env_not_reference_header(fake):
    c = fake.collections()
    ref, _ = pay(c)
    c.get_status(ref)
    get = fake.requests[-1]
    assert get.method == "GET" and get.url.endswith(f"/requesttopay/{ref}")
    assert "X-Reference-Id" not in get.headers
    assert get.headers["X-Target-Environment"] == "sandbox"


def test_reference_must_be_uuid4(fake):
    c = fake.collections()
    for bad in ["abc", "", None, str(uuid.uuid1())]:
        with pytest.raises(InvalidReference):
            c.request_to_pay(
                reference_id=bad, amount_ugx=1000, payer_msisdn=MSISDN, external_id="x"
            )
    assert fake.post_count == 0


# ---- environment / currency ----------------------------------------------------------
def test_config_defaults_and_restrictions():
    s = MomoConfig(Environment.SANDBOX, "k", "u", "p")
    assert (s.currency, s.target_environment) == ("EUR", "sandbox")
    p = MomoConfig(Environment.PRODUCTION, "k", "u", "p")
    assert (p.currency, p.target_environment) == ("UGX", "mtnuganda")
    assert p.base_url.startswith("https://")
    with pytest.raises(MomoConfigError):
        MomoConfig(Environment.PRODUCTION, "k", "u", "p", currency="EUR")
    with pytest.raises(MomoConfigError):
        MomoConfig(Environment.PRODUCTION, "k", "u", "p", target_environment="sandbox")
    with pytest.raises(MomoConfigError):
        MomoConfig(Environment.SANDBOX, "k", "u", "p", currency="USD")
    with pytest.raises(MomoConfigError):
        MomoConfig(Environment.SANDBOX, "", "u", "p")


def test_config_repr_hides_secrets():
    r = repr(MomoConfig(Environment.SANDBOX, "SUBKEY123", "USER123", "APIKEY123"))
    assert "SUBKEY123" not in r and "APIKEY123" not in r and "USER123" not in r


def test_production_uses_ugx_in_body():
    f = FakeMomo()
    # production config against fake: fake rejects non-EUR (sandbox restriction), proving the
    # client really sends UGX in production
    c = HttpCollectionsClient(
        MomoConfig(
            Environment.PRODUCTION,
            FAKE_SUB_KEY,
            "fake-api-user",
            FAKE_API_KEY,
            base_url="https://fake.example",
            target_environment="x",
            min_amount_ugx=None,
            max_amount_ugx=None,
        ),
        f,
        sleep=f.sleep,
        clock=f.clock.now,
    )
    with pytest.raises(RequestRejected):
        c.request_to_pay(
            reference_id=new_reference_id(), amount_ugx=1000, payer_msisdn=MSISDN, external_id="x"
        )
    body = json.loads(
        next(r for r in f.requests if r.method == "POST" and "requesttopay" in r.url).body
    )
    assert body["currency"] == "UGX"


# ---- validation before any network call ----------------------------------------------
@pytest.mark.parametrize("amount", [1000.0, 0, -1, True, "1000"])
def test_bad_amounts_never_reach_network(fake, amount):
    with pytest.raises(InvalidAmount):
        pay(fake.collections(), amount=amount)
    assert fake.requests == []


def test_amount_limits_enforced():
    f = FakeMomo()
    c = f.collections(min_amount_ugx=500, max_amount_ugx=5_000_000)
    with pytest.raises(InvalidAmount):
        pay(c, amount=499)
    with pytest.raises(InvalidAmount):
        pay(c, amount=5_000_001)
    pay(c, amount=500)
    pay(c, amount=5_000_000)
    assert f.accepted_count() == 2


def test_bad_msisdn_never_reaches_network(fake):
    with pytest.raises(InvalidMsisdn):
        pay(fake.collections(), payer_msisdn="12345")
    assert fake.requests == []


# ---- statuses ------------------------------------------------------------------------
def test_success(fake):
    c = fake.collections()
    ref, _ = pay(c)
    r = c.get_status(ref)
    assert r.status is Status.SUCCESSFUL and r.amount_ugx == 1000
    assert r.financial_transaction_id and r.is_final


def test_pending_then_success_with_polling_helper(fake):
    c = fake.collections()
    ref = new_reference_id()
    fake.script(ref, Scenario.pending_then(3))
    pay(c, ref)
    r = poll_until_final(
        c.get_status, ref, PollSchedule((1, 2)), sleep=fake.sleep, clock=fake.clock.now
    )
    assert r.status is Status.SUCCESSFUL
    assert fake.sleeps == [1.0, 2.0, 2.0]


def test_polling_stops_at_deadline_and_stays_pending(fake):
    c = fake.collections()
    ref = new_reference_id()
    fake.script(ref, Scenario.never_final())
    pay(c, ref)
    r = poll_until_final(
        c.get_status,
        ref,
        PollSchedule((10,), deadline_seconds=35),
        sleep=fake.sleep,
        clock=fake.clock.now,
    )
    assert r.status is Status.PENDING  # never converted to failed
    assert sum(fake.sleeps) <= 35


def test_poll_schedule_repeats_last_delay():
    s = PollSchedule((1, 5))
    assert [s.delay_for(i) for i in range(4)] == [1, 5, 5, 5]


@pytest.mark.parametrize(
    "reason", [x.name for x in FailureReason if x is not FailureReason.UNKNOWN]
)
def test_failure_each_reason(fake, reason):
    c = fake.collections()
    ref = new_reference_id()
    fake.script(ref, Scenario.failure(reason))
    pay(c, ref)
    r = c.get_status(ref)
    assert (
        r.status is Status.FAILED and r.reason is FailureReason[reason] and r.raw_reason == reason
    )


def test_failure_unknown_reason_keeps_raw_code(fake):
    c = fake.collections()
    ref = new_reference_id()
    fake.script(ref, Scenario.failure("SOME_NEW_MTN_CODE"))
    pay(c, ref)
    r = c.get_status(ref)
    assert r.status is Status.FAILED
    assert r.reason is FailureReason.UNKNOWN and r.raw_reason == "SOME_NEW_MTN_CODE"


def test_failed_without_reason_is_unknown_not_success(fake):
    c = fake.collections()
    ref = new_reference_id()
    fake.script(ref, Scenario(final_status="FAILED"))
    pay(c, ref)
    r = c.get_status(ref)
    assert r.status is Status.FAILED and r.reason is FailureReason.UNKNOWN


def test_status_unknown_reference_is_not_found(fake):
    with pytest.raises(NotFound):
        fake.collections().get_status(new_reference_id())


def test_garbage_status_body_is_transient_error(fake):
    c = fake.collections(max_attempts=2)
    ref, _ = pay(c)
    fake.raw_responses[ref] = "<html>oops"
    with pytest.raises(ServerError):
        c.get_status(ref)


# ---- idempotency (AC: X-Reference-Id; NFR-8) ------------------------------------------
def test_same_reference_twice_creates_one_payment(fake):
    c = fake.collections()
    ref, _ = pay(c)
    _, again = pay(c, ref)
    assert fake.accepted_count() == 1
    assert fake.post_count == 2  # second POST sent, MTN answered 409, client returned status
    assert again.reference_id == ref and again.status is Status.SUCCESSFUL


def test_duplicate_reference_returns_existing_result_not_error(fake):
    c = fake.collections()
    ref = new_reference_id()
    fake.script(ref, Scenario.failure("NOT_ENOUGH_FUNDS"))
    pay(c, ref)
    _, again = pay(c, ref)
    assert again.status is Status.FAILED and again.reason is FailureReason.NOT_ENOUGH_FUNDS


def test_timeout_after_accepted_queries_status_never_resends(fake):
    c = fake.collections()
    ref = new_reference_id()
    fake.script(ref, Scenario.pending_then(1, post_faults=["timeout_after"]))
    _, res = pay(c, ref)
    assert fake.post_count == 1  # NOT resent blindly
    assert fake.accepted_count() == 1
    assert res.reference_id == ref and res.status in (Status.PENDING, Status.SUCCESSFUL)
    kinds = [r.method for r in fake.requests if "requesttopay" in r.url]
    assert kinds == ["POST", "GET"]


def test_timeout_before_accept_resends_same_reference_once_known_absent(fake):
    c = fake.collections()
    ref = new_reference_id()
    fake.script(ref, Scenario(post_faults=["timeout_before"]))
    pay(c, ref)
    assert fake.accepted_count() == 1
    posts = [r for r in fake.requests if r.method == "POST" and "requesttopay" in r.url]
    assert len(posts) == 2 and {p.headers["X-Reference-Id"] for p in posts} == {ref}
    # order: POST (timeout), GET status (404), POST again
    seq = [r.method for r in fake.requests if "requesttopay" in r.url]
    assert seq == ["POST", "GET", "POST"]


def test_5xx_after_accept_is_not_double_created(fake):
    c = fake.collections()
    ref = new_reference_id()
    fake.script(ref, Scenario(post_faults=["5xx_after"]))
    pay(c, ref)
    assert fake.accepted_count() == 1 and fake.post_count == 1


def test_5xx_then_ok(fake):
    c = fake.collections()
    ref = new_reference_id()
    fake.script(ref, Scenario(post_faults=["5xx", "5xx"]))
    pay(c, ref)
    assert fake.accepted_count() == 1
    assert fake.sleeps  # backed off between attempts


def test_persistent_5xx_gives_outcome_unknown_after_bounded_attempts(fake):
    c = fake.collections(max_attempts=3)
    ref = new_reference_id()
    fake.script(ref, Scenario(post_faults=["5xx"] * 10))
    with pytest.raises(OutcomeUnknown) as e:
        pay(c, ref)
    assert e.value.reference_id == ref and e.value.retryable
    assert fake.post_count == 3


def test_429_backoff_honours_retry_after(fake):
    c = fake.collections()
    ref = new_reference_id()
    fake.script(ref, Scenario(post_faults=["429"]))
    pay(c, ref)
    assert fake.sleeps[0] == 2.0 and fake.accepted_count() == 1


def test_get_status_retries_5xx_429_timeouts_then_succeeds(fake):
    c = fake.collections()
    ref, _ = pay(c)
    fake.payments[("collection", ref)].scenario.get_faults = ["5xx", "429", "timeout"]
    assert c.get_status(ref).status is Status.SUCCESSFUL
    assert len(fake.sleeps) == 3


def test_get_status_gives_up_with_retryable_error(fake):
    c = fake.collections(max_attempts=2)
    ref, _ = pay(c)
    fake.payments[("collection", ref)].scenario.get_faults = ["5xx"] * 5
    with pytest.raises(ServerError) as e:
        c.get_status(ref)
    assert e.value.retryable
    fake.payments[("collection", ref)].scenario.get_faults = ["429"] * 5
    with pytest.raises(RateLimited):
        c.get_status(ref)


def test_backoff_is_exponential_and_capped(fake):
    c = fake.collections(max_attempts=6, backoff_base_seconds=1, backoff_max_seconds=5)
    ref, _ = pay(c)
    fake.payments[("collection", ref)].scenario.get_faults = ["5xx"] * 5
    c.get_status(ref)
    assert fake.sleeps == [0.5, 1.0, 2.0, 2.5, 2.5]  # jitter fixed at 0 => factor 0.5; cap 5


def test_permanent_4xx_not_retried(fake):
    c = fake.collections(currency="UGX")  # sandbox rejects UGX -> 400
    with pytest.raises(RequestRejected) as e:
        pay(c)
    assert e.value.status_code == 400 and not e.value.retryable
    assert fake.post_count == 1 and not fake.sleeps


# ---- tokens ------------------------------------------------------------------------
def test_token_cached_across_calls(fake):
    c = fake.collections()
    for _ in range(3):
        pay(c)
    assert fake.token_requests == 1


def test_token_refreshed_before_expiry_margin(fake):
    c = fake.collections(token_expiry_margin_seconds=60)
    pay(c)
    fake.clock.advance(3600 - 61)
    pay(c)
    assert fake.token_requests == 1
    fake.clock.advance(2)  # inside the margin
    pay(c)
    assert fake.token_requests == 2


def test_401_mid_request_refreshes_and_retries_once(fake):
    c = fake.collections()
    pay(c)
    fake.expire_all_tokens()
    ref, res = pay(c)
    assert fake.token_requests == 2 and fake.accepted_count() == 2
    assert res.status is Status.PENDING


def test_401_twice_raises_auth_error_without_looping(fake):
    c = fake.collections()
    pay(c)
    fake._valid_tokens.clear()
    # make every freshly issued token invalid immediately
    real_token = fake._token

    def bad_token(h):
        r = real_token(h)
        fake._valid_tokens.clear()
        return r

    fake._token = bad_token
    with pytest.raises(AuthError):
        pay(c)
    assert fake.token_requests <= 3


def test_bad_credentials_auth_error():
    f = FakeMomo()
    c = f.collections(api_key="wrong")
    with pytest.raises(AuthError):
        pay(c)
    assert f.post_count == 0


def test_token_endpoint_5xx_is_retryable_server_error(fake):
    fake.token_faults = ["5xx"]
    with pytest.raises(ServerError):
        pay(fake.collections())


def test_token_manager_thread_safe_single_flight(fake):
    c = fake.collections()
    errs = []

    def work():
        try:
            pay(c)
        except Exception as e:  # pragma: no cover
            errs.append(e)

    ts = [threading.Thread(target=work) for _ in range(12)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert not errs and fake.token_requests == 1 and fake.accepted_count() == 12


# ---- callbacks (SEC-8) -------------------------------------------------------------
def test_callback_before_polling_verified_via_status_call(fake):
    c = fake.collections()
    ref = new_reference_id()
    fake.script(ref, Scenario(callback="on_accept"))
    pay(c, ref)
    ((_, cref, body),) = fake.deliveries
    n = len(fake.requests)
    v = c.verify_callback(body, reference_id=cref)
    assert v.raw_body == body  # stored untouched
    assert v.result.status is Status.SUCCESSFUL and v.agrees
    assert any(r.method == "GET" for r in fake.requests[n:])  # asked MTN


def test_forged_callback_claiming_success_is_not_trusted(fake):
    c = fake.collections()
    ref = new_reference_id()
    fake.script(ref, Scenario.never_final())
    pay(c, ref)
    forged = json.dumps({"externalId": ref, "status": "SUCCESSFUL", "amount": "1000"}).encode()
    v = c.verify_callback(forged, reference_id=ref)
    assert v.body_status is Status.SUCCESSFUL
    assert v.result.status is Status.PENDING and v.agrees is False


def test_forged_callback_claiming_failure_for_paid_payment(fake):
    c = fake.collections()
    ref, _ = pay(c)
    forged = json.dumps({"externalId": ref, "status": "FAILED", "reason": "EXPIRED"}).encode()
    v = c.verify_callback(forged, reference_id=ref)
    assert v.result.status is Status.SUCCESSFUL and not v.agrees


def test_callback_for_unknown_reference_rejected(fake):
    from py_momo.errors import CallbackRejected

    body = json.dumps({"externalId": new_reference_id(), "status": "SUCCESSFUL"}).encode()
    with pytest.raises(CallbackRejected):
        fake.collections().verify_callback(body)


@pytest.mark.parametrize("raw", [b"", b"not json", b"[1]", b'{"status":"SUCCESSFUL"}'])
def test_callback_malformed_or_unidentifiable_rejected(fake, raw):
    from py_momo.errors import CallbackRejected

    with pytest.raises(CallbackRejected):
        fake.collections().verify_callback(raw)


def test_callback_reference_mismatch_rejected(fake):
    from py_momo.errors import CallbackRejected

    c = fake.collections()
    a, _ = pay(c)
    b, _ = pay(c)
    body = json.dumps({"externalId": a, "status": "SUCCESSFUL"}).encode()
    with pytest.raises(CallbackRejected):
        c.verify_callback(body, reference_id=b)


def test_callback_amount_mismatch_rejected(fake):
    from py_momo.errors import CallbackRejected

    c = fake.collections()
    ref, _ = pay(c, amount=1000)
    body = json.dumps({"externalId": ref, "status": "SUCCESSFUL"}).encode()
    with pytest.raises(CallbackRejected):
        c.verify_callback(body, reference_id=ref, expected_amount_ugx=2000)
    assert c.verify_callback(body, reference_id=ref, expected_amount_ugx=1000).agrees


def test_callback_never_arrives_polling_still_resolves(fake):
    c = fake.collections()
    ref = new_reference_id()
    fake.script(ref, Scenario.pending_then(2, callback="none"))
    pay(c, ref)
    r = poll_until_final(
        c.get_status, ref, PollSchedule((1,)), sleep=fake.sleep, clock=fake.clock.now
    )
    assert fake.deliveries == [] and r.status is Status.SUCCESSFUL


def test_callback_after_final_matches_polling(fake):
    c = fake.collections()
    ref = new_reference_id()
    fake.script(ref, Scenario.failure("PAYER_NOT_FOUND", callback="on_final"))
    pay(c, ref)
    c.get_status(ref)
    ((_, cref, body),) = fake.deliveries
    v = c.verify_callback(body)  # reference from externalId
    assert v.result.reason is FailureReason.PAYER_NOT_FOUND and v.agrees


# ---- logging (PII) -----------------------------------------------------------------
def test_logs_never_contain_pii_or_secrets(fake, caplog):
    import logging

    caplog.set_level(logging.DEBUG, logger="py_momo")
    c = fake.collections()
    ref = new_reference_id()
    fake.script(ref, Scenario(post_faults=["timeout_after"]))
    pay(c, ref, payer_msisdn="+256772123456")
    fake.expire_all_tokens()
    pay(c)
    text = " ".join(r.getMessage() for r in caplog.records)
    assert text  # something was logged
    for secret in ("772123456", "0772123456", "tok-", FAKE_API_KEY, FAKE_SUB_KEY):
        assert secret not in text
    assert "256***456" in text
