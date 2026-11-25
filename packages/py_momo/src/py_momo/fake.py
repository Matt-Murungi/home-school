"""In-memory fake MTN MoMo (Collections + Disbursements) with scriptable scenarios.

It is a Transport, so the real clients run unchanged against it (headers, tokens, retries).
Public for downstream tickets (P0x payments) as the MoMo stand-in. No network, no secrets.
"""

from __future__ import annotations

import base64
import json
import re
import uuid
from dataclasses import dataclass, field

from .client import HttpCollectionsClient, HttpDisbursementsClient
from .config import Environment, MomoConfig
from .errors import TransportError, TransportTimeout
from .transport import HttpRequest, HttpResponse

_PATH = re.compile(r"^/(collection|disbursement)/v1_0/(requesttopay|transfer)(?:/([^/]+))?$")
_TOKEN = re.compile(r"^/(collection|disbursement)/token/$")

FAKE_SUB_KEY = "fake-subscription-key"
FAKE_API_USER = "fake-api-user"
FAKE_API_KEY = "fake-api-key"


@dataclass
class Scenario:
    """How one payment behaves. Build with the classmethods."""

    final_status: str = "SUCCESSFUL"
    reason: object = None  # str or dict (disbursements may return {"code":..})
    pending_polls: int = 0  # status calls that answer PENDING before the final status
    post_faults: list[str] = field(default_factory=list)
    # post_faults entries consumed one per POST: "5xx" (not accepted), "429", "timeout_before"
    # (not accepted), "timeout_after" (ACCEPTED, but the response is lost), "5xx_after" (accepted)
    callback: str = "none"  # "none" (never arrives) | "on_accept" | "on_final"
    callback_claims: str | None = None  # override status in the callback body (forged/stale)
    get_faults: list[str] = field(default_factory=list)  # "5xx" | "429" | "timeout"

    @classmethod
    def success(cls, **kw):
        return cls(**kw)

    @classmethod
    def pending_then(cls, polls: int, status="SUCCESSFUL", reason=None, **kw):
        return cls(final_status=status, reason=reason, pending_polls=polls, **kw)

    @classmethod
    def failure(cls, reason, **kw):
        return cls(final_status="FAILED", reason=reason, **kw)

    @classmethod
    def never_final(cls, **kw):
        return cls(final_status="PENDING", **kw)


@dataclass
class _Payment:
    product: str
    body: dict
    scenario: Scenario
    polls: int = 0


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def now(self) -> float:
        return self.t

    def advance(self, s: float) -> None:
        self.t += s


class FakeMomo:
    def __init__(self, *, default: Scenario | None = None, token_ttl: int = 3600):
        self.clock = FakeClock()
        self.sleeps: list[float] = []
        self.requests: list[HttpRequest] = []
        self.payments: dict[tuple[str, str], _Payment] = {}
        self.post_count = 0  # POSTs received (including rejected)
        self.deliveries: list[tuple[str, str, bytes]] = []  # (product, reference, body) callbacks
        self.default = default or Scenario.success()
        self._scripts: dict[str, Scenario] = {}
        self._tokens: dict[str, float] = {}
        self._valid_tokens: set[str] = set()
        self._ttl = token_ttl
        self._n = 0
        self.token_requests = 0
        self.token_faults: list[str] = []  # "5xx"
        self.raw_responses: dict[str, str] = {}  # reference -> raw body override for GET

    # -- scripting ----------------------------------------------------------------------
    def script(self, reference_id: str, scenario: Scenario) -> None:
        self._scripts[reference_id] = scenario

    def expire_all_tokens(self) -> None:
        """Server forgets every issued token: next use gets 401 (expiry mid-request)."""
        self._valid_tokens.clear()

    def sleep(self, s: float) -> None:
        if not isinstance(s, int | float) or s != s or s in (float("inf"), float("-inf")) or s < 0:
            raise ValueError(f"invalid sleep argument {s!r} (time.sleep would fail)")
        self.sleeps.append(s)
        self.clock.advance(s)

    def accepted(self, product: str, reference_id: str) -> bool:
        return (product, reference_id) in self.payments

    def accepted_count(self) -> int:
        return len(self.payments)

    # -- client factories ---------------------------------------------------------------
    def config(self, **kw) -> MomoConfig:
        base = dict(
            environment=Environment.SANDBOX,
            subscription_key=FAKE_SUB_KEY,
            api_user=FAKE_API_USER,
            api_key=FAKE_API_KEY,
            callback_url="https://example.invalid/hooks/momo",
            min_amount_ugx=None,
            max_amount_ugx=None,
        )
        base.update(kw)
        return MomoConfig(**base)

    def _kw(self):
        return dict(sleep=self.sleep, clock=self.clock.now, jitter=lambda: 0.0)

    def collections(self, **cfg) -> HttpCollectionsClient:
        return HttpCollectionsClient(self.config(**cfg), self, **self._kw())

    def disbursements(self, **cfg) -> HttpDisbursementsClient:
        return HttpDisbursementsClient(self.config(**cfg), self, **self._kw())

    # -- Transport ----------------------------------------------------------------------
    def send(self, request: HttpRequest) -> HttpResponse:
        self.requests.append(request)
        path = "/" + request.url.split("://", 1)[1].split("/", 1)[1]
        h = request.headers
        if h.get("Ocp-Apim-Subscription-Key") != FAKE_SUB_KEY:
            return _json(401, {"message": "Access denied due to invalid subscription key"})
        m = _TOKEN.match(path)
        if m:
            return self._token(h)
        m = _PATH.match(path)
        if not m:
            return _json(404, {"code": "RESOURCE_NOT_FOUND"})
        product, resource, ref = m.groups()
        if (product, resource) not in (
            ("collection", "requesttopay"),
            ("disbursement", "transfer"),
        ):
            return _json(404, {"code": "RESOURCE_NOT_FOUND"})
        auth = h.get("Authorization", "")
        token = auth[7:] if auth.startswith("Bearer ") else ""
        if token not in self._valid_tokens or self._tokens.get(token, 0) <= self.clock.now():
            return _json(401, {"message": "token invalid or expired"})
        if h.get("X-Target-Environment") != "sandbox":
            return _json(400, {"code": "NOT_ALLOWED_TARGET_ENVIRONMENT"})
        if request.method == "POST" and ref is None:
            return self._post(product, request)
        if request.method == "GET" and ref is not None:
            return self._get(product, ref)
        return _json(405, {})

    def _token(self, h) -> HttpResponse:
        self.token_requests += 1
        if self.token_faults:
            self.token_faults.pop(0)
            return _json(503, {})
        want = base64.b64encode(f"{FAKE_API_USER}:{FAKE_API_KEY}".encode()).decode()
        if h.get("Authorization") != f"Basic {want}":
            return _json(401, {"message": "bad credentials"})
        self._n += 1
        tok = f"tok-{self._n}"
        self._valid_tokens.add(tok)
        self._tokens[tok] = self.clock.now() + self._ttl
        return _json(
            200, {"access_token": tok, "token_type": "access_token", "expires_in": self._ttl}
        )

    def _post(self, product: str, request: HttpRequest) -> HttpResponse:
        self.post_count += 1
        h = request.headers
        ref = h.get("X-Reference-Id", "")
        try:
            if str(uuid.UUID(ref)) != ref:
                raise ValueError
        except ValueError:
            return _json(400, {"code": "INVALID_REFERENCE_ID"})
        body = json.loads(request.body or b"{}")
        if body.get("currency") != "EUR":  # sandbox restriction
            return _json(400, {"code": "INVALID_CURRENCY"})
        sc = self._scripts.get(ref, self.default)
        fault = sc.post_faults.pop(0) if sc.post_faults else None
        if fault == "5xx":
            return _json(503, {})
        if fault == "429":
            return HttpResponse(429, {"Retry-After": "2"}, b"")
        if fault == "timeout_before":
            raise TransportTimeout("fake timeout before accept")
        if (product, ref) in self.payments:
            return _json(409, {"code": "RESOURCE_ALREADY_EXIST"})
        pay = _Payment(product, body, sc)
        self.payments[(product, ref)] = pay
        if sc.callback == "on_accept":
            self._deliver(product, ref, pay, final=False)
        if fault == "timeout_after":
            raise TransportTimeout("fake timeout after accept")
        if fault == "5xx_after":
            return _json(503, {})
        return HttpResponse(202, {}, b"")

    def _state(self, pay: _Payment) -> tuple[str, object]:
        sc = pay.scenario
        if pay.polls < sc.pending_polls or sc.final_status == "PENDING":
            return "PENDING", None
        return sc.final_status, sc.reason

    def _deliver(self, product, ref, pay: _Payment, final: bool) -> None:
        st, reason = self._state(pay) if final else ("PENDING", None)
        if pay.scenario.final_status != "PENDING" and not final:
            st, reason = pay.scenario.final_status, pay.scenario.reason  # MTN calls back when done
        st = pay.scenario.callback_claims or st
        body = {
            "externalId": pay.body.get("externalId"),
            "amount": pay.body["amount"],
            "currency": pay.body["currency"],
            "status": st,
        }
        if reason:
            body["reason"] = reason
        self.deliveries.append((product, ref, json.dumps(body).encode()))

    def _get(self, product: str, ref: str) -> HttpResponse:
        pay = self.payments.get((product, ref))
        if pay is None:
            return _json(404, {"code": "RESOURCE_NOT_FOUND"})
        sc = pay.scenario
        if sc.get_faults:
            fault = sc.get_faults.pop(0)
            if fault == "5xx":
                return _json(500, {})
            if fault == "429":
                return HttpResponse(429, {}, b"")
            if fault == "timeout":
                raise TransportTimeout("fake get timeout")
            if fault == "conn":
                raise TransportError("fake connection error")
        if ref in self.raw_responses:
            return HttpResponse(200, {}, self.raw_responses[ref].encode())
        st, reason = self._state(pay)
        pay.polls += 1
        if (
            sc.callback == "on_final"
            and st != "PENDING"
            and not any(d[1] == ref for d in self.deliveries)
        ):
            self._deliver(product, ref, pay, final=True)
        out = {
            "amount": pay.body["amount"],
            "currency": pay.body["currency"],
            "externalId": pay.body.get("externalId"),
            "status": st,
        }
        if st == "SUCCESSFUL":
            out["financialTransactionId"] = f"fin-{ref[:8]}"
        if reason:
            out["reason"] = reason
        return _json(200, out)


def _json(status: int, data: dict) -> HttpResponse:
    return HttpResponse(status, {"Content-Type": "application/json"}, json.dumps(data).encode())
