"""SMS sending behind an interface (ADR 0005 pattern): fake for tests, Africa's Talking adapter.

The Africa's Talking adapter is written against the public Bulk SMS API and has only ever been
exercised with a mock transport: no account or key exists yet. Endpoints, headers, field names and
status codes below are **unverified until a real account is used** (owner-blocked, see memory.md).
The message body lives in `sms_otp.en.txt`, not in code.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import httpx

log = logging.getLogger("hs_core.auth_otp.sms")

APP_NAME = "Home School"
TEMPLATE_FILE = Path(__file__).with_name("sms_otp.en.txt")

# UNVERIFIED until a real account is used: documented public endpoints of the Bulk SMS API.
AT_LIVE_URL = "https://api.africastalking.com/version1/messaging"
AT_SANDBOX_URL = "https://api.sandbox.africastalking.com/version1/messaging"
SANDBOX_USERNAME = "sandbox"
# UNVERIFIED: statusCode of a recipient. 100 Processed, 101 Sent, 102 Queued are accepted.
_AT_OK = {100, 101, 102}
# 403 InvalidPhoneNumber, 404 UnsupportedNumberType, 406 UserInBlacklist: the number is the problem.
_AT_BAD_NUMBER = {403, 404, 406}
# Everything else (401 RiskHold, 402 InvalidSenderId, 405 InsufficientBalance, 407 CouldNotRoute,
# 500/501/502 gateway errors) is ours or the provider's problem: retriable for the user.


class SmsError(Exception):
    """Base class. Messages never contain the number, the code or any credential."""


class SmsUnavailable(SmsError):  # noqa: N818 - a state, not a "...Error"
    """Provider outage or our configuration: the user may simply try again."""


class SmsRejected(SmsError):
    """The provider refuses this number (not a mobile, blacklisted): another number is needed."""


class SmsSender(Protocol):
    def send(self, to: str, body: str) -> None:
        """Send `body` to the E.164 number `to`; raise SmsUnavailable or SmsRejected."""
        ...


def render_otp_message(*, code: str, ttl_minutes: int, app_hash: str) -> str:
    """Body from the template file. With an `app_hash` the message follows the Android SMS
    Retriever format (`<#>` prefix, hash on the last line) so the app can auto-fill the code;
    the hash is a placeholder the owner replaces once the signing key exists."""
    text = (
        TEMPLATE_FILE.read_text(encoding="utf-8")
        .strip()
        .format(app_name=APP_NAME, code=code, minutes=ttl_minutes)
    )
    if app_hash:
        return f"<#> {text}\n{app_hash}"
    return text


@dataclass
class SentSms:
    to: str
    body: str


@dataclass
class FakeSmsSender:
    sent: list[SentSms] = field(default_factory=list)
    _failures: list[SmsError] = field(default_factory=list)

    def fail_next(self, error: SmsError) -> None:
        self._failures.append(error)

    def send(self, to: str, body: str) -> None:
        if self._failures:
            raise self._failures.pop(0)
        self.sent.append(SentSms(to, body))


class UnconfiguredSmsSender:
    """Used when no provider credentials are set: every send is a retriable outage."""

    def send(self, to: str, body: str) -> None:
        raise SmsUnavailable("SMS provider is not configured")


class AfricasTalkingSender:
    def __init__(
        self,
        api_key: str,
        username: str,
        sender_id: str = "",
        client: httpx.Client | None = None,
    ) -> None:
        self._api_key = api_key
        self._username = username
        self._sender_id = sender_id
        self._url = AT_SANDBOX_URL if username == SANDBOX_USERNAME else AT_LIVE_URL
        self._client = client or httpx.Client(
            timeout=httpx.Timeout(connect=3.05, read=8.0, write=5.0, pool=3.05)
        )

    def __repr__(self) -> str:
        return f"AfricasTalkingSender(username={self._username!r}, url={self._url!r})"

    def send(self, to: str, body: str) -> None:
        form = {"username": self._username, "to": to, "message": body}
        if self._sender_id:
            form["from"] = self._sender_id
        try:
            resp = self._client.post(
                self._url,
                data=form,
                headers={"apiKey": self._api_key, "Accept": "application/json"},
            )
        except httpx.HTTPError as e:
            raise SmsUnavailable(f"transport error: {type(e).__name__}") from None
        if resp.status_code not in (200, 201):
            raise SmsUnavailable(f"provider HTTP {resp.status_code}")
        try:
            recipients = resp.json()["SMSMessageData"]["Recipients"]
            status = int(recipients[0]["statusCode"])
        except (ValueError, KeyError, IndexError, TypeError):
            raise SmsUnavailable("unreadable provider reply") from None
        if status in _AT_OK:
            return
        if status in _AT_BAD_NUMBER:
            raise SmsRejected(f"provider status {status}")
        log.error("sms provider refused a message", extra={"provider_status": status})
        raise SmsUnavailable(f"provider status {status}")
