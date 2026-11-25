# Audit logging (M06, #41; SEC-6, ADM-6, ADR 0012)

Appwrite has no database triggers, so the audit trail lives in the core service's data layer (`services/core/src/hs_core/audit/`). `audit_log` is defined in `appwrite/schema/platform.json` (M04). Nothing here deploys anything or touches the live project.

## What is audited

Every create, update and delete of these tables, made through the app's repository (`app.state.repository`, an `AuditedRepository`):

money `invoice`, `payment`, `earning`, `payout`, `payout_batch`, `ledger_account`, `ledger_transaction`, `ledger_entry`; tiers `tier`, `tier_history`, `bonus_rule`; plans and sessions `plan`, `subscription`, `assignment`, `session`, `session_checkin`, `session_confirmation`, `cancellation`, `dispute`; logs `session_log`, `session_log_topic`, `session_log_struggle`; safeguarding `safeguarding_case`, `safeguarding_action`, `case_status`; settings, roles and approvals `app_setting` (table not built yet; the name is reserved), `role_assignment`, `user_profile`, `teacher_profile`, `teacher_approval`, `teacher_document`.

Admin actions that are not a table write (an Appwrite label change, an export) use `AuditService.run_change(... op="action", apply=...)` or `AuditService.record(...)`. The set lives in `audit/registry.py`; a ticket that adds an audited table edits that file (and a schema-driven test forces a redaction decision for every free-text column).

`ledger_entry`, `ledger_transaction`, `safeguarding_action` (and `audit_log`) are also append-only: the wrapper creates (audited) but refuses update and delete (`AppendOnlyViolation`). `audit_log` is written only by `AuditService`, only through `AppendOnlyRepository`.

## Protocol

**Audit-first, with an intent row and an outcome row only when something went wrong.** Chosen, with the alternative rejected:

1. Build the context: actor and roles from the verified JWT (`current_principal`), request id from the middleware, IP from the trusted client-IP helper A01 uses (`Fly-Client-IP` outside dev), or the F04 job context for a job (`actor_id` null, `actor_roles` `system`, job name in the row).
2. Check the reason registry (below). A missing or blank reason raises `ReasonRequired` before anything is written.
3. Take an in-process lock for the entity, read its current state (the before state), and create the **intent row** with a deterministic id (hash of the SERVER-side request key or job key, entity, entity id, n-th change in this context; see "Keys and collisions"). The id is the atomic claim (Appwrite 409): a retry of the same change does not add a row. If this write fails, the business write is not attempted (`AuditUnavailable`, fail closed).
4. Run the business write.
5. If it raises an error that PROVES nothing was written (`Conflict` on create, `NotFound`, or an exception deriving from `NotWritten`, which callers raise for validation or preconditions failing before the repository call), write a `failed` **outcome row** that points at the intent (`meta.ref`) and carries the exception class name only; re-raise. Every other error is **ambiguous** (timeout, connection error, `RepositoryUnavailable`, 5xx, unknown exception: the server may have applied the write before the error): no `failed` row is written, the metric `ambiguous_error` is counted, the intent stays live and `audit-recover` settles it from the entity's state hash. A `failed` row therefore never contradicts a change that landed.
6. If the process dies anywhere, `audit-recover` settles it (next section).

Why audit-first, not business-then-audit with an outbox: with business-first, a crash between the two writes leaves a money or safeguarding change with no audit row until a recovery job notices, and noticing means scanning the business tables. Audit-first makes the invariant simple: **a business change never exists without an intent row** (the row exists before the change). The price is that an intent row can describe a change that never landed; that is exactly what the outcome rows and the recovery job fix, and no extra table or outbox is needed. The happy path is exactly one row per logical change (one intent, no outcome row); rows are append-only, so a failure is a second row, not an edit.

Row layout (existing `audit_log` columns only, no schema change): `before`/`after` hold a JSON envelope `{v, fields, hash, truncated}`; `after` also holds `meta` `{status, source, seq, op, key, before_state, after_state, job?, ref?, error?}`. `status` is `intent` (a change), `recorded` (a read or action with no business write), `failed` or `not_applied` (outcome rows). `source` is `user`, `admin` or `system`. `seq` is the per-entity sequence. A change is "live" when it has an intent row and no outcome row.

Retry rules: the same logical change (same server request key or job run key, same actor, entity, action and intended after-state) replays the same intent (no second row) and the business write is applied idempotently (a create that already landed returns the row, a delete of a deleted row is done). After a recorded `failed`/`not_applied` outcome, the next attempt is a new intent. Two different changes to one entity in one request get two rows (counter).

## Keys and collisions

The dedupe key must never be chosen by the client. `main.py` generates a server request key (uuid4) per request (`audit.context.server_request_key_var`) and `AuditContext.for_principal` uses it as `key_base`; the client's `X-Request-ID` (sanitised, as before) is only the correlation field stored in `request_id`. Jobs use the F04 effect key, which is deterministic per run chunk. In addition the key is bound to the content: a row found under our key is a replay only if actor, entity, action, op and the hash of the intended after-state all equal ours. Otherwise it is a **key collision**: a new row is written under a suffixed id (`~k1`, `~k2`...), flagged `meta.key_collision: true`, counted (`AuditService.metrics['key_collision']`) and sent to the alert hook (`audit.key_collision`, wired to the job `AlertSink`). Consequences: a fixed or spoofed request id cannot suppress an audit row (two updates, or two users reading one case, under one id give two rows); an HTTP client retrying a request produces a second row (each request is a real change attempt); only an in-process retry of the same request key (fault-injection tests) dedupes to one row. `AuditService.record_external(key, content_hash=...)` is the same rule for callers with their own deterministic key (the ledger and notification hooks below).

## Crash recovery

`audit-recover` (registered in `jobs/schedule.py`, every 10 minutes, chunked) looks at intent rows older than `AUDIT_RECOVER_GRACE_S` (300) and younger than `AUDIT_RECOVER_LOOKBACK_HOURS` (6) that have no outcome row, reads the entity and compares its redacted-state hash with the intent's `before_state` and `after_state`:

- equals after: the change landed, nothing to write;
- equals before: the change never landed, write a `not_applied` row (fixed id, idempotent, actor `system`, job `audit-recover`);
- neither, or not comparable: counted as **unverifiable**, logged for review, nothing written and nothing guessed. Not comparable means: non-table actions (`op` is `action`), updates whose only changes are redacted fields (the hash of a redacted value does not change), no-op updates, a later change by someone else, or a datetime/default the store normalised differently from the predicted after state.

Unverifiable intents are not only logged: each one increments `AuditService.metrics['unverifiable_intent']` and calls the alert hook (`audit.unverifiable_intent`, with the intent id), and the sweep report (`recover.report`) lists their ids. They keep being reported on every sweep until they leave the lookback window (an intent older than the window is neither counted nor alerted: widen the window with the payload below to review it).

A manual run can widen the window with the job payload `{"lookback_hours": 72}`. Fault injection after every write of create, update and delete, in both orders (recover then retry, retry then recover), is in `tests/test_audit_recovery.py`.

## Redaction

Applied before anything is hashed, stored or logged (`audit/payload.py`, rules in `audit/registry.py`):

- by key: credentials (password, token, secret, API key, OTP code, salt, code hash), full phone numbers and e-mail, home coordinates (`lat`, `lng`), address text and directions, document files (`file_id`), raw provider payloads (`raw_callback`), person names (`name`, `*_name`; bare `name` is kept only on label tables such as `tier`), answer keys (decision: **never logged**, an exam-integrity secret), plus the per-entity table below;
- by value: every other string goes through the F08 scrubber (`scrub_text`: phone numbers, e-mails, bearer tokens, JWTs, `key=value` secrets);
- round 2 decisions on generic keys, redacted wherever they occur (any depth): `coordinates`, `location`, `pickup`, `pin`, `pan` (card PAN), `nin`, `passport`, `guardian`, `comment(s)`, `note(s)`, `description`, and joined forms containing `nationalid`, `cardpan`, `passportnumber`. A key that ends in `id` (`home_location_id`) is a foreign key and is kept. `title` is **kept** (catalogue text: topics, plans, questions; passes the scrubber). `mock_location` (a boolean fraud flag) is redacted with the `location` rule: the log shows that it changed, not to what. Explicit exceptions are `TEXT_ALLOWED` (`ledger_transaction.description`, written by the ledger service). `subscription.cancel_reason` (parent free text), `assignment.end_reason` and `earning.note` (staff or parent free text) are redacted per entity: the structured status column carries the audit-relevant fact;
- a redacted field that changed still appears as changed, with `[redacted]` instead of the value; rows carry ids, not names.

Per-entity rules (`ENTITY_REDACT`): `user_profile` (name, photo), `teacher_profile` (bio, photo file), `teacher_document` (file, notes, number), `teacher_approval` (notes), `payment` (raw callback, failure reason), `payout` (account number, destination, last error), `session_log` (note, notes, homework), `safeguarding_case` (description, outcome, summary, narrative, notes), `safeguarding_action` (detail, notes), `case_status` (summary, message), `dispute` (reason, resolution note), `cancellation` (reason), `role_assignment` (revoke reason). Free text that is deliberately kept (staff reasons, tier criteria, bonus conditions) is the allow-list `TEXT_ALLOWED`; a schema-driven test fails when an audited table gets a new free-text column nobody decided about.

Size: `before` holds only the changed fields' old values and `after` the new ones (a minimal diff) with a SHA-256 `hash` of the full pre-truncation fields. Each column is cut to 15,000 characters (the column is about 16k): long strings are shortened, then values become their size, then only field names remain, always valid JSON with `"truncated": true`. Unicode is stored unescaped and counted in characters.

## Reasons

Actions that require a reason (`REASON_REQUIRED`): `dispute.resolve`, `tier.override`, `payment.refund`, `user.suspend`, `payout.approve`, `ledger.manual_adjustment`, `setting.update`, `app_setting.create`, `app_setting.update`, `app_setting.delete`. Pass the action name with `action=` and the text with `reason=`; the reason is normalised (NFKC), every format, control and space character is stripped (categories Cf, Cc, Cs, Co, Cn, Zs, Zl, Zp, plus the blank-looking U+2800, U+3164, U+FFA0, U+115F/1160, U+034F, U+180E, U+061C, and combining marks with no visible base), at least `MIN_REASON_CHARS` (3) visible characters must remain or `ReasonRequired` is raised, the text is scrubbed and cut to the 500-character column; a cut is recorded as `meta.reason_truncated` with `meta.reason_chars` (the length before the cut). Zero-width joiners are removed, which also drops them from scripts that use them (for example Persian ZWNJ): a reason is a note for an auditor, not typeset text. Other actions take an optional reason (cleaned the same way). Add to the registry when a new UI requires a reason.

## Reads of safeguarding cases

`read_audited(repo, ctx, table, id, purpose=...)` and `list_audited(...)` (in `audit/reads.py`) write a `safeguarding_case.read` / `.list` row (ids only, never content) **before** returning the data; if the audit store is down the case is not shown. `AuditedRepository.get/list` refuse `safeguarding_case` and `safeguarding_action` (`UnauditedRead`). The later G tickets must read cases only through these helpers; case detail is served by a core-service endpoint, not read from Appwrite by a client.

## Read side

`list_audit(repo, roles=..., entity=, entity_id=, actor_id=, since=, until=, cursor=, limit=, audit=service, ctx=ctx)` in `audit/query.py` (no UI: H08 builds it). **Decision: reading the audit log is itself audited.** H08 passes `audit` and `ctx`; one `audit.read` row (filter names and row count only, no values or ids) is written before the page is returned, so an unavailable audit store means no page. A forbidden caller writes nothing. Visibility: `opsadmin` and `superadmin` always; `safeguardinglead` while `AUDIT_LEAD_BLANKET_READ` is true (owner question 12, default true, the ADR 0012 deviation; false removes the lead and the lead's per-row read grant on new rows); nobody else. Pages are cursor-based (`limit` 1 to 100, store asked for `limit + 1`), newest first; entity and actor cannot be combined (no index for the pair). Rows are created with per-row read grants for opsadmin, superadmin and (flag) safeguardinglead and no write grants.

## Admin-UI events (the admin app's audit path)

`POST /v1/admin/audit/event` (`audit/admin_events.py`) is how the admin app (a Next.js server with no Appwrite API key) records events that are not table writes. Today one event is whitelisted, `admin.search` (H09, SEC-6/ADM-6: a search that can return personal data is an admin action). The caller sends its own short-lived Appwrite JWT; the core service verifies it (`current_principal`, same as every endpoint) and requires an admin role (opsadmin, curator, safeguardinglead, superadmin). Actor, roles, IP (trusted client-IP helper) and request id come from the server; the body is a strict model (`extra=forbid`): `event`, `entity_types` (the fixed list of search types), `query_kind` (text, phone, date, reference) and per-type `results` counts (0 to 10,000). There is no field for query text, result rows or ids; anything else is a 422 and nothing is written. If the row cannot be written the answer is 503 (`audit_unavailable`).

Admin side (`apps/admin/src/lib/search/audit-sink.ts`): `CoreAuditSink` posts the entry to `CORE_API_URL` with `Authorization: Bearer <jwt>`; the JWT is created from the signed-in admin's own session (`POST {APPWRITE_ENDPOINT}/account/jwts` with `X-Appwrite-Session`, no API key; cached per request, valid 15 minutes). `UnwiredAuditSink` is deleted. In **staging and production** `/api/search` answers 503 and reads nothing when `CORE_API_URL` is unset, not a URL, or not https (`SearchAuditUnwired`); a failing audit call also gives 503 and no results (fail closed). Local development (`ADMIN_ENV` unset or unknown) may run with an in-memory fake that persists nothing, or with `http://localhost` as the core URL. The route, the audit-sink and the deployed refusal are covered by `search-audit-sink.test.ts` and `search-route-deployed.test.ts`; the core side by `tests/test_audit_admin_events.py`. NOT verified against a real Appwrite project (the `/account/jwts` call and the core JWT check are only mock-tested): staging check REQUIRED before relying on it (see memory.md "Blocked on owner").

## Hooks from other tickets (status)

| Hook | Where it lives | Status |
|---|---|---|
| H09 admin search `AuditSink` | `apps/admin/src/lib/search/audit-sink.ts` (merged with this ticket) | DELIVERED (core endpoint, real sink, `UnwiredAuditSink` removed, deployed refusal). Staging verification outstanding. |
| $01 ledger `AuditSink` (`hs_core/ledger/audit.py`, key `ledger:<tx id>`) | `audit/adapters.py` `LedgerAuditAdapter(app.state.audit)` implements `record(event)` | ADAPTER DELIVERED and tested against the event shape on branch `s01-ledger` (key bound to a hash of the transaction content: same key and content is a replay, same key with other content or actor is a collision, not a dedupe). WIRING is a follow-up for $01 when it merges: pass `audit=LedgerAuditAdapter(app.state.audit)` where `LedgerService` is constructed (it defaults to `NullAuditSink`), and add `ledger_transaction` rows to the `$01` factory test. REQUIRED before staging: until then ledger postings are not audited. |
| N01 `TemplateStore.audit` (`hs_core/notifications/templates.py`, `publish`) | `audit/adapters.py` `TemplateAuditAdapter(app.state.audit)` is the callable | ADAPTER DELIVERED and tested against the payload on branch `n01-notification-service` (key `template:<key>:<channel>:<locale>:<version>`). WIRING is a follow-up for N01 when it merges: construct `TemplateStore(..., audit=TemplateAuditAdapter(app.state.audit))`. REQUIRED before staging: until then template publishes are not audited. |

## Tables that are NOT audited (decision)

SEC-6 asks for "all changes to money, tiers, sessions, logs and safeguarding cases". Not audited, on purpose: `child` and `consent_record` (personal and consent data: SEC-6 does not list them and an audit row of a child's change would copy personal data into a second table; consent rows carry their own `decision`, `version`, `accepted_by_id` and `accepted_at` columns (`appwrite/schema/core.json`); **revisit** when D06/C04 define PDPO evidence needs), `match_*` (generated matching scores and candidates, recomputed, no money or safeguarding effect), `notification_template` (see N01: versions are insert-only and the publish is audited through the hook above), `notification`/delivery rows (system-generated, high volume), `travel_*` cache tables, OTP and job bookkeeping tables (`otp_*`, `job_run`), `curriculum`/catalogue tables (`topic`, `question`: curator content, versioned by the curriculum tickets), `location` and zone tables (reference data). A ticket that makes one of these a money, tier, session, log or safeguarding concern adds it to `registry.AUDITED_TABLES`.

## Settings

`AUDIT_LEAD_BLANKET_READ` (true), `AUDIT_RECOVER_LOOKBACK_HOURS` (6), `AUDIT_RECOVER_GRACE_S` (300). In `services/core/.env.example` and the F09 inventory.

## Limits

Read these before relying on the log:

- **Console bypass.** A change made directly in the Appwrite console, or with another API key, does not go through the core service and writes no audit row. Mitigation is organisational: console access to production is limited to super admins and reviewed. The `permission-audit` job (ADR 0012) and the recovery job do not detect console edits.
- **Wiring, not enforcement.** `app.state.repository` is the audited wrapper, but code that builds its own raw repository, or passes the raw one around, can still write. Nothing in Appwrite stops the server API key.
- **Sequence under more than one instance.** `seq` is per entity, computed under an in-process lock. With more than one core instance, two instances can pick the same `seq` for concurrent changes to one entity (the rows are still both written, ordered by `occurred_at`); Appwrite has no conditional update, so the business writes themselves are last-writer-wins today.
- **Recovery is bounded.** It only sees intents inside the lookback window and compares hashes of a predicted after state; unverifiable cases are reported, never resolved automatically. Cost is about two reads per intent per sweep.
- **Retry replay.** A retried request re-applies its write; a very late retry could overwrite a newer change (same as any non-conditional update).
- **Collision rows.** A key collision is recorded and alerted, but the log cannot tell a hostile caller from two programming mistakes sharing a key; an operator reads the alert.
- **Created-then-conflicting creates.** `Conflict` on create is treated as proof that nothing was written by this call; a driver that retries a timed-out create internally could turn a landed create into a 409. The row exists either way and the intent stays, so the audit shows the change, but the `failed` outcome would then be wrong: verify with the Appwrite adapter on staging.
- **Redaction is by key and pattern.** A person's name typed into a free-text value that is not redacted by key cannot be found reliably; the rules are therefore keyed on free-text columns, and rows carry ids.

## Tripwires

Two AST scans (`audit/tripwire.py`) are **tripwires for literal table names, not coverage**, in the same spirit as the M04 append-only scan:

- `find_unaudited_writes(root)` flags a `create`/`update`/`delete` (and the SDK write-style methods) with an audited table written as a literal, or as a name bound anywhere in the same file to a literal, unless the receiver's name contains `audited` (the convention for variables holding an `AuditedRepository`). It runs over `src/hs_core` in the test suite.
- `find_unaudited_case_reads(root)` flags `get`/`list`/`get_row`/`list_rows` of `safeguarding_case` or `safeguarding_action` outside the audit package.

They **miss**: table names that are computed (f-strings, concatenation), passed as a function parameter, kept in a class attribute or container constant, supplied through `*args`/`**kwargs` splats, reached through `getattr`, a bound-method alias or dict dispatch; a raw repository stored in a variable called `audited...`; code outside the scanned tree. The run-time checks (context-free writes and plain case reads raise) are opt-in: they protect code that holds the wrapper. Review rule: a new data-layer module takes the audited repository, never the raw one. Mutation tests (`tests/test_audit_mutations.py`) make the suite fail if the update path skips the audit, a retry double-writes, secrets are logged, a failed change is not marked, the reason is not enforced, the audit comes after the write, truncation is dropped, or recovery does nothing, a client-chosen key suppresses a different change, an ambiguous error is marked failed, or an invisible-character reason is accepted.
