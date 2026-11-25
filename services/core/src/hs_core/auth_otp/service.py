"""Phone OTP: request a code, verify it, hand back an Appwrite custom token (A01, ACC-1, SEC-7).

Security properties (each has a test in tests/test_otp_service.py):
- the code is 6 digits from a CSPRNG, stored only as HMAC(pepper, challenge|salt|code);
- single use: redemption claims a deterministic row, so of N concurrent verifies one wins;
- at most `max_attempts` tries per code (slot rows), plus per-number and per-IP caps, so guessing
  across many codes is bounded too;
- the response to a request never depends on whether the number has an account;
- the hash comparison runs exactly once on every verify, challenge or not;
- nothing here logs a code or a full phone number (masked number only);
- an SMS provider outage rolls the request back: no cooldown, no quota, no attempts are burnt.
"""

from __future__ import annotations

import logging
import math
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from ..errors import AppError
from ..repository import Repository, RepositoryError
from .accounts import (
    SELF_SERVICE_ROLES,
    AccountBlocked,
    AccountConflict,
    AccountService,
    AccountUnavailable,
    UserDirectory,
)
from .crypto import generate_code, hash_code, hash_identifier, hashes_equal, new_salt
from .phone import InvalidPhone, mask_phone, normalize_ug_phone
from .policy import OtpPolicy
from .sms import SmsRejected, SmsSender, render_otp_message
from .store import Challenge, OtpStore, Slot

log = logging.getLogger("hs_core.auth_otp")
_DUMMY = "0" * 64


class OtpError(AppError):
    """Every user-visible failure. `extra` goes into the error body (never a secret)."""

    def __init__(self, code: str, message: str, status: int, **extra) -> None:
        headers = None
        if "retry_after_s" in extra:
            headers = {"Retry-After": str(extra["retry_after_s"])}
        super().__init__(status, code, message, headers, extra)


@dataclass(frozen=True)
class RequestResult:
    resend_after_s: int
    expires_in_s: int


@dataclass(frozen=True)
class VerifyResult:
    user_id: str
    secret: str
    expire: datetime
    is_new_user: bool
    dormant: bool


def _unavailable() -> OtpError:
    return OtpError("service_unavailable", "Sign-in is temporarily unavailable. Try again.", 503)


class OtpService:
    def __init__(
        self,
        *,
        repo: Repository,
        sms: SmsSender,
        users: UserDirectory,
        pepper: str,
        policy: OtpPolicy | None = None,
        clock: Callable[[], datetime] | None = None,
        code_factory: Callable[[], str] = generate_code,
    ) -> None:
        self.policy = policy or OtpPolicy()
        self.clock = clock or (lambda: datetime.now(UTC))
        self.sms = sms
        self.store = OtpStore(repo)
        self.accounts = AccountService(
            users, repo, self.clock, self.policy.dormant_days, self.policy.token_ttl_s
        )
        self._pepper = pepper.encode()
        self._code_factory = code_factory

    # -- helpers -----------------------------------------------------------------------------
    def _prepare(self, phone_raw: str, app: str, ip: str) -> tuple[str, str, str, str]:
        if app not in SELF_SERVICE_ROLES:
            raise OtpError("invalid_app", "Unknown app", 422)
        try:
            phone = normalize_ug_phone(phone_raw)
        except InvalidPhone:
            raise OtpError("invalid_phone", "Enter a valid Ugandan mobile number", 422) from None
        return (
            phone,
            hash_identifier(self._pepper, "phone", phone),
            hash_identifier(self._pepper, "ip", ip or "unknown"),
            mask_phone(phone),
        )

    def _window(self, now: datetime, seconds: int) -> tuple[int, int]:
        """(window index, seconds until that window ends)."""
        t = int(now.timestamp())
        return t // seconds, seconds - t % seconds

    def _claim(self, bucket: str, limit: int, seconds: int, now: datetime, claimed: list[Slot]):
        idx, left = self._window(now, seconds)
        slot = self.store.claim_slot(f"{bucket}:{idx}", limit, now, now + timedelta(seconds=left))
        if slot is None:
            self.store.release(claimed)
            raise OtpError(
                "rate_limited",
                "Too many codes requested. Try again later.",
                429,
                retry_after_s=left,
            )
        claimed.append(slot)

    # -- request -----------------------------------------------------------------------------
    def request_code(self, phone_raw: str, *, app: str, client_ip: str) -> RequestResult:
        try:
            return self._request(phone_raw, app, client_ip)
        except RepositoryError:
            log.warning("otp request failed: storage unavailable")
            raise _unavailable() from None

    def _request(self, phone_raw: str, app: str, ip: str) -> RequestResult:
        p = self.policy
        phone, ph, iph, masked = self._prepare(phone_raw, app, ip)
        now = self.clock()
        latest = self.store.latest_challenge(ph)
        if latest is not None:
            wait = latest.created_at + timedelta(seconds=p.resend_cooldown_s) - now
            if wait > timedelta(0):
                raise OtpError(
                    "resend_too_soon",
                    "Please wait before asking for another code.",
                    429,
                    retry_after_s=max(1, math.ceil(wait.total_seconds())),
                )
        claimed: list[Slot] = []
        self._claim(f"req:ph:h:{ph}", p.phone_requests_per_hour, 3600, now, claimed)
        self._claim(f"req:ph:d:{ph}", p.phone_requests_per_day, 86400, now, claimed)
        self._claim(f"req:ip:h:{iph}", p.ip_requests_per_hour, 3600, now, claimed)
        self._claim(f"req:ip:d:{iph}", p.ip_requests_per_day, 86400, now, claimed)
        challenge: Challenge | None = None
        try:
            code = self._code_factory()
            salt = new_salt()
            cid = secrets.token_hex(16)
            challenge = self.store.create_challenge(
                challenge_id=cid,
                phone_hash=ph,
                code_hash=hash_code(self._pepper, cid, salt, code),
                salt=salt,
                app=app,
                now=now,
                expires_at=now + p.ttl,
            )
            body = render_otp_message(
                code=code, ttl_minutes=max(1, p.ttl_s // 60), app_hash=p.app_hash
            )
            self.sms.send(phone, body)
        except SmsRejected:
            self._rollback(challenge, claimed)
            log.info("otp number rejected by the sms provider", extra={"phone": masked})
            raise OtpError(
                "number_unreachable",
                "We can't send an SMS to this number. Use a different number.",
                422,
            ) from None
        except Exception as e:  # noqa: BLE001 - provider or storage trouble: undo, retriable
            self._rollback(challenge, claimed)
            log.warning("otp send failed", extra={"phone": masked, "error_type": type(e).__name__})
            if isinstance(e, RepositoryError):
                raise _unavailable() from None
            raise OtpError(
                "sms_unavailable",
                "We couldn't send the SMS right now. Try again in a moment.",
                503,
            ) from None
        log.info("otp sent", extra={"phone": masked, "app": app})
        return RequestResult(p.resend_cooldown_s, p.ttl_s)

    def _rollback(self, challenge: Challenge | None, claimed: list[Slot]) -> None:
        try:
            if challenge is not None:
                self.store.delete_challenge(challenge.id)
        except Exception:  # noqa: BLE001
            log.warning("could not remove a challenge after a failed send")
        self.store.release(claimed)

    # -- verify ------------------------------------------------------------------------------
    def verify_code(
        self, phone_raw: str, code: str, *, app: str, client_ip: str, device: dict | None = None
    ) -> VerifyResult:
        try:
            return self._verify(phone_raw, code, app, client_ip, device or {})
        except RepositoryError:
            log.warning("otp verify failed: storage unavailable")
            raise _unavailable() from None

    def _wrong(self, ph: str, now: datetime, slot_n: int) -> OtpError:
        """Record a failure against the per-number cap and build the wrong-code error."""
        idx, left = self._window(now, 3600)
        self.store.claim_slot(
            f"fail:ph:{ph}:{idx}",
            self.policy.phone_failures_per_hour,
            now,
            now + timedelta(seconds=left),
        )
        return OtpError(
            "invalid_code",
            "That code is not right.",
            400,
            attempts_left=max(0, self.policy.max_attempts - slot_n),
        )

    def _verify(self, phone_raw: str, code: str, app: str, ip: str, device: dict) -> VerifyResult:
        p = self.policy
        phone, ph, iph, masked = self._prepare(phone_raw, app, ip)
        now = self.clock()
        # Per-IP cap on verify calls, then the per-number lockout (SEC-7).
        idx, left = self._window(now, 3600)
        if (
            self.store.claim_slot(
                f"ver:ip:{iph}:{idx}", p.ip_verifies_per_hour, now, now + timedelta(seconds=left)
            )
            is None
        ):
            raise OtpError(
                "rate_limited", "Too many attempts. Try again later.", 429, retry_after_s=left
            )
        if self.store.used(f"fail:ph:{ph}:{idx}", p.phone_failures_per_hour) >= (
            p.phone_failures_per_hour
        ):
            log.warning("otp locked out", extra={"phone": masked})
            raise OtpError(
                "otp_locked",
                "Too many wrong codes. Try again later.",
                429,
                retry_after_s=left,
            )
        challenge = self.store.latest_challenge(ph)
        # One hash and one constant-time comparison on every path, challenge or not.
        supplied = hash_code(
            self._pepper,
            challenge.id if challenge else "none",
            challenge.salt if challenge else "none",
            code if isinstance(code, str) else "",
        )
        match = hashes_equal(challenge.code_hash if challenge else _DUMMY, supplied)
        well_formed = isinstance(code, str) and len(code) == 6 and code.isascii() and code.isdigit()
        if challenge is None:
            raise self._wrong(ph, now, 1)
        if now >= challenge.expires_at:
            raise OtpError("code_expired", "That code has expired. Request a new one.", 400)
        slot = self.store.claim_slot(
            f"att:{challenge.id}", p.max_attempts, now, challenge.expires_at, kind="attempt"
        )
        if slot is None:
            raise OtpError("too_many_attempts", "Too many wrong codes. Request a new code.", 429)
        if not (match and well_formed) or challenge.app != app:
            raise self._wrong(ph, now, slot.n)
        if not self.store.claim_consume(challenge, now):
            # Lost the race (or replay): same answer as a wrong code.
            raise OtpError("invalid_code", "That code is not right.", 400, attempts_left=0)
        try:
            result = self.accounts.sign_in(phone, app)
        except AccountUnavailable:
            self.store.release_consume(challenge)  # our outage must not burn the user's code
            raise OtpError(
                "auth_unavailable", "Sign-in is temporarily unavailable. Try again.", 503
            ) from None
        except AccountBlocked:
            raise OtpError(
                "account_blocked", "This account cannot sign in. Contact support.", 403
            ) from None
        except AccountConflict:
            log.error(
                "otp sign-in conflict: phone belongs to another profile", extra={"phone": masked}
            )
            raise OtpError(
                "account_conflict", "We couldn't sign you in. Contact support.", 409
            ) from None
        log.info(
            "otp verified",
            extra={
                "phone": masked,
                "app": app,
                "new_user": result.is_new_user,
                "device": device.get("platform"),
            },
        )
        return VerifyResult(
            result.user_id,
            result.token.secret,
            result.token.expire,
            result.is_new_user,
            result.dormant,
        )
