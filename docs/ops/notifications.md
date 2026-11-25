# Notifications (N01 #126): operations notes

Code: `services/core/src/hs_core/notifications/`. Real FCM/APNs/Resend adapters are N02 (#127);
until then push and email are "unconfigured" (every send fails loudly) and nothing is delivered.

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

`EmailChannel` rejects an address with CR/LF, any control character, whitespace, or an empty value
(`permanent:invalid_address`, no provider call): header-injection guard. The N02 adapter and the
contact directory must keep this check.

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
