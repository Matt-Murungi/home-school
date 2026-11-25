# py_momo

## Purpose

Internal, reusable Python client for MTN MoMo **Collections** (RequestToPay, status, callback
verification) and **Disbursements** (Transfer, status). Standard library only; **no dependency on
Home School code** (it is meant to be shared with the owner's other products). Ticket $02 (#77).
It does not choose or imply any gateway beyond MTN.

Layers: `config.MomoConfig` -> `transport.Transport` (injectable; `UrllibTransport` is the real one,
with timeouts) -> `auth.TokenManager` (cached, expiry margin, single-flight, refresh once on 401) ->
`HttpCollectionsClient` / `HttpDisbursementsClient` (interfaces `CollectionsClient` /
`DisbursementsClient` in `interfaces.py`). `py_momo.fake.FakeMomo` is an in-memory MTN that is itself
a `Transport`, so the real clients run against it in every test (no network, no credentials).

## Test command

```
cd packages/py_momo && uv run pytest -q          # 290+ tests, offline
cd packages/py_momo && uv run ruff check . && uv run ruff format --check .
```

Manual sandbox smoke test (owner only, calls the network, never run in CI):
`python scripts/manual_sandbox.py --yes-call-the-network` (see the script header for env vars).

## Key behaviours

- **Idempotency:** `X-Reference-Id` (UUID v4) on every create. Generate with `new_reference_id()` and
  **persist it before the first call**. Same reference never creates a second payment (409 returns the
  existing status).
- **Create contract (RequestToPay and Transfer).** `request_to_pay` / `transfer` return a
  `PaymentResult` (any status, including failed/pending) or raise exactly one of:
  - `OutcomeUnknown(reference_id, detail)`: the POST may have been accepted and status could not
    settle it (POST timeout, connection reset or 5xx AND the status query timed out, 5xx, 429,
    401, 403, returned a bad body, or said 404 after a 409). **The client never POSTs again in that
    case.** What the caller must do: keep the reference, poll `get_status(reference_id)` with the
    SAME reference until it answers definitively (a result, or a definite `NotFound` meaning MTN
    never saw it, after which re-calling the create with the same reference is safe). **Never mint
    a new reference** for the same business operation (double payment). Payouts (`transfer`): hold
    the payout and alert a human; do not retry with a new reference. `__cause__` and `detail` say
    what failed.
  - a definite 4xx refusal of the POST itself (`RequestRejected`, or `AuthError` for 401 after a
    token refresh on the POST): MTN answered the POST, nothing was created; permanent.
  - `RateLimited`: every POST got a 429 (MTN answered before processing; nothing created). The
    client waits (`Retry-After`) and re-POSTs the same reference with no status query, because a
    received 429 is a definite "not accepted"; retryable later.
  - a pre-send failure (validation errors, token endpoint failure: no POST was sent).
  The only case that allows a second POST is a status query returning a definite 404 (reference
  unknown to MTN); it reuses the same reference so MTN dedupes if that 404 was wrong.
- **Money:** `int` UGX only (floats, bools, strings rejected). `money.to_ugx(Decimal, rounding)` is the
  only place a fraction is rounded, with an explicit mode. Provider fractions are an error, never
  rounded. Our guard rails `min_amount_ugx=500` / `max_amount_ugx=5_000_000` are **assumptions**: set
  the real limits from the MTN agreement.
- **Sandbox vs production:** sandbox only supports currency **EUR** (public docs, unverified), so
  sandbox amounts are test values, not UGX; the amount integer is sent unchanged. Production is UGX
  and `X-Target-Environment` defaults to `mtnuganda` (unverified; confirm with MTN).
- **MSISDN:** `+256 7xx..`, `256..`, `00256..`, `07xx..`, `7xx..` -> `2567xxxxxxxx`. Sandbox config
  accepts non-Ugandan test numbers. The check is deliberately loose (`256` + `3|7` + 8 digits):
  Airtel (070/075), Africell (079) and 03x numbers are NOT rejected locally; MTN answers
  payer/payee not found. Prefix lists go stale with number portability, so we leave it to MTN
  (owner may tighten later).
- **Status:** SUCCESSFUL/PENDING/FAILED -> `successful/pending/failed`; unrecognised status stays
  pending; FAILED reasons enumerated in `FailureReason`, an unknown code becomes `UNKNOWN` with
  `raw_reason` kept.
- **Callbacks (SEC-8):** `verify_callback(raw_body, reference_id=...)` never trusts the body: it only
  identifies the payment, then asks MTN's status endpoint and returns that result plus whether the
  body agreed. With `expected_amount_ugx` it fails closed (`CallbackRejected`) if MTN's status has no
  amount, an unparseable amount, a different amount, or a currency other than the configured one.
  Store `raw_body` untouched. Put the reference in our callback URL path and in
  `externalId` (callback bodies do not carry `X-Reference-Id`). Always also poll.
- **Errors:** retryable = `RateLimited`, `ServerError`, `TransportError/Timeout`, `OutcomeUnknown`
  (status query only); permanent = `MomoConfigError`, `ValidationError` family, `AuthError`,
  `RequestRejected/NotFound`, `CallbackRejected`. **Scope of "permanent":** `AuthError`,
  `RequestRejected` and `NotFound` raised by a status poll (`get_status`) or by the POST itself
  are permanent only for that call. From a status poll they say nothing about whether the payment
  exists, so never treat them as "payment failed"; keep the reference. Creates convert them into
  `OutcomeUnknown` whenever a POST may have been accepted.
- **Redirects:** never followed (`UrllibTransport` returns every 3xx as a response), because a followed
  redirect would re-send the Bearer token, subscription key or Basic API key to another host and
  turn a POST into a GET. A 3xx to a POST is ambiguous (status probe, never success, never a blind
  re-POST); a 3xx from the token endpoint is `AuthError`.
- **Retry-After:** parsed as a finite number of seconds and clamped to `[0, backoff_max_seconds]`
  (stored clamped in `RateLimited.retry_after`); HTTP dates, `nan` and garbage are ignored (normal
  backoff).
- **Logging:** logger `py_momo`; MSISDNs masked (`256***456`); tokens, keys and raw bodies never logged.

## Endpoints and headers implemented

Source: MTN MoMo developer portal public documentation, from memory of the public docs.
**Every line below is "from public docs, unverified against the live sandbox".** An attempt to read
https://momodeveloper.mtn.com/API-collections on 2026-11-25 returned only a JavaScript shell with no
endpoint content, so nothing was confirmed. Verify during the first sandbox run.

| Call | Method and path (base: sandbox `https://sandbox.momodeveloper.mtn.com`, production `https://proxy.momoapi.mtn.com`) | Notes |
|---|---|---|
| Collections token | `POST /collection/token/` | `Authorization: Basic base64(apiUser:apiKey)`, `Ocp-Apim-Subscription-Key`; returns `access_token`, `expires_in` |
| RequestToPay | `POST /collection/v1_0/requesttopay` | 202 on accept; `Authorization: Bearer`, `X-Reference-Id`, `X-Target-Environment`, `Ocp-Apim-Subscription-Key`, optional `X-Callback-Url`; body `amount` (string), `currency`, `externalId`, `payer{partyIdType:MSISDN,partyId}`, `payerMessage`, `payeeNote` |
| RequestToPay status | `GET /collection/v1_0/requesttopay/{referenceId}` | Bearer, `X-Target-Environment`, subscription key; `status` PENDING/SUCCESSFUL/FAILED, `reason`, `financialTransactionId` |
| Disbursements token | `POST /disbursement/token/` | same as above, Disbursements subscription key and API user |
| Transfer | `POST /disbursement/v1_0/transfer` | as RequestToPay with `payee{...}` instead of `payer` |
| Transfer status | `GET /disbursement/v1_0/transfer/{referenceId}` | `reason` may be a string or `{code,message}`; both handled |
| (not implemented) sandbox API user creation | `POST /v1_0/apiuser`, `POST /v1_0/apiuser/{id}/apikey` | done once by the owner in the portal |

Failure reason codes enumerated are from the public error list, unverified. 409 on a repeated
`X-Reference-Id` and 404 on an unknown reference are assumed behaviours.

## Owner steps (blocked, nothing here can be done by an agent)

1. Create an MTN MoMo developer account at momodeveloper.mtn.com.
2. Subscribe to the **Collections** product and, separately, the **Disbursements** product (two
   primary keys, one per product; Disbursements may need approval).
3. Sandbox: create an API user and API key per product (portal, or `POST /v1_0/apiuser` then
   `/apikey`) with a callback host; keep user/key/subscription keys in the secret store (F09), never
   in git.
4. Host a public HTTPS callback URL on the core service (a later ticket builds the endpoint) and
   register its host for the sandbox user.
5. Run `scripts/manual_sandbox.py` with the sandbox keys and a sandbox test number; report any path,
   header or reason-code differences so this README can drop the "unverified" labels.
6. Production: MTN go-live/certification (R04, #152), production keys, the correct
   `X-Target-Environment`, the real transaction limits, and the callback host.
