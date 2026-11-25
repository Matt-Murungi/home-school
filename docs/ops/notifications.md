# Notifications (N01 #126): operations notes

Code: `services/core/src/hs_core/notifications/`. The real FCM/APNs and Resend adapters are N02
(#127, section "N02 channels" below). They are used ONLY when their credentials are configured;
without them push and email stay "unconfigured" (every send fails permanently and loudly) and
nothing is delivered. All provider code is **unverified until a real account is used**.

## Catalog

26 events: the 25 events of PRD 7.13 plus 1 sample marketing event
(`marketing_announcement_parent`, the only mutable category). All copy is a draft for owner review.
Every event has an explicit default lifetime (`TTL_DECISIONS` in `catalog.py`; import fails if a
new event has none). Safeguarding never expires. A row that cannot be delivered before its
`not_after` (explicit, or the default: minutes from the first send time, cut short at the session
start for reminders) is stored as `blocked` with `last_error = suppressed:expired` and shown as
`public_status = expired`; it is never sent late. `not_after` must be timezone-aware
(`NaiveDatetime` otherwise).

## Delivery guarantees (honest version)

- **Provider exceptions** (timeout, connection error, anything that is an `Exception`) become a
  transient failure that records the exception TYPE NAME only (`transient:exception:TimeoutError`).
  Messages are never stored or logged (they may hold tokens, addresses, URLs). Rows retry with
  backoff up to `NOTIFY_MAX_ATTEMPTS` (4), then `failed`; critical push falls back to email and
  critical failures raise an alert. `send()` does not raise provider errors. `BaseException`
  (`KeyboardInterrupt`, `SystemExit`, a killed worker) is not caught. One failing row never blocks
  the others in `dispatch_due`.
- **Push is at-least-once.** FCM v1 has no idempotency key. If the worker dies after FCM accepted
  the message and before the row is marked `sent`, the retry after the lease (`NOTIFY_LEASE_S`)
  sends again: at most one duplicate push per device token per crash.
- **Email dedupes only if Resend honours the idempotency key** we send (ADR 0005). N02 must
  verify this against the real provider before relying on it.
- No cross-process claim exists (Appwrite has no conditional update): protection is the lease plus
  the provider key.

## Address hygiene

`EmailChannel` validates every recipient with `address.validate_address` (N02) before any provider
call; a bad address is `permanent:invalid_address`. Rules: ONE bare address; exactly one `@`; no
display name (`Name <a@b>`), angle brackets, quotes, comments, lists (`,` `;` space), CR/LF/tab,
control, zero-width, BOM, bidi or NBSP characters (everything outside printable ASCII is refused);
at most 254 characters, local part 64, domain labels 63; the domain needs a dot and an alphabetic
TLD of 2+ letters (no IP literals, no numeric TLD); local part is a dot-atom of letters, digits and
`#$&'*+/=?^_{}~-` (no `% ! |` or backtick). **Decision:** internationalised addresses are NOT
supported (no SMTPUTF8): a Unicode domain must arrive as punycode (`xn--...`), a Unicode local part
is refused. The contact directory must keep using this validator.

## Rate limit

5 non-critical sends per recipient, template, channel and *subject* per 10 minutes. Pass
`subject_id` (child, session or invoice id) to `send()` for events about one child, so e.g. several
`payment_received` rows for different children are not suppressed as one burst. Without a
`subject_id` all sends share one bucket. Critical events are never limited. All rows in the window
are counted by cursor paging (no 200-row undercount); the count is still approximate under
concurrent sends because there is no atomic counter (a counter-slot approach as in A01 is the
follow-up if this ever matters).

## Retention and the inbox

`retention.expire_payloads` (C03 hook) walks every row older than the boundary by cursor (100 per
list call, no offsets), removes the variables and sets `deleted_at`, and keeps the status row and
metadata. Rows created exactly at the boundary are kept; `queued` rows are never touched. The
inbox API must call `service.is_renderable(row)` and skip (or show a neutral line for) rows where
it is false: `render_notification` raises `MissingVariables` on an expired payload.

## Permissions and audit (checked at the time of this fix)

`notification*` and `device_token` are not in the M06 audited-table registry, and M05 already has
specs for them (per-user rows). A write path that adds audited tables later must use the audited
repository.

## N02 channels (push, email, webhook)

### Which adapter runs

`build_notification_service(settings, ...)` picks per channel. A real adapter only when EVERY
setting of its group is present AND valid; none set = the N01 placeholder (permanent failure, a
critical event alerts); a half-set or invalid group fails startup (`ConfigError`, variable names
only, never values). It never falls back to a fake success.

| Group | Settings (all required together) | Adapter |
|---|---|---|
| Email | `RESEND_API_KEY`, `RESEND_WEBHOOK_SECRET`, `EMAIL_FROM` (optional `EMAIL_FROM_NAME`, `EMAIL_REPLY_TO`) | `resend.ResendEmailSender` |
| Push | `FCM_SERVICE_ACCOUNT_JSON` (optional `FCM_PROJECT_ID`) | `fcm.FcmPushChannel` |

`From`/`Reply-To` come from settings per environment (D05 plan: `no-reply@mail.<domain>`, reply-to
a monitored mailbox). Templates are rendered by N01 (plain text plus escaped HTML); no attachments.

### Idempotency and at-least-once

- **Email:** every send carries `Idempotency-Key: <notification id>:<attempt group>` (max 256
  chars; Resend keeps keys 24 hours). The key is stable across a crash and a lease retry (the same
  row, the same group) and only changes after a DEFINITE transient failure. So a retry after a crash
  is deduped by Resend, IF Resend honours it as documented (unverified). Past 24 hours a key is
  forgotten: the longest retry chain (4 attempts, 60 s base) is far shorter.
- **Push is at-least-once.** FCM HTTP v1 has no idempotency key and none is sent. A crash after FCM
  accepted a message and before the row is marked `sent` makes the retry (after `NOTIFY_LEASE_S`)
  send once more: at most ONE duplicate push per device per crash. Accepted; the app should show
  one notification per `notification_id` (the data payload carries it, so the apps can replace, not
  stack, a repeat).
- An unreadable 200 from FCM counts as accepted (never risk a duplicate); an unreadable 200 from
  Resend is retried with the SAME key.

### Push details (FCM HTTP v1, iOS through APNs via FCM)

- `notification` block (shown by the OS when the app is closed) plus `data` (`notification_id`,
  `deep_link`); data keys are validated (no reserved `from`, `notification`, `google*`, `gcm*`).
- Priority: `critical` = Android `HIGH` / APNs `10`; everything else `NORMAL` / `5`. TTL from the
  row's `not_after` (clamped 0 to 28 days; defaults critical 24 h, others 1 h), also as
  `apns-expiration`. Optional `collapse_key` (`apns-collapse-id`). Android channel ids
  `hs_critical`, `hs_operational`, `hs_marketing` (the apps must create them: Flutter follow-up).
- Payload over 4096 bytes is refused locally (`payload_too_large`, permanent).
- One request per token (HTTP v1 has no multicast); `send_each` handles lists in chunks of 500 with
  per-token results, one failing token never stops the others, and once FCM answers 429 or
  Retry-After the rest of the call is NOT sent (transient, same wait).
- Errors: `UNREGISTERED`/404 and an `INVALID_ARGUMENT` that names the token field -> the device
  token is revoked (N01 cleanup); `SENDER_ID_MISMATCH`, `THIRD_PARTY_AUTH_ERROR`, other 4xx ->
  permanent WITHOUT revoking (a config problem must not wipe every token); 429/5xx/timeouts ->
  transient with `Retry-After` clamped to [0, 3600] s (nan, inf, garbage ignored; a 429 without the
  header waits 60 s). A 401 refreshes the OAuth token once.
- OAuth2: service-account JWT (RS256) to the PINNED `https://oauth2.googleapis.com/token` (the key
  file's own `token_uri` is ignored), token cached until 5 minutes before expiry.
- Device endpoints: `PUT /v1/me/devices` (after sign-in) and `DELETE /v1/me/devices` (before the
  session is destroyed) register and remove the caller's token (role must match the app).

### HTTP hygiene (both providers)

Redirects are never followed (`follow_redirects=False`; a 3xx is a failed call; the MoMo review
found auto-followed redirects leak credentials; two-server tests prove it). Timeouts: connect
3 s, read 10 s. Proxies: httpx honours `HTTPS_PROXY`/`HTTP_PROXY`/`NO_PROXY` from the environment
(set them on the Fly app only if egress needs a proxy; TLS is tunnelled, the proxy never sees
payloads). No base URL is configurable in settings (a setting must not be able to redirect
credentials). Logs carry only ids, status codes and exception TYPE names; adapter reprs hide keys.

### Email webhook `POST /v1/webhooks/resend`

Public, authenticated by the Svix signature. Headers `svix-id`, `svix-timestamp`,
`svix-signature`; signed content `id.timestamp.body`; HMAC-SHA256 with the base64 key inside the
`whsec_` secret; the header may hold several space separated `v1,<sig>` values (any valid one is
enough, at most 10 are examined); constant-time comparison; timestamp must be within 5 minutes in
BOTH directions; body at most 256 KiB. Answers: 200 accepted (also duplicates), 401 any signature or
timestamp problem (generic body), 400 signed but not the expected JSON, 413 too large, 503 email
provider not configured, 500 on our own failure (Resend retries).

Replay protection: the `svix-id` (reused by Resend on retries) is claimed in `webhook_event`
(derived row id, create = atomic claim). A `done` claim is acknowledged and ignored; a claim left in
`processing` by a crash is re-applied (every effect is idempotent). Rows are deleted after 7 days by
the `webhook-event-cleanup` job (the signature tolerance is 5 minutes, so an older replay fails the
timestamp check first).

Event mapping (ADR 0005): `email.delivered` -> `delivered`; `email.bounced` Permanent ->
`bounced` (+ suppress), Temporary or unrecognised -> `bounced_soft` (never suppresses);
`email.delivery_delayed` -> `bounced_soft`; `email.complained` -> `complained` (+ suppress);
`email.failed` -> `failed` (row failed, no suppression); `email.suppressed` -> `suppressed` (row
failed, address added: Resend already refuses it). `sent`, `opened`, `clicked`, `scheduled`,
`received` are ignored. The notification is found through the `notification_id` TAG we send (no
lookup column needed). An event for an unknown notification is counted, not an error, and still
updates the suppression list.

### Suppression list (`email_suppression`)

Hard bounce, complaint and provider suppression add the address (hash only; see the table note).
`EmailChannel` checks it before every send: a suppressed address gets no provider call and the row
fails `permanent:suppressed`. **Decision (owner question, default):** suppression applies to ALL
email including safeguarding. A safeguarding email to a suppressed address fails and raises the
`notification_delivery_failed` alert; push and in-app already went out in parallel; an admin phones
the contact. A support release sets `revoked_at` (`SuppressionList.release`; the admin screen is a
follow-up); a new hard bounce re-lists the address.

### Rotation

`RESEND_API_KEY`: provider-key procedure. `RESEND_WEBHOOK_SECRET`: NOT overlapping (one secret is
verified; webhooks fail 401 and are retried between rolling it in Resend and the Fly redeploy).
`FCM_SERVICE_ACCOUNT_JSON`: create a new key, set the JSON, delete the old key. Procedures in
`docs/runbooks/secrets.md`.

### Unverified until a real account is used (assumptions)

Resend: idempotency header behaviour on reuse, the `email.failed`/`email.suppressed` payload
shapes, that `data.tags` echoes our tags, a Retry-After header on 429, whether a webhook secret can
be rolled with overlap. FCM: the exact Android/APNs field names and the 4096 byte limit (from the
v1 reference as remembered), `details[].errorCode` shapes, reserved data keys. Sources and fetch
date (26 Nov 2026) are in the module docstrings of `resend.py` and `fcm.py`.

### Owner-blocked steps (nothing below was done)

1. Choose the sending domain (D05 default: `mail.<domain>`), create a Resend account (Free is
   enough for staging), add the domain, create the SPF/DKIM/bounce-MX records, add DMARC at
   `p=none` (ADR 0005 checklist), create a sending-only API key and a webhook for
   `https://<core>/v1/webhooks/resend` with the events delivered, bounced, complained,
   delivery_delayed, failed, suppressed; sign the DPA (DP-9).
2. Set `RESEND_API_KEY`, `RESEND_WEBHOOK_SECRET`, `EMAIL_FROM` (+ optional `EMAIL_REPLY_TO`) as Fly
   secrets/env of staging; all three together.
3. Create the Firebase project (one per environment), enable Cloud Messaging, create a service
   account key with the FCM sender role, upload the iOS APNs auth key (Apple developer account) in
   the Firebase console; set `FCM_SERVICE_ACCOUNT_JSON` as a Fly secret. The Flutter apps need the
   Firebase config files and the `firebase_messaging` plugin (F05/N02 follow-up, needs the project).
4. Run the deliverability and push tests (ADR 0005, Gmail, Yahoo, a Ugandan host; Android and iOS
   devices; deep link opens session detail) and report. Defaults used until answered: suppression
   covers safeguarding mail too; marketing quiet-hours as N01.
