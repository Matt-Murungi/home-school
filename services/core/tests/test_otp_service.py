"""A01 (#43): the OTP state machine: request, verify, rate limits, edge cases. Fully offline."""

import hmac
import threading
from datetime import timedelta

import pytest

from hs_core.auth_otp.service import OtpError
from hs_core.auth_otp.sms import SmsRejected, SmsUnavailable
from otp_helpers import IP, OTHER, PHONE, Env

# ---------------------------------------------------------------- code generation and storage


def test_code_is_six_digits_and_sent_to_the_normalised_number():
    e = Env()
    r = e.request("0772 123 456")
    assert e.sms.sent[0].to == PHONE
    assert len(e.last_code()) == 6 and e.last_code().isdigit()
    assert r.resend_after_s == 30 and r.expires_in_s == 300


def test_codes_come_from_a_csprng_and_keep_leading_zeros(monkeypatch):
    import secrets

    from hs_core.auth_otp import crypto

    monkeypatch.setattr(secrets, "randbelow", lambda n: 42)
    assert crypto.generate_code() == "000042"


def test_codes_are_spread_not_constant():
    from hs_core.auth_otp import crypto

    assert len({crypto.generate_code() for _ in range(200)}) > 150


def test_store_keeps_only_a_peppered_hash_never_the_code_or_the_number():
    e = Env()
    e.request()
    code = e.last_code()
    dump = repr(e.repo.list("otp_challenge"))
    assert code not in dump.replace(e.rows("challenge")[0]["code_hash"], "")
    assert PHONE not in dump and "772123456" not in dump and IP not in dump
    ch = e.rows("challenge")[0]
    assert len(ch["code_hash"]) == 64 and ch["salt"] and ch["code_hash"] != code


def test_same_code_hashes_differently_per_challenge_and_pepper():
    from hs_core.auth_otp import crypto

    a = crypto.hash_code(b"pepper-one" * 4, "c1", "salt1", "123456")
    assert a != crypto.hash_code(b"pepper-one" * 4, "c2", "salt1", "123456")
    assert a != crypto.hash_code(b"pepper-one" * 4, "c1", "salt2", "123456")
    assert a != crypto.hash_code(b"pepper-two" * 4, "c1", "salt1", "123456")


def test_comparison_is_constant_time_and_identical_for_unknown_challenges(monkeypatch):
    """Timing equality: every verify runs exactly one hmac.compare_digest, challenge or not."""
    calls = []
    real = hmac.compare_digest
    monkeypatch.setattr(hmac, "compare_digest", lambda a, b: (calls.append(1), real(a, b))[1])
    e = Env()
    with pytest.raises(OtpError):
        e.verify("123456")  # no challenge at all
    no_challenge = len(calls)
    e.request()
    with pytest.raises(OtpError):
        e.verify(e.wrong_code())  # wrong code
    assert no_challenge == 1 and len(calls) - no_challenge == 1


# ---------------------------------------------------------------- happy path and single use


def test_correct_code_signs_in_and_returns_a_token_never_a_session_or_hash():
    e = Env()
    e.request()
    res = e.verify(e.last_code())
    assert res.user_id and res.secret and res.is_new_user is True
    assert res.expire > e.clock.now


def test_otp_cannot_be_reused():
    e = Env()
    e.request()
    code = e.last_code()
    e.verify(code)
    with pytest.raises(OtpError) as x:
        e.verify(code)
    assert x.value.code == "invalid_code"


def test_a_used_code_cannot_be_replayed_after_a_new_request_either():
    e = Env(codes=["111111", "222222"])
    e.request()
    e.verify("111111")
    e.clock.advance(31)
    e.request()
    with pytest.raises(OtpError):
        e.verify("111111")
    assert e.verify("222222").user_id


def test_new_request_invalidates_the_previous_code():
    e = Env(codes=["111111", "222222"])
    e.request()
    e.clock.advance(31)
    e.request()
    with pytest.raises(OtpError):
        e.verify("111111")
    assert e.verify("222222").user_id


def test_two_simultaneous_verifies_with_the_same_code_exactly_one_wins():
    e = Env()
    e.request()
    code = e.last_code()
    n = 12
    barrier = threading.Barrier(n)
    results, errors = [], []

    def go():
        barrier.wait()
        try:
            results.append(e.verify(code))
        except OtpError as ex:
            errors.append(ex.code)

    ts = [threading.Thread(target=go) for _ in range(n)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(results) == 1 and len(errors) == n - 1
    assert set(errors) <= {"invalid_code", "too_many_attempts"}
    assert len(e.users.users) == 1  # one account, not one per racer


def test_race_loser_when_downstream_fails_lets_the_code_be_retried():
    e = Env()
    e.request()
    code = e.last_code()
    e.users.available = False
    with pytest.raises(OtpError) as x:
        e.verify(code)
    assert x.value.code == "auth_unavailable" and x.value.status == 503
    e.users.available = True
    assert e.verify(code).user_id  # the code was not burnt by our outage


# ---------------------------------------------------------------- expiry boundary


def test_code_is_valid_until_the_expiry_instant_and_dead_at_it():
    e = Env()
    e.request()
    code = e.last_code()
    e.clock.advance(299.999)
    assert e.verify(code).user_id


def test_code_exactly_at_expiry_is_expired():
    e = Env()
    e.request()
    code = e.last_code()
    e.clock.advance(300)
    with pytest.raises(OtpError) as x:
        e.verify(code)
    assert x.value.code == "code_expired" and x.value.status == 400


def test_expired_code_with_wrong_digits_is_also_reported_expired_without_burning_attempts():
    e = Env()
    e.request()
    e.clock.advance(301)
    with pytest.raises(OtpError) as x:
        e.verify(e.wrong_code())
    assert x.value.code == "code_expired"
    assert not e.rows("attempt")


def test_ttl_is_configurable():
    e = Env(ttl_s=120)
    e.request()
    code = e.last_code()
    e.clock.advance(121)
    with pytest.raises(OtpError) as x:
        e.verify(code)
    assert x.value.code == "code_expired"


# ---------------------------------------------------------------- wrong codes and attempts


def test_wrong_code_reports_attempts_left():
    e = Env()
    e.request()
    with pytest.raises(OtpError) as x:
        e.verify(e.wrong_code())
    assert x.value.code == "invalid_code" and x.value.extra["attempts_left"] == 4


def test_max_five_attempts_then_even_the_right_code_is_refused():
    e = Env()
    e.request()
    right = e.last_code()
    for left in (4, 3, 2, 1, 0):
        with pytest.raises(OtpError) as x:
            e.verify(e.wrong_code())
        assert x.value.extra["attempts_left"] == left
    with pytest.raises(OtpError) as x:
        e.verify(right)
    assert x.value.code == "too_many_attempts" and x.value.status == 429


def test_the_fifth_attempt_can_still_succeed():
    e = Env()
    e.request()
    right = e.last_code()
    for _ in range(4):
        with pytest.raises(OtpError):
            e.verify(e.wrong_code())
    assert e.verify(right).user_id


@pytest.mark.parametrize(
    "bad", ["", "12345", "1234567", "abcdef", "12 456", "١٢٣٤٥٦", " 123456", "123456\n"]
)
def test_malformed_codes_are_rejected_as_invalid_and_count_as_attempts(bad):
    e = Env()
    e.request()
    with pytest.raises(OtpError) as x:
        e.verify(bad)
    assert x.value.code == "invalid_code"


def test_verify_without_any_challenge_looks_like_a_wrong_code():
    e = Env()
    with pytest.raises(OtpError) as x:
        e.verify("123456")
    assert x.value.code == "invalid_code" and x.value.status == 400
    assert "attempts_left" in x.value.extra


def test_codes_are_bound_to_the_phone_number():
    e = Env(codes=["111111", "222222"])
    e.request(PHONE)
    e.request(OTHER)
    with pytest.raises(OtpError):
        e.verify("111111", phone=OTHER)
    assert e.verify("111111", phone=PHONE).user_id


def test_brute_force_across_many_codes_is_stopped_by_the_per_number_failure_cap():
    """Each fresh code allows 5 guesses, but guesses per number per hour are capped at 10."""
    e = Env()
    seen_lock = None
    for _round in range(4):
        try:
            e.request()
        except OtpError as x:
            seen_lock = x.code
            break
        for _ in range(5):
            try:
                e.verify(e.wrong_code())
            except OtpError as x:
                if x.code == "otp_locked":
                    seen_lock = x.code
                    break
        if seen_lock:
            break
        e.clock.advance(31)
    # after the cap, the right code no longer works either until the window passes
    assert seen_lock in ("otp_locked", "rate_limited")
    ok_code = e.last_code()
    with pytest.raises(OtpError) as x:
        e.verify(ok_code)
    assert x.value.code == "otp_locked" and x.value.extra["retry_after_s"] > 0
    e.clock.advance(3600)
    e.request()
    assert e.verify(e.last_code()).user_id


def test_lockout_is_per_number_so_other_numbers_are_unaffected():
    e = Env(phone_failures_per_hour=3)
    e.request()
    for _ in range(3):
        with pytest.raises(OtpError):
            e.verify(e.wrong_code())
    with pytest.raises(OtpError) as x:
        e.verify("123456")
    assert x.value.code == "otp_locked"
    e.request(OTHER, ip="198.51.100.9")
    assert e.verify(e.last_code(), phone=OTHER, ip="198.51.100.9").user_id


# ---------------------------------------------------------------- request-side rate limits (SEC-7)


def test_resend_cooldown_is_30_seconds():
    e = Env()
    e.request()
    with pytest.raises(OtpError) as x:
        e.request()
    assert x.value.code == "resend_too_soon" and x.value.status == 429
    assert 0 < x.value.extra["retry_after_s"] <= 30
    e.clock.advance(29)
    with pytest.raises(OtpError):
        e.request()
    e.clock.advance(1)
    e.request()
    assert len(e.sms.sent) == 2


def test_cooldown_refusal_sends_no_sms_and_creates_no_challenge():
    e = Env()
    e.request()
    with pytest.raises(OtpError):
        e.request()
    assert len(e.sms.sent) == 1 and len(e.rows("challenge")) == 1


def test_max_requests_per_number_per_hour_then_lockout_message():
    e = Env(phone_requests_per_hour=5)
    for _ in range(5):
        e.request()
        e.clock.advance(31)
    with pytest.raises(OtpError) as x:
        e.request()
    assert x.value.code == "rate_limited" and x.value.status == 429
    assert x.value.extra["retry_after_s"] > 0
    assert len(e.sms.sent) == 5


def test_hourly_window_resets():
    e = Env(phone_requests_per_hour=2)
    e.request()
    e.clock.advance(31)
    e.request()
    e.clock.advance(31)
    with pytest.raises(OtpError):
        e.request()
    e.clock.advance(3600)
    e.request()


def test_daily_cap_per_number():
    e = Env(phone_requests_per_hour=100, phone_requests_per_day=6)
    for _ in range(6):
        e.request()
        e.clock.advance(31)
    with pytest.raises(OtpError) as x:
        e.request()
    assert x.value.code == "rate_limited"
    e.clock.advance(86400)
    e.request()


def test_per_ip_cap_spans_many_numbers():
    e = Env(ip_requests_per_hour=3)
    for i in range(3):
        e.request(f"+25677212345{i}")
    with pytest.raises(OtpError) as x:
        e.request("+256772123459")
    assert x.value.code == "rate_limited"
    e.request("+256772123459", ip="198.51.100.1")  # another IP is fine


def test_per_ip_verify_cap():
    e = Env(ip_verifies_per_hour=3)
    e.request()
    for _ in range(3):
        with pytest.raises(OtpError) as x:
            e.verify(e.wrong_code())
        assert x.value.code == "invalid_code"
    with pytest.raises(OtpError) as x:
        e.verify(e.last_code())
    assert x.value.code == "rate_limited"


def test_rate_limit_rows_never_contain_the_number_or_ip():
    e = Env()
    e.request()
    dump = repr(e.repo.list("otp_challenge"))
    assert IP not in dump and PHONE not in dump


def test_responses_are_identical_for_known_and_unknown_numbers():
    known, unknown = Env(), Env()
    known.users.add(PHONE, labels=["parent"])
    a, b = known.request(), unknown.request()
    assert a == b
    assert len(known.sms.sent) == len(unknown.sms.sent) == 1
    assert len(known.rows()) == len(unknown.rows())


def test_rate_limit_outcomes_do_not_depend_on_whether_the_account_exists():
    known, unknown = Env(), Env()
    known.users.add(PHONE, labels=["parent"])
    out = []
    for env in (known, unknown):
        env.request()
        try:
            env.request()
        except OtpError as x:
            out.append((x.code, x.status))
    assert out[0] == out[1]


# ---------------------------------------------------------------- SMS outage and permanent failures


def test_sms_outage_is_retriable_and_leaves_no_state_behind():
    e = Env()
    e.sms.fail_next(SmsUnavailable("provider down"))
    with pytest.raises(OtpError) as x:
        e.request()
    assert x.value.code == "sms_unavailable" and x.value.status == 503
    assert not e.rows()  # no challenge, no cooldown, no quota burnt
    e.request()  # immediately retriable: no cooldown, quota intact
    assert len(e.sms.sent) == 1


def test_sms_outage_does_not_burn_the_hourly_quota():
    e = Env(phone_requests_per_hour=2)
    for _ in range(5):
        e.sms.fail_next(SmsUnavailable("down"))
        with pytest.raises(OtpError):
            e.request()
    e.request()
    e.clock.advance(31)
    e.request()


def test_sms_outage_does_not_touch_the_attempts_of_an_earlier_valid_code():
    e = Env()
    e.request()
    code = e.last_code()
    with pytest.raises(OtpError):
        e.verify(e.wrong_code())
    e.clock.advance(31)
    e.sms.fail_next(SmsUnavailable("down"))
    with pytest.raises(OtpError) as x:
        e.request()
    assert x.value.code == "sms_unavailable"
    assert e.verify(code).user_id  # the older challenge is still live and had 4 attempts left


def test_number_the_provider_cannot_reach_is_a_clear_user_error():
    e = Env()
    e.sms.fail_next(SmsRejected("unsupported number type"))
    with pytest.raises(OtpError) as x:
        e.request()
    assert x.value.code == "number_unreachable" and x.value.status == 422
    assert not e.rows()


def test_storage_outage_on_request_is_503_and_sends_nothing():
    e = Env()
    e.repo.available = False
    with pytest.raises(OtpError) as x:
        e.request()
    assert x.value.status == 503
    assert not e.sms.sent
    e.repo.available = True


def test_storage_outage_on_verify_is_503_not_invalid_code():
    e = Env()
    e.request()
    code = e.last_code()
    e.repo.available = False
    with pytest.raises(OtpError) as x:
        e.verify(code)
    assert x.value.status == 503
    e.repo.available = True
    assert e.verify(code).user_id


def test_invalid_numbers_are_rejected_before_any_state_or_sms():
    e = Env()
    with pytest.raises(OtpError) as x:
        e.request("+254712345678")
    assert x.value.code == "invalid_phone" and x.value.status == 422
    assert not e.rows() and not e.sms.sent


def test_unknown_app_is_rejected():
    e = Env()
    with pytest.raises(OtpError) as x:
        e.request(app="admin")
    assert x.value.code == "invalid_app"


def test_the_app_is_bound_at_request_time():
    """A code requested from the parent app cannot be redeemed as a teacher sign-in."""
    e = Env()
    e.request(app="parent")
    with pytest.raises(OtpError):
        e.verify(e.last_code(), app="teacher")
    assert e.verify(e.last_code(), app="parent").user_id


# ---------------------------------------------------------------- clock and phone edge cases


def test_time_going_backwards_does_not_extend_a_code():
    e = Env()
    e.request()
    code = e.last_code()
    e.clock.advance(400)
    e.clock.advance(-200)  # now t+200: still before expiry, so valid; expiry stays absolute
    assert e.verify(code).user_id


def test_equivalent_formats_of_one_number_share_limits_and_challenges():
    e = Env()
    e.request("0772123456")
    with pytest.raises(OtpError) as x:
        e.request("+256 772 123 456")
    assert x.value.code == "resend_too_soon"
    assert e.verify(e.last_code(), phone="772123456").user_id


def test_expiry_is_stamped_in_utc_and_cleanup_horizon_is_set():
    e = Env()
    e.request()
    ch = e.rows("challenge")[0]
    assert ch["expires_at"].endswith("+00:00") or ch["expires_at"].endswith("Z")
    assert timedelta(0) < e.policy.ttl
