"""Channel interfaces (push, email, in-app) and scriptable fakes. The real adapters live in
`fcm.py` and `resend.py` (N02, #127) behind these same interfaces.

The email contract is the one from ADR 0005 (`EmailSender`: `send` with an idempotency key and
`parse_webhook`). `EmailChannel` adapts it to the common `ChannelResult`.

Failure kinds drive the service: TRANSIENT is retried with backoff, PERMANENT fails the row,
UNREGISTERED (push only) deletes the token and counts as permanent for that token.
"""

from __future__ import annotations

import hashlib
import json
import threading
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal, Protocol

from .address import validate_address


class FailureKind(StrEnum):
    TRANSIENT = "transient"
    PERMANENT = "permanent"
    UNREGISTERED = "unregistered"


@dataclass(frozen=True)
class ChannelResult:
    accepted: bool
    failure: FailureKind | None = None
    provider_message_id: str | None = None
    detail: str = ""  # a provider code, never message content or addresses
    retry_after_s: float | None = None  # provider back-pressure hint, already clamped (httpsafe)


OK = ChannelResult(True)


# ------------------------------------------------------------------------------------- push
@dataclass(frozen=True)
class PushMessage:
    token: str
    title: str
    body: str
    data: dict[str, str]
    idempotency_key: str
    priority: str = "operational"  # critical | operational | marketing (N01 priority)
    ttl_s: int | None = None  # how long the provider may hold it for an offline device
    collapse_key: str | None = None  # newer message with the same key replaces the older
    channel: str | None = None  # Android notification channel id (apps create these)

    def __repr__(self) -> str:  # tokens are secrets: never reach a log through repr
        return f"PushMessage(key={self.idempotency_key})"


class PushChannel(Protocol):
    def send(self, message: PushMessage) -> ChannelResult: ...


def send_each(push: PushChannel, messages: list[PushMessage]) -> list[ChannelResult]:
    """One result per message, in order. Uses the channel's own `send_each` (batching, shared
    back-pressure) when it has one. A single message raising an `Exception` becomes a transient
    failure carrying the exception TYPE NAME only; it never stops the others."""
    batch = getattr(push, "send_each", None)
    if batch is not None:
        return batch(messages)
    out: list[ChannelResult] = []
    for m in messages:
        try:
            out.append(push.send(m))
        except Exception as e:  # noqa: BLE001 - one device must not stop the others
            out.append(
                ChannelResult(False, FailureKind.TRANSIENT, detail=f"exception:{type(e).__name__}")
            )
    return out


# ------------------------------------------------------------------------------------ email
class EmailError(StrEnum):
    RATE_LIMITED = "rate_limited"
    INVALID_ADDRESS = "invalid_address"
    PROVIDER_DOWN = "provider_down"
    REJECTED = "rejected"


@dataclass(frozen=True)
class EmailMessage:
    to: str
    subject: str
    html: str
    text: str
    from_address: str = ""
    reply_to: str | None = None
    tags: Mapping[str, str] = field(default_factory=dict)  # template name, notification id

    def __repr__(self) -> str:
        return f"EmailMessage(tags={dict(self.tags)})"


@dataclass(frozen=True)
class SendResult:
    provider_message_id: str | None
    accepted: bool
    error: EmailError | None = None
    retry_after_s: float | None = None


EmailEventKind = Literal[
    "delivered", "bounced", "bounced_soft", "complained", "failed", "suppressed"
]


@dataclass(frozen=True)
class EmailEvent:
    provider_message_id: str
    kind: EmailEventKind
    occurred_at: datetime
    reason: str = ""
    notification_id: str | None = None  # our row id, echoed back by the provider (email tag)
    address: str | None = None  # recipient, for the suppression list ONLY: never logged

    def __repr__(self) -> str:
        return f"EmailEvent(kind={self.kind}, notification_id={self.notification_id})"


class EmailSender(Protocol):
    """ADR 0005 contract (N02 builds the Resend adapter)."""

    def send(self, message: EmailMessage, *, idempotency_key: str) -> SendResult: ...

    def parse_webhook(self, headers: Mapping[str, str], body: bytes) -> list[EmailEvent]: ...

    def event_id(self, headers: Mapping[str, str], body: bytes) -> str:
        """Stable id of one webhook DELIVERY (the provider reuses it on retries): replay guard."""
        ...


class WebhookRejected(ValueError):  # noqa: N818 - a verdict, not a "...Error"
    """A webhook was not accepted. `status` is the HTTP answer (401 signature or timestamp,
    400 malformed signed payload); `code` is a short reason for logs. Never carries the body."""

    def __init__(self, status: int, code: str) -> None:
        super().__init__(code)
        self.status, self.code = status, code


class AddressSuppression(Protocol):
    def is_suppressed(self, address: str) -> bool: ...


_EMAIL_FAILURE = {
    EmailError.RATE_LIMITED: FailureKind.TRANSIENT,
    EmailError.PROVIDER_DOWN: FailureKind.TRANSIENT,
    EmailError.INVALID_ADDRESS: FailureKind.PERMANENT,
    EmailError.REJECTED: FailureKind.PERMANENT,
}


class EmailChannel:
    """Adapts an ADR 0005 `EmailSender` to the common channel result."""

    def __init__(
        self,
        sender: EmailSender,
        from_address: str = "",
        reply_to: str | None = None,
        suppression: AddressSuppression | None = None,
    ):
        self.sender, self.from_address, self.reply_to = sender, from_address, reply_to
        self.suppression = suppression

    def send(
        self, to: str, subject: str, text: str, html: str, tags: Mapping[str, str], key: str
    ) -> ChannelResult:
        if validate_address(to) is None:  # one clean address: no CR/LF, lists, names, odd chars
            return ChannelResult(False, FailureKind.PERMANENT, detail="invalid_address")
        if self.suppression is not None and self.suppression.is_suppressed(to):
            # hard bounce / complaint / provider suppression: never emailed again (all mail)
            return ChannelResult(False, FailureKind.PERMANENT, detail="suppressed")
        msg = EmailMessage(to, subject, html, text, self.from_address, self.reply_to, dict(tags))
        res = self.sender.send(msg, idempotency_key=key)
        if res.accepted:
            return ChannelResult(True, provider_message_id=res.provider_message_id)
        kind = _EMAIL_FAILURE.get(res.error or EmailError.REJECTED, FailureKind.PERMANENT)
        return ChannelResult(
            False,
            kind,
            detail=(res.error or EmailError.REJECTED).value,
            retry_after_s=res.retry_after_s,
        )


# ------------------------------------------------------------------------------------ in-app
@dataclass(frozen=True)
class InAppMessage:
    user_id: str
    title: str
    body: str
    deep_link: str | None
    idempotency_key: str


class InAppChannel(Protocol):
    def send(self, message: InAppMessage) -> ChannelResult: ...


class StoredInAppChannel:
    """The `notification` row itself is the in-app message (the app lists its own rows), so
    delivery is complete once the row exists."""

    def send(self, message: InAppMessage) -> ChannelResult:
        return OK


# ---------------------------------------------------------------------------------- unconfigured
class UnconfiguredChannel:
    """Used until N02 provides real adapters: every send fails permanently, loudly."""

    def send(self, *args: Any, **kwargs: Any) -> ChannelResult:
        return ChannelResult(False, FailureKind.PERMANENT, detail="not_configured")


class UnconfiguredEmailSender:
    """Placeholder `EmailSender` until N02: rejects every send, parses nothing."""

    def send(self, message: EmailMessage, *, idempotency_key: str) -> SendResult:
        return SendResult(None, False, EmailError.REJECTED)

    def parse_webhook(self, headers: Mapping[str, str], body: bytes) -> list[EmailEvent]:
        raise NotImplementedError("email provider not configured: webhooks are refused")

    def event_id(self, headers: Mapping[str, str], body: bytes) -> str:
        raise NotImplementedError("email provider not configured")


# ------------------------------------------------------------------------------------- fakes
class SimulatedCrash(BaseException):
    """Worker death injected by tests (BaseException: nothing catches it by accident)."""


class _Scripted:
    """Outcomes consumed in order: 'ok', 'transient', 'permanent', 'unregistered' (and for email
    the ADR error names). `always(x)` sets the default outcome."""

    def __init__(self) -> None:
        self._script: deque[str] = deque()
        self._default = "ok"
        self._lock = threading.RLock()
        self.crash_after_accept = False
        self.crash_before_accept = False

    def script(self, *outcomes: str) -> None:
        with self._lock:
            self._script.extend(outcomes)

    def always(self, outcome: str) -> None:
        self._default = outcome

    def _next(self) -> str:
        with self._lock:
            return self._script.popleft() if self._script else self._default


class FakePushChannel(_Scripted):
    def __init__(self) -> None:
        super().__init__()
        self.messages: list[PushMessage] = []
        self.accepted_keys: dict[str, int] = {}  # provider-side dedupe by idempotency key
        self.unregistered_tokens: set[str] = set()
        self.invalid_tokens: set[str] = set()

    @property
    def calls_count(self) -> int:
        return len(self.messages)

    @property
    def deliveries(self) -> int:
        """Distinct messages the 'provider' actually delivered (it dedupes by key)."""
        return len(self.accepted_keys)

    def send(self, message: PushMessage) -> ChannelResult:
        with self._lock:
            self.messages.append(message)
            if self.crash_before_accept:
                raise SimulatedCrash
            if message.token in self.unregistered_tokens:
                return ChannelResult(False, FailureKind.UNREGISTERED, detail="unregistered")
            if message.token in self.invalid_tokens:
                return ChannelResult(False, FailureKind.PERMANENT, detail="invalid_token")
            outcome = self._next()
            if outcome == "ok":
                self.accepted_keys.setdefault(message.idempotency_key, 1)
                if self.crash_after_accept:
                    raise SimulatedCrash
                return ChannelResult(True, provider_message_id=f"push-{len(self.accepted_keys)}")
            return ChannelResult(False, FailureKind(outcome), detail=outcome)


class FakeEmailSender(_Scripted):
    """Implements the ADR 0005 EmailSender contract. `script("provider_down")` etc. take the
    ADR error names; 'ok' accepts."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[EmailMessage] = []
        self.sent: list[EmailMessage] = []  # accepted calls (a retried key appears twice)
        self._by_key: dict[str, str] = {}

    @property
    def sent_unique(self) -> list[str]:
        return list(self._by_key)

    def send(self, message: EmailMessage, *, idempotency_key: str) -> SendResult:
        with self._lock:
            self.calls.append(message)
            if self.crash_before_accept:
                raise SimulatedCrash
            outcome = self._next()
            if outcome != "ok":
                return SendResult(None, False, EmailError(outcome))
            if idempotency_key not in self._by_key:
                self._by_key[idempotency_key] = f"email-{len(self._by_key) + 1}"
                self.sent.append(message)
            if self.crash_after_accept:
                raise SimulatedCrash
            return SendResult(self._by_key[idempotency_key], True)

    def event_id(self, headers: Mapping[str, str], body: bytes) -> str:
        return headers.get("x-fake-event-id") or hashlib.sha256(body).hexdigest()

    def parse_webhook(self, headers: Mapping[str, str], body: bytes) -> list[EmailEvent]:
        if headers.get("x-fake-signature") != "ok":
            raise ValueError("bad signature")
        out = []
        for e in json.loads(body):
            out.append(
                EmailEvent(
                    e["id"],
                    e["kind"],
                    datetime.fromisoformat(e["at"]),
                    e.get("reason", ""),
                    e.get("notification_id"),
                    e.get("address"),
                )
            )
        return out


class FakeInAppChannel(_Scripted):
    def __init__(self) -> None:
        super().__init__()
        self.messages: list[InAppMessage] = []

    def send(self, message: InAppMessage) -> ChannelResult:
        with self._lock:
            self.messages.append(message)
            outcome = self._next()
            if outcome == "ok":
                return OK
            return ChannelResult(False, FailureKind(outcome), detail=outcome)
