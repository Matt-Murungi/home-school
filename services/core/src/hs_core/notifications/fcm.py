"""FCM HTTP v1 push adapter (Android directly, iOS through APNs via FCM).

UNVERIFIED UNTIL A REAL ACCOUNT IS USED. Written from public documentation (fetched 26 Nov 2026),
exercised only against mock transports and fixture payloads; no Firebase project, service account
or APNs key exists (owner-blocked, see memory.md N02):
- send: https://firebase.google.com/docs/reference/fcm/rest/v1/projects.messages/send
  (POST https://fcm.googleapis.com/v1/projects/{id}/messages:send, body {"message": {...}})
- errors: https://firebase.google.com/docs/cloud-messaging/error-codes  (UNREGISTERED 404,
  INVALID_ARGUMENT 400, SENDER_ID_MISMATCH 403, QUOTA_EXCEEDED 429, UNAVAILABLE 503, INTERNAL 500,
  THIRD_PARTY_AUTH_ERROR 401; honour Retry-After on UNAVAILABLE)
- OAuth2 for a service account without a client library:
  https://developers.google.com/identity/protocols/oauth2/service-account  (RS256 JWT with iss,
  scope, aud=https://oauth2.googleapis.com/token, iat, exp<=1h; POST grant_type=
  urn:ietf:params:oauth:grant-type:jwt-bearer; reply access_token, expires_in)
- batching: https://firebase.google.com/docs/cloud-messaging/send/admin-sdk  (Admin SDKs send up
  to 500 messages per `sendEach`; HTTP v1 itself takes ONE message per request, so a "multicast"
  is one request per token)
ASSUMED from memory of the FCM v1 message reference (verify on first real use): android.priority
HIGH|NORMAL, android.ttl "Ns", android.collapse_key, android.notification.channel_id,
apns.headers `apns-priority` (10 immediate / 5 power-aware), `apns-expiration` (unix seconds),
`apns-collapse-id`, a 4096 byte payload limit, reserved data keys (`from`, `notification`,
anything starting `google` or `gcm`), and error `details[].errorCode` (FcmError).

Delivery semantics: at-least-once. FCM v1 has no idempotency key; the provider key in
`PushMessage.idempotency_key` is NOT sent. A worker that dies after FCM accepted a message and
before the row is marked sent causes ONE duplicate per device on the retry
(docs/ops/notifications.md).

Message design: a `notification` block (the OS shows it even when the app is closed) plus a `data`
block (`notification_id`, `deep_link`) for the app to route a tap. Critical = HIGH priority (APNs
10); everything else NORMAL (APNs 5).

Secrets: the service account JSON and the access token are never logged and never in a repr. The
token endpoint is pinned (the JSON's own `token_uri` is ignored). Redirects are refused.
"""

from __future__ import annotations

import base64
import json
import logging
import re
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from .channels import ChannelResult, FailureKind, PushMessage
from .httpsafe import build_client, parse_retry_after

log = logging.getLogger("hs_core.notifications.fcm")
FCM_BASE = "https://fcm.googleapis.com"
TOKEN_URL = "https://oauth2.googleapis.com/token"
SCOPE = "https://www.googleapis.com/auth/firebase.messaging"
MAX_BATCH = 500
MAX_PAYLOAD_BYTES = 4096
MAX_TTL_S = 28 * 24 * 3600
DEFAULT_TTL_S = {"critical": 24 * 3600, "operational": 3600, "marketing": 3600}
_DATA_KEY = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,63}\Z")
_RESERVED = ("from", "notification", "message_type")
_CHANNEL = re.compile(r"hs_[a-z]{1,20}\Z")
_COLLAPSE = re.compile(r"[A-Za-z0-9_.:-]{1,64}\Z")
_PROJECT = re.compile(r"[a-z][a-z0-9-]{4,28}[a-z0-9]\Z")
_EMAIL = re.compile(r"[A-Za-z0-9._+-]{1,128}@[A-Za-z0-9.-]{1,128}\Z")


class ServiceAccount:
    def __init__(
        self, client_email: str, private_key: rsa.RSAPrivateKey, project_id: str, kid: str
    ):
        self.client_email, self.project_id, self.kid = client_email, project_id, kid
        self._key = private_key

    def __repr__(self) -> str:
        return f"ServiceAccount(project={self.project_id!r})"

    def sign(self, data: bytes) -> bytes:
        return self._key.sign(data, padding.PKCS1v15(), hashes.SHA256())


def parse_service_account(raw: str, project_id: str = "") -> ServiceAccount:
    """Parse the Google service account JSON. Error messages never contain values."""
    try:
        doc = json.loads(raw)
    except (ValueError, TypeError):
        raise ValueError("FCM_SERVICE_ACCOUNT_JSON is not valid JSON") from None
    if not isinstance(doc, dict) or doc.get("type") != "service_account":
        raise ValueError("FCM_SERVICE_ACCOUNT_JSON is not a service account key")
    email, pem = doc.get("client_email"), doc.get("private_key")
    if not isinstance(email, str) or not _EMAIL.match(email):
        raise ValueError("FCM_SERVICE_ACCOUNT_JSON has no valid client_email")
    if not isinstance(pem, str):
        raise ValueError("FCM_SERVICE_ACCOUNT_JSON has no private_key")
    try:
        key = serialization.load_pem_private_key(pem.encode(), password=None)
    except (ValueError, TypeError):
        raise ValueError("FCM_SERVICE_ACCOUNT_JSON private_key is not a valid PEM key") from None
    if not isinstance(key, rsa.RSAPrivateKey) or key.key_size < 2048:
        raise ValueError("FCM_SERVICE_ACCOUNT_JSON private_key must be RSA of at least 2048 bits")
    pid = project_id or doc.get("project_id") or ""
    if not isinstance(pid, str) or not _PROJECT.match(pid):
        raise ValueError("FCM project id is missing or invalid (FCM_PROJECT_ID)")
    kid = doc.get("private_key_id")
    return ServiceAccount(email, key, pid, kid if isinstance(kid, str) else "")


def _b64(data: bytes) -> bytes:
    return base64.urlsafe_b64encode(data).rstrip(b"=")


class _AuthFailure(Exception):
    def __init__(self, failure: FailureKind, detail: str, retry_after: float | None = None):
        super().__init__(detail)
        self.failure, self.detail, self.retry_after = failure, detail, retry_after


class FcmPushChannel:
    def __init__(
        self,
        account: ServiceAccount,
        base_url: str = FCM_BASE,
        token_url: str = TOKEN_URL,
        client: httpx.Client | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._account = account
        self._base = base_url.rstrip("/")
        self._token_url = token_url
        self._client = client or build_client()
        self.clock = clock or (lambda: datetime.now(UTC))
        self._lock = threading.Lock()
        self._token: str | None = None
        self._token_expires: float = 0.0

    def __repr__(self) -> str:
        return f"FcmPushChannel(project={self._account.project_id!r})"

    # ------------------------------------------------------------------------------ auth
    def _access_token(self, force: bool = False) -> str:
        with self._lock:
            now = self.clock().timestamp()
            if not force and self._token and now < self._token_expires - 300:
                return self._token
            self._token = None
            iat = int(now)
            header = {"alg": "RS256", "typ": "JWT"}
            if self._account.kid:
                header["kid"] = self._account.kid
            claims = {
                "iss": self._account.client_email,
                "scope": SCOPE,
                "aud": TOKEN_URL,
                "iat": iat,
                "exp": iat + 3600,
            }
            signing_input = (
                _b64(json.dumps(header, separators=(",", ":")).encode())
                + b"."
                + _b64(json.dumps(claims, separators=(",", ":")).encode())
            )
            jwt = signing_input + b"." + _b64(self._account.sign(signing_input))
            try:
                resp = self._client.post(
                    self._token_url,
                    data={
                        "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                        "assertion": jwt.decode(),
                    },
                )
            except httpx.HTTPError as e:
                log.warning("fcm token transport error", extra={"error_type": type(e).__name__})
                raise _AuthFailure(FailureKind.TRANSIENT, "auth_transport") from None
            if resp.status_code != 200:
                retry = parse_retry_after(resp.headers.get("retry-after"), self.clock())
                log.warning("fcm token refused", extra={"status": resp.status_code})
                if resp.status_code >= 500 or resp.status_code in (408, 429):
                    raise _AuthFailure(FailureKind.TRANSIENT, "auth_unavailable", retry)
                raise _AuthFailure(FailureKind.PERMANENT, "auth_config")  # bad key: needs a human
            try:
                doc = resp.json()
                token, ttl = doc["access_token"], float(doc.get("expires_in", 3600))
            except (ValueError, KeyError, TypeError, AttributeError):
                raise _AuthFailure(FailureKind.TRANSIENT, "auth_unreadable") from None
            if not isinstance(token, str) or not token.strip() or ttl != ttl:
                raise _AuthFailure(FailureKind.TRANSIENT, "auth_unreadable")
            self._token = token
            self._token_expires = now + min(max(ttl, 60.0), 3600.0)
            return token

    # ---------------------------------------------------------------------------- payload
    def build_payload(self, m: PushMessage) -> dict[str, Any]:
        priority = m.priority if m.priority in DEFAULT_TTL_S else "operational"
        high = priority == "critical"
        ttl = m.ttl_s if m.ttl_s is not None else DEFAULT_TTL_S[priority]
        ttl = min(max(int(ttl), 0), MAX_TTL_S)
        data: dict[str, str] = {}
        for k, v in m.data.items():
            lk = k.lower()
            if (
                not _DATA_KEY.match(k)
                or lk in _RESERVED
                or lk.startswith(("google", "gcm"))
                or not isinstance(v, str)
            ):
                raise ValueError("bad_data")
            data[k] = v
        android: dict[str, Any] = {"priority": "HIGH" if high else "NORMAL", "ttl": f"{ttl}s"}
        apns_headers = {
            "apns-priority": "10" if high else "5",
            "apns-expiration": str(int(self.clock().timestamp()) + ttl),
        }
        if m.collapse_key and _COLLAPSE.match(m.collapse_key):
            android["collapse_key"] = m.collapse_key
            apns_headers["apns-collapse-id"] = m.collapse_key
        if m.channel and _CHANNEL.match(m.channel):
            android["notification"] = {"channel_id": m.channel}
        message = {
            "token": m.token,
            "notification": {"title": m.title, "body": m.body},
            "data": data,
            "android": android,
            "apns": {"headers": apns_headers, "payload": {"aps": {"sound": "default"}}},
        }
        return {"message": message}

    # ------------------------------------------------------------------------------ send
    def send(self, message: PushMessage) -> ChannelResult:
        return self._send_one(message)

    def send_each(self, messages: list[PushMessage]) -> list[ChannelResult]:
        """One result per message, in order, in chunks of 500. Per-message failures never stop
        the rest; once FCM pushes back (429 or Retry-After) the remaining messages are NOT sent in
        this call: they come back transient with the same wait."""
        out: list[ChannelResult] = []
        pushback: ChannelResult | None = None
        for start in range(0, len(messages), MAX_BATCH):
            for m in messages[start : start + MAX_BATCH]:
                if pushback is not None:
                    out.append(
                        ChannelResult(
                            False,
                            FailureKind.TRANSIENT,
                            detail="backpressure",
                            retry_after_s=pushback.retry_after_s,
                        )
                    )
                    continue
                try:
                    res = self._send_one(m)
                except Exception as e:  # noqa: BLE001 - one token must not stop the others
                    res = ChannelResult(
                        False, FailureKind.TRANSIENT, detail=f"exception:{type(e).__name__}"
                    )
                out.append(res)
                if (
                    not res.accepted
                    and res.failure is FailureKind.TRANSIENT
                    and (res.detail in ("quota_exceeded", "unavailable") or res.retry_after_s)
                ):
                    pushback = res
        return out

    def _send_one(self, m: PushMessage) -> ChannelResult:
        token = m.token
        if not isinstance(token, str) or not 1 <= len(token) <= 1024 or not token.isprintable():
            return ChannelResult(False, FailureKind.UNREGISTERED, detail="invalid_token")
        try:
            payload = self.build_payload(m)
        except ValueError:
            return ChannelResult(False, FailureKind.PERMANENT, detail="bad_data")
        body = json.dumps(payload, ensure_ascii=False).encode()
        if len(body) - len(token) > MAX_PAYLOAD_BYTES:
            return ChannelResult(False, FailureKind.PERMANENT, detail="payload_too_large")
        url = f"{self._base}/v1/projects/{self._account.project_id}/messages:send"
        for attempt in (0, 1):
            try:
                access = self._access_token(force=attempt == 1)
            except _AuthFailure as e:
                return ChannelResult(False, e.failure, detail=e.detail, retry_after_s=e.retry_after)
            try:
                resp = self._client.post(
                    url,
                    content=body,
                    headers={
                        "Authorization": f"Bearer {access}",
                        "Content-Type": "application/json; charset=UTF-8",
                    },
                )
            except httpx.HTTPError as e:
                log.warning("fcm transport error", extra={"error_type": type(e).__name__})
                return ChannelResult(False, FailureKind.TRANSIENT, detail="transport")
            if (
                resp.status_code == 401
                and attempt == 0
                and _fcm_code(resp) != "THIRD_PARTY_AUTH_ERROR"
            ):
                continue  # the cached token went stale: fetch a new one once
            return self._interpret(resp)
        return ChannelResult(False, FailureKind.TRANSIENT, detail="auth")  # pragma: no cover

    def _interpret(self, resp: httpx.Response) -> ChannelResult:
        status = resp.status_code
        if status == 200:
            name = None
            try:
                name = resp.json().get("name")
            except (ValueError, AttributeError):
                pass  # accepted even if the reply is unreadable: never risk a duplicate push
            return ChannelResult(
                True, provider_message_id=name[:128] if isinstance(name, str) else None
            )
        code = _fcm_code(resp)
        retry = parse_retry_after(resp.headers.get("retry-after"), self.clock())
        log.warning("fcm refused a message", extra={"status": status, "fcm_error": code})
        if 300 <= status < 400:  # never followed
            return ChannelResult(False, FailureKind.PERMANENT, detail="redirect_refused")
        if code == "UNREGISTERED" or status == 404:
            return ChannelResult(False, FailureKind.UNREGISTERED, detail="unregistered")
        if code == "INVALID_ARGUMENT" or status == 400:
            if _token_is_the_problem(resp):
                return ChannelResult(False, FailureKind.UNREGISTERED, detail="invalid_token")
            return ChannelResult(False, FailureKind.PERMANENT, detail="invalid_argument")
        if code == "SENDER_ID_MISMATCH":  # a config problem: must NOT revoke the token
            return ChannelResult(False, FailureKind.PERMANENT, detail="sender_id_mismatch")
        if code == "THIRD_PARTY_AUTH_ERROR":
            return ChannelResult(False, FailureKind.PERMANENT, detail="third_party_auth")
        if code == "QUOTA_EXCEEDED" or status == 429:
            return ChannelResult(
                False, FailureKind.TRANSIENT, detail="quota_exceeded", retry_after_s=retry or 60.0
            )
        if code in ("UNAVAILABLE", "INTERNAL") or status >= 500 or status in (408, 425):
            return ChannelResult(
                False, FailureKind.TRANSIENT, detail="unavailable", retry_after_s=retry
            )
        if status == 401:
            return ChannelResult(False, FailureKind.TRANSIENT, detail="auth")
        return ChannelResult(False, FailureKind.PERMANENT, detail=f"http_{status}")


def _error_doc(resp: httpx.Response) -> dict:
    try:
        err = resp.json().get("error")
    except (ValueError, AttributeError):
        return {}
    return err if isinstance(err, dict) else {}


def _fcm_code(resp: httpx.Response) -> str:
    err = _error_doc(resp)
    for d in err.get("details") or []:
        if isinstance(d, dict) and isinstance(d.get("errorCode"), str):
            return d["errorCode"][:40]
    status = err.get("status")
    return status[:40] if isinstance(status, str) else ""


def _token_is_the_problem(resp: httpx.Response) -> bool:
    """INVALID_ARGUMENT naming the token field (a malformed or garbage registration token)."""
    for d in _error_doc(resp).get("details") or []:
        if not isinstance(d, dict):
            continue
        for v in d.get("fieldViolations") or []:
            if isinstance(v, dict) and str(v.get("field", "")).endswith("token"):
                return True
    return False
