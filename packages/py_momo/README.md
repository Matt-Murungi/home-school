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
cd packages/py_momo && uv run pytest -q          # 130 tests, offline
cd packages/py_momo && uv run ruff check . && uv run ruff format --check .
```

Manual sandbox smoke test (owner only, calls the network, never run in CI):
`python scripts/manual_sandbox.py --yes-call-the-network` (see the script header for env vars).

## Key behaviours

- **Idempotency:** `X-Reference-Id` (UUID v4) on every create. Generate with `new_reference_id()` and
  **persist it before the first call**. Same reference never creates a second payment (409 returns the
  existing status). After a timeout / 5xx the client queries status first; it re-POSTs (same
  reference) only when status is a definite 404 (not created); otherwise `OutcomeUnknown` and the
  caller polls later. Never mint a new reference to "retry".
- **Money:** `int` UGX only (floats, bools, strings rejected). `money.to_ugx(Decimal, rounding)` is the
  only place a fraction is rounded, with an explicit mode. Provider fractions are an error, never
  rounded. Our guard rails `min_amount_ugx=500` / `max_amount_ugx=5_000_000` are **assumptions**: set
  the real limits from the MTN agreement.
- **Sandbox vs production:** sandbox only supports currency **EUR** (public docs, unverified), so
  sandbox amounts are test values, not UGX; the amount integer is sent unchanged. Production is UGX
  and `X-Target-Environment` defaults to `mtnuganda` (unverified; confirm with MTN).
- **MSISDN:** `+256 7xx..`, `256..`, `00256..`, `07xx..`, `7xx..` -> `2567xxxxxxxx`. Sandbox config
  accepts non-Ugandan test numbers.
- **Status:** SUCCESSFUL/PENDING/FAILED -> `successful/pending/failed`; unrecognised status stays
  pending; FAILED reasons enumerated in `FailureReason`, an unknown code becomes `UNKNOWN` with
  `raw_reason` kept.
- **Callbacks (SEC-8):** `verify_callback(raw_body, reference_id=...)` never trusts the body: it only
  identifies the payment, then asks MTN's status endpoint and returns that result plus whether the
  body agreed. Store `raw_body` untouched. Put the reference in our callback URL path and in
  `externalId` (callback bodies do not carry `X-Reference-Id`). Always also poll.
- **Errors:** retryable = `RateLimited`, `ServerError`, `TransportError/Timeout`, `OutcomeUnknown`
  (status query only); permanent = `MomoConfigError`, `ValidationError` family, `AuthError`,
  `RequestRejected/NotFound`, `CallbackRejected`.
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
