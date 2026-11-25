"""Shared builders for the A01 OTP tests: fake clock, SMS, users and a ready service."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

from hs_core.auth_otp.accounts import FakeUserDirectory
from hs_core.auth_otp.policy import OtpPolicy
from hs_core.auth_otp.service import OtpService
from hs_core.auth_otp.sms import FakeSmsSender
from hs_core.repository import InMemoryRepository

T0 = datetime(2026, 11, 25, 9, 0, 0, tzinfo=UTC)
PEPPER = "p" * 40
PHONE = "+256772123456"
OTHER = "+256701234567"
IP = "203.0.113.7"


class Clock:
    def __init__(self, now: datetime = T0) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


class Env:
    """A fully faked OTP stack."""

    def __init__(self, codes=None, **policy) -> None:
        self.clock = Clock()
        self.repo = InMemoryRepository()
        self.sms = FakeSmsSender()
        self.users = FakeUserDirectory(clock=self.clock)
        self.policy = OtpPolicy(**policy)
        self.service = OtpService(
            repo=self.repo,
            sms=self.sms,
            users=self.users,
            pepper=PEPPER,
            policy=self.policy,
            clock=self.clock,
            **({"code_factory": iter(codes).__next__} if codes else {}),
        )

    def request(self, phone=PHONE, app="parent", ip=IP):
        return self.service.request_code(phone, app=app, client_ip=ip)

    def last_code(self) -> str:
        m = re.search(r"(?<!\d)(\d{6})(?!\d)", self.sms.sent[-1].body)
        assert m, self.sms.sent[-1].body
        return m.group(1)

    def wrong_code(self) -> str:
        real = self.last_code()
        return "000000" if real != "000000" else "111111"

    def verify(self, code, phone=PHONE, app="parent", ip=IP):
        return self.service.verify_code(phone, code, app=app, client_ip=ip, device={})

    def rows(self, kind=None):
        rows = self.repo.list("otp_challenge")
        return [r for r in rows if kind is None or r["kind"] == kind]
