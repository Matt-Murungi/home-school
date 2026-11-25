"""N02 test helpers: throwaway RSA key / service account, Svix signing, mock transports."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path

import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

FIXTURES = Path(__file__).parent / "fixtures" / "n02"
NOW = datetime(2027, 1, 18, 9, 0, tzinfo=UTC)  # 12:00 Kampala, never the wall clock
NID = "a" * 36
WEBHOOK_KEY = b"0123456789abcdef0123456789abcdef"
WEBHOOK_SECRET = "whsec_" + base64.b64encode(WEBHOOK_KEY).decode()
API_KEY = "re_canary_canary_canary_canary"  # secret-scan: allow
CANARY_EMAIL = "canary.person@example.org"
CANARY_TOKEN = "fcm-token-CANARY-0123456789"  # secret-scan: allow


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text())


@lru_cache(maxsize=1)
def _key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def service_account_json(project: str = "hs-test-project", **over) -> str:
    pem = (
        _key()
        .private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        .decode()
    )
    doc = {
        "type": "service_account",
        "project_id": project,
        "private_key_id": "kid123",
        "private_key": pem,
        "client_email": "fcm-sender@hs-test-project.iam.gserviceaccount.com",
        "token_uri": "https://evil.example/token",  # must be ignored by the adapter
    }
    doc.update(over)
    return json.dumps(doc)


def public_key():
    return _key().public_key()


def svix_headers(
    body: bytes,
    *,
    msg_id: str = "msg_2abc",
    ts: int | None = None,
    key: bytes = WEBHOOK_KEY,
    now: datetime = NOW,
) -> dict[str, str]:
    stamp = str(int(now.timestamp()) if ts is None else ts)
    signed = msg_id.encode() + b"." + stamp.encode() + b"." + body
    sig = base64.b64encode(hmac.new(key, signed, hashlib.sha256).digest()).decode()
    return {"svix-id": msg_id, "svix-timestamp": stamp, "svix-signature": f"v1,{sig}"}


def json_response(name_or_obj, status: int = 200, headers: dict | None = None) -> httpx.Response:
    obj = fixture(name_or_obj) if isinstance(name_or_obj, str) else name_or_obj
    return httpx.Response(status, json=obj, headers=headers or {})


class Transport:
    """httpx.MockTransport with a request log and scripted answers (callable or list)."""

    def __init__(self, handler) -> None:
        self.requests: list[httpx.Request] = []
        self._handler = handler
        self.client = httpx.Client(transport=httpx.MockTransport(self._handle))

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self._handler(request)

    @property
    def bodies(self) -> list[dict]:
        out = []
        for r in self.requests:
            try:
                out.append(json.loads(r.content))
            except ValueError:
                out.append({})
        return out
