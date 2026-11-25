import json

import pytest

from py_momo import FailureReason, Status, new_reference_id
from py_momo.errors import InvalidAmount, InvalidMsisdn, OutcomeUnknown
from py_momo.fake import FAKE_SUB_KEY, Scenario


def send(c, ref=None, **kw):
    ref = ref or new_reference_id()
    kw.setdefault("payee_msisdn", "+256 782 000 111")
    kw.setdefault("amount_ugx", 50_000)
    return ref, c.transfer(reference_id=ref, external_id=ref, **kw)


def test_transfer_shape_and_separate_product_credentials(fake):
    c = fake.disbursements()
    ref, res = send(c)
    assert res.status is Status.PENDING
    paths = [r.url for r in fake.requests]
    assert paths[0].endswith("/disbursement/token/")
    post = fake.requests[1]
    assert post.url.endswith("/disbursement/v1_0/transfer") and post.method == "POST"
    assert post.headers["X-Reference-Id"] == ref
    assert post.headers["Ocp-Apim-Subscription-Key"] == FAKE_SUB_KEY
    body = json.loads(post.body)
    assert body["payee"] == {"partyIdType": "MSISDN", "partyId": "256782000111"}
    assert body["amount"] == "50000" and "payer" not in body


def test_transfer_status_and_failure_reasons(fake):
    c = fake.disbursements()
    ref = new_reference_id()
    fake.script(ref, Scenario.failure({"code": "PAYEE_NOT_FOUND", "message": "x"}))
    send(c, ref)
    r = c.get_status(ref)
    assert r.status is Status.FAILED and r.reason is FailureReason.PAYEE_NOT_FOUND


def test_transfer_duplicate_reference_never_pays_twice(fake):
    c = fake.disbursements()
    ref, _ = send(c)
    send(c, ref)
    send(c, ref)
    assert fake.accepted_count() == 1


def test_transfer_timeout_after_accept_does_not_resend(fake):
    c = fake.disbursements()
    ref = new_reference_id()
    fake.script(ref, Scenario(post_faults=["timeout_after"]))
    send(c, ref)
    assert fake.post_count == 1 and fake.accepted_count() == 1


def test_transfer_unknown_outcome_when_status_unobtainable(fake):
    c = fake.disbursements(max_attempts=2)
    ref = new_reference_id()
    fake.script(ref, Scenario(post_faults=["timeout_after", "timeout_after"]))
    send(c, ref)  # accepted; status query works -> fine
    fake2_ref = new_reference_id()
    fake.script(fake2_ref, Scenario(post_faults=["5xx"] * 5))
    with pytest.raises(OutcomeUnknown):
        send(c, fake2_ref)


def test_transfer_limits_and_msisdn(fake):
    c = fake.disbursements(min_amount_ugx=1000, max_amount_ugx=100_000)
    with pytest.raises(InvalidAmount):
        send(c, amount_ugx=999)
    with pytest.raises(InvalidAmount):
        send(c, amount_ugx=100_001)
    with pytest.raises(InvalidAmount):
        send(c, amount_ugx=2000.0)
    with pytest.raises(InvalidMsisdn):
        send(c, payee_msisdn="nope")
    assert fake.post_count == 0


def test_collections_and_disbursements_tokens_independent(fake):
    send(fake.disbursements())
    from tests.test_collections import pay

    pay(fake.collections())
    assert fake.token_requests == 2


def test_transfer_401_refresh_once(fake):
    c = fake.disbursements()
    send(c)
    fake.expire_all_tokens()
    send(c)
    assert fake.token_requests == 2 and fake.accepted_count() == 2
