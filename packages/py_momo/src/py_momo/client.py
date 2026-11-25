"""Collections and Disbursements clients over an injectable transport.

Endpoint paths and headers: see README ("from public docs, unverified against the live sandbox").
"""

from __future__ import annotations

import json
import math
import random
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass

from . import money
from .auth import TokenManager
from .config import MomoConfig
from .errors import (
    AuthError,
    CallbackRejected,
    InvalidReference,
    MomoError,
    NotFound,
    OutcomeUnknown,
    RateLimited,
    RequestRejected,
    ServerError,
    TransportError,
)
from .logs import log, mask_msisdn
from .msisdn import normalize_msisdn
from .status import PaymentResult, Status, map_reason, map_status
from .transport import HttpRequest, HttpResponse, Transport, UrllibTransport


def new_reference_id() -> str:
    """A fresh X-Reference-Id. Persist it BEFORE the first call: it is the idempotency key."""
    return str(uuid.uuid4())


def check_reference_id(value: object) -> str:
    try:
        parsed = uuid.UUID(str(value))
    except (ValueError, AttributeError, TypeError) as exc:
        raise InvalidReference("reference_id must be a UUID") from exc
    if str(parsed) != str(value).lower() or parsed.version != 4:
        raise InvalidReference("reference_id must be a canonical UUID v4")
    return str(parsed)


@dataclass(frozen=True)
class CallbackVerification:
    """What MTN itself says about the payment; the callback body is only a hint."""

    raw_body: bytes  # store this untouched (SR 7.8)
    result: PaymentResult  # from the status endpoint, never from the body
    body_status: Status | None  # what the body claimed (informational)
    agrees: bool  # body claim matches MTN's answer


class _Base:
    PRODUCT = ""  # token path segment
    API = ""  # e.g. collection/v1_0
    RESOURCE = ""  # requesttopay | transfer

    def __init__(
        self,
        config: MomoConfig,
        transport: Transport | None = None,
        *,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        jitter: Callable[[], float] = random.random,
    ):
        self.config = config
        self._transport = transport or UrllibTransport()
        self._sleep = sleep
        self._jitter = jitter
        self._tokens = TokenManager(config, self._transport, self.PRODUCT, clock)

    # ---- low level -------------------------------------------------------------------
    def _backoff(self, attempt: int, retry_after: float | None = None) -> None:
        c = self.config
        if retry_after is not None:
            delay = self._clamp(retry_after)
        else:
            delay = min(c.backoff_max_seconds, c.backoff_base_seconds * 2**attempt)
            delay *= 0.5 + self._jitter() / 2
        self._sleep(delay)

    def _headers(self, token: str, reference_id: str | None, post: bool) -> dict[str, str]:
        c = self.config
        h = {
            "Authorization": f"Bearer {token}",
            "X-Target-Environment": c.target_environment,
            "Ocp-Apim-Subscription-Key": c.subscription_key,
        }
        if post:
            h["X-Reference-Id"] = reference_id
            h["Content-Type"] = "application/json"
            if c.callback_url:
                h["X-Callback-Url"] = c.callback_url
        return h

    def _once(self, method: str, path: str, reference_id: str | None, body: dict | None):
        """One logical HTTP call: token, 401 -> refresh and retry ONCE. No other retries."""
        refreshed = False
        while True:
            token = self._tokens.get()
            req = HttpRequest(
                method,
                f"{self.config.base_url}/{path}",
                self._headers(token, reference_id, method == "POST"),
                json.dumps(body).encode() if body is not None else None,
                self.config.timeout_seconds,
            )
            resp = self._transport.send(req)  # may raise TransportError/TransportTimeout
            if resp.status == 401:
                self._tokens.invalidate(token)
                if refreshed:
                    raise AuthError("401 after token refresh")
                refreshed = True
                log.info("token rejected (401); refreshing once")
                continue
            return resp

    def _retry_after(self, resp: HttpResponse) -> float | None:
        """Retry-After in seconds, finite and clamped to [0, backoff_max_seconds]. Anything we
        cannot parse as a plain number (HTTP dates, garbage, nan, inf) -> None (use backoff)."""
        raw = next((v for k, v in resp.headers.items() if k.lower() == "retry-after"), None)
        if raw is None or not raw.strip():
            return None
        try:
            value = float(raw.strip())
        except (ValueError, OverflowError):
            return None
        if not math.isfinite(value):
            return None if math.isnan(value) else self._clamp(value)
        return self._clamp(value)

    def _clamp(self, value: float) -> float:
        return max(0.0, min(value, self.config.backoff_max_seconds))

    def _raise_for(self, resp: HttpResponse) -> None:
        s = resp.status
        if s == 429:
            raise RateLimited(self._retry_after(resp))
        if s >= 500:
            raise ServerError(s)
        if s >= 400:
            code, message = "", ""
            try:
                d = json.loads(resp.body)
                code, message = str(d.get("code", "")), str(d.get("message", ""))
            except (ValueError, AttributeError):
                pass
            cls = NotFound if s == 404 else RequestRejected
            raise cls(s, code, message)

    def _get_with_retry(self, path: str) -> HttpResponse:
        """GETs are safe to repeat: retry timeouts, 429 and 5xx with backoff."""
        last: MomoError | None = None
        for attempt in range(self.config.max_attempts):
            try:
                resp = self._once("GET", path, None, None)
                self._raise_for(resp)
                return resp
            except (RateLimited, ServerError, TransportError) as e:
                last = e
                if attempt + 1 < self.config.max_attempts:
                    self._backoff(attempt, getattr(e, "retry_after", None))
        assert last is not None
        raise last

    # ---- status ----------------------------------------------------------------------
    def get_status(self, reference_id: str) -> PaymentResult:
        ref = check_reference_id(reference_id)
        resp = self._get_with_retry(f"{self.API}/{self.RESOURCE}/{ref}")
        return self._parse_result(ref, resp.body)

    def _parse_result(self, ref: str, body: bytes) -> PaymentResult:
        try:
            d = json.loads(body)
            if not isinstance(d, dict):
                raise ValueError
        except ValueError as exc:
            raise ServerError(502) from exc  # unusable body: treat as transient
        status = map_status(d.get("status"))
        reason, raw_reason = (
            map_reason(d.get("reason")) if status is Status.FAILED else (None, None)
        )
        if status is Status.FAILED and reason is None:
            from .status import FailureReason

            reason, raw_reason = FailureReason.UNKNOWN, None
        amount = None
        if d.get("amount") is not None:
            try:
                amount = money.from_provider_amount(d["amount"])
            except MomoError:
                amount = None
        return PaymentResult(
            ref,
            status,
            reason,
            raw_reason,
            d.get("financialTransactionId"),
            amount,
            d.get("currency"),
            d,
        )

    # ---- create (idempotent) ---------------------------------------------------------
    def _create(self, reference_id: str, body: dict, masked: str) -> PaymentResult:
        """POST with X-Reference-Id. Never resends blindly.

        Once a POST may have reached MTN (timeout, connection reset, 5xx, 409) the ONLY ways out
        are: a PaymentResult; OutcomeUnknown; or a definite 4xx refusal of the POST itself
        (RequestRejected / AuthError, with a response to the POST). Rules:
        - 202: accepted (PENDING).  409: exists; return its status (status failure -> Unknown).
        - 429 on the POST: MTN answered before processing, so definitely NOT accepted: wait
          (Retry-After) and POST again with the same reference, no status query. Exhausted ->
          RateLimited (nothing was created).
        - timeout / connection error / 5xx / any undocumented non-error code: ambiguous. Query
          status; a result is returned. A DEFINITE 404 (reference unknown to MTN) is the only
          case that permits another POST (same reference). Any other status outcome
          (timeout, 5xx, 429, 401, 403, bad body...) raises OutcomeUnknown at once,
          with no further POST.
        """
        path = f"{self.API}/{self.RESOURCE}"
        rate_limited: RateLimited | None = None
        for attempt in range(self.config.max_attempts):
            rate_limited = None
            try:
                resp = self._once("POST", path, reference_id, body)
            except TransportError as e:
                log.warning(
                    "create %s: no response (%s); checking status", reference_id, type(e).__name__
                )
                found = self._probe(reference_id, f"POST: {type(e).__name__}")
                if found is not None:
                    return found
                self._backoff(attempt)
                continue
            if resp.status in (200, 201, 202):
                log.info("%s accepted ref=%s party=%s", self.RESOURCE, reference_id, masked)
                return PaymentResult(reference_id, Status.PENDING)
            if resp.status == 409:  # duplicate reference: same key never makes a second payment
                log.info("duplicate reference %s; returning existing status", reference_id)
                found = self._probe(reference_id, "POST: HTTP 409 duplicate")
                if found is None:
                    raise OutcomeUnknown(
                        reference_id, "POST said 409 duplicate but status says 404 unknown"
                    )
                return found
            if resp.status == 429:
                try:
                    self._raise_for(resp)
                except RateLimited as rl:
                    rate_limited = rl
                    self._backoff(attempt, rl.retry_after)
                continue
            if resp.status >= 500 or resp.status < 400:  # 5xx, or an undocumented 1xx/3xx/2xx
                found = self._probe(reference_id, f"POST: HTTP {resp.status}")
                if found is not None:
                    return found
                self._backoff(attempt)
                continue
            self._raise_for(resp)  # 4xx: MTN definitely refused the POST
        if rate_limited is not None:
            raise rate_limited
        raise OutcomeUnknown(
            reference_id,
            f"{self.config.max_attempts} attempts; last status query said 404 (not created) "
            "but the POST kept failing",
        )

    def _probe(self, reference_id: str, why: str) -> PaymentResult | None:
        """Status query after an ambiguous POST. Result -> it; definite 404 -> None (safe to
        POST again, same reference); anything else -> OutcomeUnknown (cause preserved)."""
        try:
            return self.get_status(reference_id)
        except NotFound:
            return None
        except Exception as exc:  # noqa: BLE001 - any status failure is "unknown", by design
            raise OutcomeUnknown(
                reference_id, f"{why}; status query failed: {type(exc).__name__}"
            ) from exc

    # ---- callbacks -------------------------------------------------------------------
    def verify_callback(
        self,
        raw_body: bytes,
        *,
        reference_id: str | None = None,
        expected_amount_ugx: int | None = None,
    ) -> CallbackVerification:
        """Never trusts the body: it only identifies WHICH payment to ask MTN about.

        reference_id should come from our own callback URL path (X-Callback-Url embeds it); we
        also put the reference in externalId. If both are present and differ, reject. The
        returned result always comes from MTN's status endpoint (SEC-8). Raises
        CallbackRejected for unknown references, malformed bodies or amount mismatch.
        """
        try:
            body = json.loads(raw_body)
            if not isinstance(body, dict):
                raise ValueError
        except (ValueError, TypeError) as exc:
            raise CallbackRejected("callback body is not a JSON object") from exc
        ext = body.get("externalId")
        candidates = {c for c in (reference_id, ext if _is_uuid(ext) else None) if c}
        if len(candidates) != 1:
            raise CallbackRejected("callback does not identify exactly one payment reference")
        ref = next(iter(candidates))
        try:
            check_reference_id(ref)
        except InvalidReference as exc:
            raise CallbackRejected("callback reference is not a valid UUID") from exc
        try:
            result = self.get_status(ref)
        except NotFound as exc:
            raise CallbackRejected("MTN does not know this reference") from exc
        if expected_amount_ugx is not None:  # fail closed: MTN must confirm amount AND currency
            if result.amount_ugx is None:
                raise CallbackRejected("MTN status has no usable amount; cannot confirm amount")
            if result.currency != self.config.currency:
                raise CallbackRejected("currency reported by MTN differs from configured currency")
            if result.amount_ugx != expected_amount_ugx:
                raise CallbackRejected("amount reported by MTN differs from expected amount")
        claimed = map_status(body["status"]) if "status" in body else None
        agrees = claimed is None or claimed is result.status
        if not agrees:
            log.warning("callback for %s claimed %s but MTN says %s", ref, claimed, result.status)
        return CallbackVerification(raw_body, result, claimed, agrees)


def _is_uuid(v: object) -> bool:
    try:
        check_reference_id(v)
        return True
    except InvalidReference:
        return False


class HttpCollectionsClient(_Base):
    PRODUCT = "collection"
    API = "collection/v1_0"
    RESOURCE = "requesttopay"

    def request_to_pay(
        self,
        *,
        reference_id: str,
        amount_ugx: int,
        payer_msisdn: str,
        external_id: str,
        payer_message: str = "",
        payee_note: str = "",
    ) -> PaymentResult:
        ref = check_reference_id(reference_id)
        c = self.config
        amount = money.to_provider_amount(
            amount_ugx, minimum=c.min_amount_ugx, maximum=c.max_amount_ugx
        )
        msisdn = normalize_msisdn(payer_msisdn, strict_uganda=c.msisdn_strict)
        body = {
            "amount": amount,
            "currency": c.currency,
            "externalId": str(external_id),
            "payer": {"partyIdType": "MSISDN", "partyId": msisdn},
            "payerMessage": payer_message,
            "payeeNote": payee_note,
        }
        return self._create(ref, body, mask_msisdn(msisdn))


class HttpDisbursementsClient(_Base):
    PRODUCT = "disbursement"
    API = "disbursement/v1_0"
    RESOURCE = "transfer"

    def transfer(
        self,
        *,
        reference_id: str,
        amount_ugx: int,
        payee_msisdn: str,
        external_id: str,
        payer_message: str = "",
        payee_note: str = "",
    ) -> PaymentResult:
        ref = check_reference_id(reference_id)
        c = self.config
        amount = money.to_provider_amount(
            amount_ugx, minimum=c.min_amount_ugx, maximum=c.max_amount_ugx
        )
        msisdn = normalize_msisdn(payee_msisdn, strict_uganda=c.msisdn_strict)
        body = {
            "amount": amount,
            "currency": c.currency,
            "externalId": str(external_id),
            "payee": {"partyIdType": "MSISDN", "partyId": msisdn},
            "payerMessage": payer_message,
            "payeeNote": payee_note,
        }
        return self._create(ref, body, mask_msisdn(msisdn))
