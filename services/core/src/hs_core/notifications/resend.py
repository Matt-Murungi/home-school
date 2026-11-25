"""Resend email adapter (ADR 0005 `EmailSender`) and webhook verification/mapping.

UNVERIFIED UNTIL A REAL ACCOUNT IS USED. Written from Resend's public documentation, fetched
26 Nov 2026, and exercised only against mock transports and fixture payloads (no account, domain
or key exists: owner-blocked, see memory.md N02):
- send: https://resend.com/docs/api-reference/emails/send-email  (POST /emails, Bearer key,
  `Idempotency-Key` header: max 256 chars, keys expire after 24 hours, tags are
  `{name, value}` with ASCII letters, digits, `_` and `-`, max 256; response `{"id": ...}`)
- errors: https://resend.com/docs/api-reference/errors  (400 validation_error and
  invalid_idempotency_key, 409 invalid_idempotent_request and concurrent_idempotent_requests,
  429 rate_limit_exceeded and daily_quota_exceeded, 500 application_error)
- webhook events: https://resend.com/docs/webhooks/event-types  (email.sent, delivered, bounced,
  complained, failed, suppressed, delivery_delayed, opened, clicked, scheduled, received; retries
  reuse the same `svix-id`)
- bounce payload: https://resend.com/docs/webhooks/emails/bounced  (`data.email_id`, `data.to`,
  `data.tags` object, `data.bounce.type` Permanent|Temporary, `data.bounce.subType`)
- signatures (Svix): https://docs.svix.com/receiving/verifying-payloads/how-manual  (headers
  `svix-id`, `svix-timestamp`, `svix-signature`; secret `whsec_` + base64; signed content
  `id.timestamp.body`; HMAC-SHA256 base64; header is space separated `v1,<sig>` entries)
Not stated in the docs and therefore ASSUMED (verify on first real use): the exact shape of the
`email.failed`/`email.suppressed` payloads (we only need `data.email_id`, `data.to`, `data.tags`),
that webhook `data.tags` echoes the tags we send, a Retry-After header on 429, and what Resend
answers when the same idempotency key is reused with the same body (we assume the original 200).

Secrets (API key, webhook secret) are never logged and never in a repr. Redirects are refused.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import logging
import re
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any

import httpx

from .address import validate_address
from .channels import (
    EmailError,
    EmailEvent,
    EmailMessage,
    SendResult,
    WebhookRejected,
)
from .httpsafe import build_client, parse_retry_after

log = logging.getLogger("hs_core.notifications.resend")
API_BASE = "https://api.resend.com"
MAX_WEBHOOK_BODY = 256 * 1024
TOLERANCE_S = 300
MAX_SIGNATURES = 10
_ID = re.compile(r"[A-Za-z0-9_.:-]{1,128}")
_TS = re.compile(r"[0-9]{1,12}")
_TAG = re.compile(r"[^A-Za-z0-9_-]")
_HEADER_SAFE = re.compile(r"[\x21-\x7e]{1,512}")
_NAME_UNSAFE = re.compile(r'[<>"\\\x00-\x1f\x7f,;@]')


def _tag(value: object) -> str:
    return _TAG.sub("_", str(value))[:256] or "_"


class ResendEmailSender:
    def __init__(
        self,
        api_key: str,
        webhook_secret: str,
        from_address: str,
        reply_to: str | None = None,
        from_name: str = "Home School",
        base_url: str = API_BASE,
        client: httpx.Client | None = None,
        clock: Callable[[], datetime] | None = None,
        tolerance_s: int = TOLERANCE_S,
    ) -> None:
        if not _HEADER_SAFE.fullmatch(api_key or ""):
            raise ValueError("RESEND_API_KEY is empty or contains whitespace/control characters")
        if validate_address(from_address) is None:
            raise ValueError("EMAIL_FROM must be one plain email address")
        if reply_to and validate_address(reply_to) is None:
            raise ValueError("EMAIL_REPLY_TO must be one plain email address")
        self._key = api_key
        self._secret = decode_webhook_secret(webhook_secret)  # secret-scan: allow
        name = _NAME_UNSAFE.sub("", from_name).strip()[:60]
        self.from_address = from_address
        self._from = f"{name} <{from_address}>" if name else from_address
        self.reply_to = reply_to
        self._base = base_url.rstrip("/")
        self._client = client or build_client()
        self.clock = clock or (lambda: datetime.now(UTC))
        self.tolerance_s = tolerance_s

    def __repr__(self) -> str:  # never the key or the secret
        return f"ResendEmailSender(from={self.from_address!r}, base={self._base!r})"

    # ------------------------------------------------------------------------------ send
    def send(self, message: EmailMessage, *, idempotency_key: str) -> SendResult:
        if not 1 <= len(idempotency_key) <= 256 or not _HEADER_SAFE.fullmatch(idempotency_key):
            return SendResult(None, False, EmailError.REJECTED)
        body: dict[str, Any] = {
            "from": message.from_address or self._from,
            "to": [message.to],
            "subject": message.subject,
            "html": message.html,
            "text": message.text,
        }
        reply_to = message.reply_to or self.reply_to
        if reply_to:
            body["reply_to"] = [reply_to]
        if message.tags:
            body["tags"] = [{"name": _tag(k), "value": _tag(v)} for k, v in message.tags.items()]
        try:
            resp = self._client.post(
                f"{self._base}/emails",
                content=json.dumps(body).encode(),
                headers={
                    "Authorization": f"Bearer {self._key}",
                    "Content-Type": "application/json",
                    "Idempotency-Key": idempotency_key,
                    "User-Agent": "hs-core/n02",
                },
            )
        except httpx.HTTPError as e:  # timeouts, connection errors: message may hold the URL
            log.warning("resend transport error", extra={"error_type": type(e).__name__})
            return SendResult(None, False, EmailError.PROVIDER_DOWN)
        return self._interpret(resp)

    def _interpret(self, resp: httpx.Response) -> SendResult:
        status = resp.status_code
        if status == 200:
            try:
                mid = resp.json()["id"]
            except (ValueError, KeyError, TypeError):
                mid = None
            if isinstance(mid, str) and 0 < len(mid) <= 128:
                return SendResult(mid, True)
            # accepted-looking but unreadable: retry with the SAME key, Resend dedupes
            log.error("resend reply unreadable", extra={"status": status})
            return SendResult(None, False, EmailError.PROVIDER_DOWN)
        name = _error_name(resp)
        retry = parse_retry_after(resp.headers.get("retry-after"), self.clock())
        log.warning("resend refused a send", extra={"status": status, "error_name": name})
        if status == 429:
            if name == "daily_quota_exceeded":  # resets at midnight UTC: wait the cap
                return SendResult(None, False, EmailError.RATE_LIMITED, retry or 3600.0)
            return SendResult(None, False, EmailError.RATE_LIMITED, retry)
        if status == 409 and name == "concurrent_idempotent_requests":
            return SendResult(None, False, EmailError.PROVIDER_DOWN, retry)
        if status >= 500 or status in (408, 425):
            return SendResult(None, False, EmailError.PROVIDER_DOWN, retry)
        if 300 <= status < 400:  # redirects are never followed
            return SendResult(None, False, EmailError.REJECTED)
        if status in (400, 422) and name in ("invalid_to_address", "invalid_email"):
            return SendResult(None, False, EmailError.INVALID_ADDRESS)
        return SendResult(None, False, EmailError.REJECTED)

    # --------------------------------------------------------------------------- webhooks
    def event_id(self, headers: Mapping[str, str], body: bytes) -> str:
        h = {k.lower(): v for k, v in headers.items()}
        eid = h.get("svix-id", "")
        if not _ID.fullmatch(eid):
            raise WebhookRejected(401, "bad_id")
        return eid

    def parse_webhook(self, headers: Mapping[str, str], body: bytes) -> list[EmailEvent]:
        """Verify the Svix signature FIRST (401 on any failure), then map the event. Unknown event
        types give an empty list. A signed body that is not the expected JSON is a 400."""
        if len(body) > MAX_WEBHOOK_BODY:
            raise WebhookRejected(413, "too_large")
        ts = verify_signature(self._secret, headers, body, self.clock(), self.tolerance_s)
        try:
            doc = json.loads(body)
        except (ValueError, RecursionError):
            raise WebhookRejected(400, "not_json") from None
        if not isinstance(doc, dict) or not isinstance(doc.get("type"), str):
            raise WebhookRejected(400, "bad_shape")
        return map_event(doc, ts)


def _error_name(resp: httpx.Response) -> str:
    try:
        name = resp.json().get("name", "")
    except (ValueError, AttributeError):
        return ""
    return name[:64] if isinstance(name, str) else ""


def decode_webhook_secret(secret: str) -> bytes:
    if not isinstance(secret, str) or not secret.startswith("whsec_"):
        raise ValueError("RESEND_WEBHOOK_SECRET must look like whsec_<base64>")
    try:
        key = base64.b64decode(secret[len("whsec_") :], validate=True)
    except (binascii.Error, ValueError):
        raise ValueError("RESEND_WEBHOOK_SECRET is not valid base64") from None
    if len(key) < 16:
        raise ValueError("RESEND_WEBHOOK_SECRET is too short")
    return key


def verify_signature(
    key: bytes, headers: Mapping[str, str], body: bytes, now: datetime, tolerance_s: int
) -> int:
    """Svix verification. Returns the (verified) timestamp. Raises WebhookRejected(401, code)."""
    h = {k.lower(): v for k, v in headers.items() if isinstance(v, str)}
    mid, ts, sigs = h.get("svix-id", ""), h.get("svix-timestamp", ""), h.get("svix-signature", "")
    if not mid or not ts or not sigs:
        raise WebhookRejected(401, "missing_headers")
    if not _ID.fullmatch(mid) or not _TS.fullmatch(ts) or len(sigs) > 2048:
        raise WebhookRejected(401, "malformed_headers")
    stamp = int(ts)
    if abs(now.timestamp() - stamp) > tolerance_s:
        raise WebhookRejected(401, "timestamp_out_of_tolerance")  # stale or from the future
    signed = mid.encode() + b"." + ts.encode() + b"." + body
    expected = base64.b64encode(hmac.new(key, signed, hashlib.sha256).digest())
    ok = False
    for entry in sigs.split(" ")[:MAX_SIGNATURES]:
        version, _, value = entry.partition(",")
        if version == "v1" and hmac.compare_digest(value.encode(), expected):
            ok = True  # no early exit: constant work per entry
    if not ok:
        raise WebhookRejected(401, "bad_signature")
    return stamp


def _when(doc: dict, ts: int) -> datetime:
    raw = doc.get("created_at")
    if isinstance(raw, str):
        try:
            d = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            return d if d.tzinfo else d.replace(tzinfo=UTC)
        except ValueError:
            pass
    return datetime.fromtimestamp(ts, UTC)


def map_event(doc: dict, ts: int) -> list[EmailEvent]:
    """ADR 0005 mapping table. Unknown types are ignored (empty list), never an error."""
    typ = doc["type"]
    data = doc.get("data")
    if not isinstance(data, dict):
        return []
    mid = data.get("email_id")
    if not isinstance(mid, str) or not mid:
        return []
    kind, reason = None, ""
    if typ == "email.delivered":
        kind = "delivered"
    elif typ == "email.bounced":
        bounce = data.get("bounce") if isinstance(data.get("bounce"), dict) else {}
        # Permanent = hard. Temporary (and anything we do not recognise) = soft: never suppress
        # an address because of an event we cannot classify.
        kind = "bounced" if bounce.get("type") == "Permanent" else "bounced_soft"
        reason = str(bounce.get("subType") or bounce.get("type") or "")[:60]
    elif typ == "email.delivery_delayed":
        kind, reason = "bounced_soft", "delivery_delayed"
    elif typ == "email.complained":
        kind = "complained"
    elif typ == "email.failed":
        kind, reason = "failed", "provider_failed"
    elif typ == "email.suppressed":
        kind, reason = "suppressed", "provider_suppressed"
    if kind is None:
        return []
    tags = data.get("tags") if isinstance(data.get("tags"), dict) else {}
    nid = tags.get("notification_id")
    to = data.get("to")
    address = to[0] if isinstance(to, list) and to and isinstance(to[0], str) else None
    return [
        EmailEvent(
            mid,
            kind,  # type: ignore[arg-type]
            _when(doc, ts),
            reason,
            nid if isinstance(nid, str) and 0 < len(nid) <= 64 else None,
            address if address and validate_address(address) else None,
        )
    ]
