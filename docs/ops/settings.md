# Business-rule settings (H05, #134; NFR-18, A-50)

The super admin changes business rules (windows, cut-offs, grace, limits, matching weights,
budgets) in the admin app (A-50) without a release. This page says how it works, which values
live where, and the limits. The table of settings below is **generated** from
`services/core/src/hs_core/app_settings/registry.py`; a test fails when it is stale
(`cd services/core && uv run python scripts/settings_docs.py --write`).

## Which is which: settings, environment, secrets

| Kind | Examples | Where | Who changes it | Needs a deploy |
|---|---|---|---|---|
| Business rule (a **setting**) | auto-confirm window, grace days, matching weights, notification quiet hours, travel budgets | `app_setting` table, declared in `registry.py` | super admin, in A-50, with a reason | no |
| Infrastructure tuning | `JOB_ALERT_AFTER_FAILURES`, `NOTIFY_LEASE_S`, `NOTIFY_RETRY_BASE_S`, OTP limits, `SESSION_GEN_LEASE_TTL_S`, `BILLING_ROLL_CHUNK_SIZE`, `CATALOGUE_*`, `AUDIT_*` | environment variable (F09 inventory) | an engineer, by deploy | yes |
| Secret or credential | `APPWRITE_API_KEY`, `OTP_PEPPER`, `GOOGLE_MAPS_SERVER_KEY`, provider keys | secret store (F09) | owner / engineer | yes |

Settings are **non-secret by construction**: the registry holds numbers and fixed named integers
only, and a test refuses any key or field whose name looks like a secret, credential, token or URL.
Never put a secret, a URL or personal data in a setting.

**The environment variable of a registered rule is only its default.** Precedence: a value stored
by the super admin, else the environment variable (if it is a valid value, else it is ignored and
logged), else the code default in the registry. Removing a stored value is a rollback (a new
revision that restores the earlier value; the first override rolls back to the default).

## What a setting declares (registry.py)

Key (`group.name`), type (`int`, `float` or `map` of named integers), bounds, default, unit,
description, `changed_by` (super admin), reason required, `restart_required` (always false: a rule
that needs a deploy is not a setting), and **effective-from**:

- `immediate`: from its effective time, which is now unless a later time is scheduled;
- `next_day`: from the next Africa/Kampala midnight (or later if scheduled), so a rule that
  invoices, sessions or parents were already judged under is not changed under them.

A change **never takes effect in the past** (an earlier time is refused; up to two minutes of browser
clock skew is read as "now"). Code that judges an *event* (a cancellation, an invoice) should read
`settings.value_at(key, event_time)` so a later change never re-judges it. `get(key)` is the value
in force now.

Per-setting decision on effective-from, recorded for the owner (owner question 50): everything that
bills, schedules or decides what a parent was promised is `next_day`; matching, notification,
travel and the auto-confirm window are `immediate` (the H05 acceptance criterion says a change to the
auto-confirm window must change the next job run; note that a shorter window can confirm sessions
that are already waiting).

<!-- settings:begin -->
### Business rules

| Key | Type | Allowed | Default | Unit | Environment default | Applies | Description |
|---|---|---|---|---|---|---|---|
| `session.late_cancellation_notice_hours` | int | 1 to 168 | 24 | hours | `LATE_CANCELLATION_NOTICE_HOURS` | from the next Africa/Kampala midnight (or later if scheduled) | A parent cancelling less than this long before the session start is a late cancellation (the teacher is paid). M02 question 19; not in the SR. |
| `session.auto_confirm_after_hours` | int | 1 to 720 | 48 | hours | none | from its effective time (now unless scheduled) | A session awaiting confirmation is confirmed automatically after this long without a dispute (SR 7.4). |
| `checkin.geofence_radius_m` | int | 20 to 2000 | 150 | metres | none | from its effective time (now unless scheduled) | A check-in farther than this from the home pin is flagged and needs parent confirmation (SR 7.5). Value not in the SR: owner question. |
| `billing.renewal_lead_days` | int | 1 to 30 | 5 | days | `BILLING_RENEWAL_LEAD_DAYS` | from the next Africa/Kampala midnight (or later if scheduled) | The next period's invoice is created this long before renewal. |
| `billing.grace_days` | int | 0 to 30 | 3 | days | `BILLING_GRACE_DAYS` | from the next Africa/Kampala midnight (or later if scheduled) | Days after the due date before an unpaid subscription becomes past due (SR 8). |
| `billing.past_due_cancel_days` | int | 1 to 90 | 14 | days | `BILLING_PAST_DUE_CANCEL_DAYS` | from the next Africa/Kampala midnight (or later if scheduled) | A subscription past due for this long is cancelled. Must be longer than the grace period. |
| `billing.max_pause_days` | int | 1 to 180 | 30 | days | `BILLING_MAX_PAUSE_DAYS` | from the next Africa/Kampala midnight (or later if scheduled) | Longest a parent may pause a subscription (SUB-4). |
| `scheduling.horizon_days` | int | 7 to 90 | 28 | days | `SESSION_GEN_HORIZON_DAYS` | from the next Africa/Kampala midnight (or later if scheduled) | How far ahead recurring sessions are generated (S01). |

### Matching

| Key | Type | Allowed | Default | Unit | Environment default | Applies | Description |
|---|---|---|---|---|---|---|---|
| `matching.weights` | map | `travel_time` 0 to 100; `tier` 0 to 100; `struggle_fit` 0 to 100; `workload_balance` 0 to 100; `reliability` 0 to 100; `cluster_bonus` 0 to 100; sum = 100 | travel_time=30, tier=20, struggle_fit=15, workload_balance=15, reliability=15, cluster_bonus=5 | percent | none | from its effective time (now unless scheduled) | Weights of the matching score, in percent. They must add up to 100 (SR 7.2). |
| `matching.offer_expiry_minutes` | int | 5 to 1440 | 120 | minutes | none | from its effective time (now unless scheduled) | How long a teacher has to answer an offer (SR 7.2: default 2 hours). |
| `matching.substitute_offer_expiry_minutes` | int | 5 to 720 | 30 | minutes | none | from its effective time (now unless scheduled) | How long a teacher has to answer a substitute offer. Not longer than a normal offer. |
| `matching.max_offer_failures` | int | 1 to 20 | 5 | offers | none | from its effective time (now unless scheduled) | Declined or expired offers before a request moves to the admin queue (SR 7.2: 5). |
| `matching.max_rematches` | int | 0 to 10 | 2 | rematches | none | from the next Africa/Kampala midnight (or later if scheduled) | Rematches a parent may ask for per subscription. Value not in the SR: owner question. |

### Notifications

| Key | Type | Allowed | Default | Unit | Environment default | Applies | Description |
|---|---|---|---|---|---|---|---|
| `notify.quiet_hours` | map | `start_hour` 0 to 23; `end_hour` 0 to 23 | start_hour=21, end_hour=7 | hour of day | `NOTIFY_QUIET_START_HOUR`, `NOTIFY_QUIET_END_HOUR` | from its effective time (now unless scheduled) | Non-critical notifications wait outside these Africa/Kampala hours (SR 8: 21:00-07:00). |
| `notify.rate_limit` | int | 1 to 100 | 5 | notifications | `NOTIFY_RATE_LIMIT` | from its effective time (now unless scheduled) | Non-critical notifications per recipient and template per window. |
| `notify.rate_window_s` | int | 10 to 86400 | 600 | seconds | `NOTIFY_RATE_WINDOW_S` | from its effective time (now unless scheduled) | The window of the notification rate limit. |
| `notify.max_attempts` | int | 1 to 10 | 4 | attempts | `NOTIFY_MAX_ATTEMPTS` | from its effective time (now unless scheduled) | Provider attempts before a notification fails for good. |
| `notify.escalate_after_min` | int | 1 to 240 | 15 | minutes | `NOTIFY_ESCALATE_AFTER_MIN` | from its effective time (now unless scheduled) | A safeguarding notification nobody acknowledged is escalated to the backup after this. |

### Travel time

| Key | Type | Allowed | Default | Unit | Environment default | Applies | Description |
|---|---|---|---|---|---|---|---|
| `travel.daily_element_budget` | int | 1 to 1000000 | 1000 | elements | `TRAVEL_DAILY_ELEMENT_BUDGET` | from its effective time (now unless scheduled) | Most route elements (origin x destination) the provider may be asked for per day. |
| `travel.monthly_element_budget` | int | 1 to 10000000 | 15000 | elements | `TRAVEL_MONTHLY_ELEMENT_BUDGET` | from its effective time (now unless scheduled) | Most route elements per month; alerts at 80 and 100 percent, hard stop at 100. |
| `travel.cache_ttl_days` | int | 1 to 90 | 7 | days | `TRAVEL_CACHE_TTL_DAYS` | from its effective time (now unless scheduled) | How long a zone-pair travel time is cached before it is refreshed (SR 7.2: weekly). |
| `travel.within_zone_minutes` | int | 1 to 120 | 10 | minutes | `TRAVEL_WITHIN_ZONE_MINUTES` | from its effective time (now unless scheduled) | Assumed travel time between two homes in the same zone. |
| `travel.fallback_speed_kmh` | float | 5 to 120 | 20.0 | km/h | `TRAVEL_FALLBACK_SPEED_KMH` | from its effective time (now unless scheduled) | Average speed of the straight-line estimate used when no provider answer exists. |
| `travel.fallback_circuity` | float | 1 to 3 | 1.4 | factor | `TRAVEL_FALLBACK_CIRCUITY` | from its effective time (now unless scheduled) | Road-distance factor of the straight-line estimate (1.0 = as the crow flies). |

### Rules across settings

- `grace_before_cancel`: billing.grace_days must be shorter than billing.past_due_cancel_days (keys: `billing.grace_days`, `billing.past_due_cancel_days`)
- `substitute_not_longer`: the substitute offer window must not be longer than the normal offer window (keys: `matching.substitute_offer_expiry_minutes`, `matching.offer_expiry_minutes`)
- `daily_within_monthly`: the daily travel budget must not exceed the monthly budget (keys: `travel.daily_element_budget`, `travel.monthly_element_budget`)
<!-- settings:end -->

## Validation

Strict and never coercing: a JSON boolean is not a number, `"24"` is not 24, 24.5 is not a whole
number, NaN and infinity are refused, a map has exactly its declared fields. Bounds are per setting
(negative or zero windows are refused). Cross-setting rules (table above) are checked at **every
instant from now on at which one of the involved settings changes**, including scheduled changes of
other settings, so no sequence of edits can leave a rule broken. Matching weights and quiet hours are
single settings of type `map` precisely so they are edited and validated together (weights must add
up to 100). A value that equals the current one is refused (`no_change`).

If a stored value is ever invalid (someone edited the table directly) the reader falls back to the
**default** for that setting (and for every setting of a broken cross-rule) and raises an alert
(`settings_stored_value_invalid`, `settings_constraint_violated`); it never falls back to zero or
infinity.

## Reading (runtime)

`request.app.state.app_settings.get("session.auto_confirm_after_hours")`. Each core instance caches
the value rows for `SETTINGS_CACHE_TTL_S` (default 30 s): the instance that took the edit sees it at
once, the others within the TTL. A scheduled change switches at its exact effective time without
waiting for the activation job. If the store is down: a warm instance keeps serving its last good
values (not the defaults: flipping live rules during an outage would be worse), a cold one serves the
defaults; the store is retried at most every 5 s. Already wired: the notification service (quiet hours,
rate limit and window, attempts, escalation) and the travel service (budgets, cache TTL, within-zone
minutes, fallback speed and circuity) read their values through `LiveNotificationConfig` and
`LiveTravelConfig`. The late-cancellation notice, auto-confirm window, billing, scheduling, geofence and
matching values are registered and editable now; the services that consume them (M02 cancellation
endpoint, auto-confirm job, B03, S01, X03, VRF) must read them through `get`/`value_at` when they
land (each ticket's follow-up list).

## Changing (admin API)

`GET /v1/admin/settings`, `PUT /v1/admin/settings/{key}` (`value`, `reason`, `revision`,
optional `effective_at`), `POST .../{key}/rollback`, `POST .../{key}/cancel-pending`,
`GET .../{key}/history`. **Super admin only**, for reading too (owner question 48: may the ops admin
read?). Unknown fields are refused (422), bodies are capped, the actor comes from the verified JWT
(`Depends(audit_context)`), the audit key from the server request id, never from the client.

- **Optimistic concurrency:** `revision` is the number of changes made so far (0 = never changed).
  A stale revision is 409 `revision_conflict` with the current one; nothing is written.
- **Reason:** required (at least 3 visible characters, at most 2,000, stored scrubbed and capped at 500).
- **One scheduled change per setting.** Another change while one waits is 409 `change_pending`; cancel
  it first (history keeps it).
- **History:** `app_setting_change`, one row per change (op `set`, `rollback`, `cancel`), never updated.

### Write order and crash behaviour

1. audit intent, then create the **history row** for revision N+1. This is the commit and the
   concurrency claim: the row id is derived from (setting, revision), so of any number of editors who
   read revision N exactly one create succeeds (Appwrite has no conditional update). The brief listed
   "value row, then history"; the order is reversed on purpose because only the history row can be a claim.
2. audit intent, then update (or create) the **value row**, a projection of the history.

A crash after 1 and before 2 leaves history ahead of the value row. Readers keep the old value until
the next edit of that setting or the `settings-activate` job (every minute, idempotent, F04) re-projects
it from the history. A crash after the audit intent and before 1 leaves an intent that `audit-recover`
marks `not_applied`. Tested by crashing before and after every write of an edit.

## Limits (not hidden)

- The in-process lock per setting plus the history claim give one winner per revision across
  instances; two instances projecting the same setting at the same instant from slightly different
  history views could write an older projection, which the next `settings-activate` run (at most a minute
  later) corrects.
- `app_setting_change` is create-only through the audited repository; the Appwrite server key could
  still change a row. Protection is wiring and convention plus the M06 tripwire, not enforcement.
- Plans and prices, tiers and rates, bonus rules, zones and clusters (map editor, hard-to-fill flag),
  notification templates, policy versions and payment providers (the other A-50 sections) are rows of their
  own tables owned by other tickets; the A-50 screen built here covers the business-rule settings and links
  nothing it cannot change. See memory.md H05 "Not built".
