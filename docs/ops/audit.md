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
3. Take an in-process lock for the entity, read its current state (the before state), and create the **intent row** with a deterministic id (hash of request or job key, entity, entity id, n-th change in this context). The id is the atomic claim (Appwrite 409): a retry of the same request does not add a row. If this write fails, the business write is not attempted (`AuditUnavailable`, fail closed).
4. Run the business write.
5. If it raises an ordinary error, write a `failed` **outcome row** that points at the intent (`meta.ref`) and carries the exception class name only; re-raise. The log then shows the intent and the failure; it never silently implies success.
6. If the process dies anywhere, `audit-recover` settles it (next section).

Why audit-first, not business-then-audit with an outbox: with business-first, a crash between the two writes leaves a money or safeguarding change with no audit row until a recovery job notices, and noticing means scanning the business tables. Audit-first makes the invariant simple: **a business change never exists without an intent row** (the row exists before the change). The price is that an intent row can describe a change that never landed; that is exactly what the outcome rows and the recovery job fix, and no extra table or outbox is needed. The happy path is exactly one row per logical change (one intent, no outcome row); rows are append-only, so a failure is a second row, not an edit.

Row layout (existing `audit_log` columns only, no schema change): `before`/`after` hold a JSON envelope `{v, fields, hash, truncated}`; `after` also holds `meta` `{status, source, seq, op, key, before_state, after_state, job?, ref?, error?}`. `status` is `intent` (a change), `recorded` (a read or action with no business write), `failed` or `not_applied` (outcome rows). `source` is `user`, `admin` or `system`. `seq` is the per-entity sequence. A change is "live" when it has an intent row and no outcome row.

Retry rules: the same request id replays the same intent (no second row) and the business write is applied idempotently (a create that already landed returns the row, a delete of a deleted row is done). After a recorded `failed`/`not_applied` outcome, the next attempt is a new intent. Two different changes to one entity in one request get two rows (counter).

## Crash recovery

`audit-recover` (registered in `jobs/schedule.py`, every 10 minutes, chunked) looks at intent rows older than `AUDIT_RECOVER_GRACE_S` (300) and younger than `AUDIT_RECOVER_LOOKBACK_HOURS` (6) that have no outcome row, reads the entity and compares its redacted-state hash with the intent's `before_state` and `after_state`:

- equals after: the change landed, nothing to write;
- equals before: the change never landed, write a `not_applied` row (fixed id, idempotent, actor `system`, job `audit-recover`);
- neither, or not comparable: counted as **unverifiable**, logged for review, nothing written and nothing guessed. Not comparable means: non-table actions (`op` is `action`), updates whose only changes are redacted fields (the hash of a redacted value does not change), no-op updates, a later change by someone else, or a datetime/default the store normalised differently from the predicted after state.

A manual run can widen the window with the job payload `{"lookback_hours": 72}`. Fault injection after every write of create, update and delete, in both orders (recover then retry, retry then recover), is in `tests/test_audit_recovery.py`.

## Redaction

Applied before anything is hashed, stored or logged (`audit/payload.py`, rules in `audit/registry.py`):

- by key: credentials (password, token, secret, API key, OTP code, salt, code hash), full phone numbers and e-mail, home coordinates (`lat`, `lng`), address text and directions, document files (`file_id`), raw provider payloads (`raw_callback`), person names (`name`, `*_name`; bare `name` is kept only on label tables such as `tier`), answer keys (decision: **never logged**, an exam-integrity secret), plus the per-entity table below;
- by value: every other string goes through the F08 scrubber (`scrub_text`: phone numbers, e-mails, bearer tokens, JWTs, `key=value` secrets);
- a redacted field that changed still appears as changed, with `[redacted]` instead of the value; rows carry ids, not names.

Per-entity rules (`ENTITY_REDACT`): `user_profile` (name, photo), `teacher_profile` (bio, photo file), `teacher_document` (file, notes, number), `teacher_approval` (notes), `payment` (raw callback, failure reason), `payout` (account number, destination, last error), `session_log` (note, notes, homework), `safeguarding_case` (description, outcome, summary, narrative, notes), `safeguarding_action` (detail, notes), `case_status` (summary, message), `dispute` (reason, resolution note), `cancellation` (reason), `role_assignment` (revoke reason). Free text that is deliberately kept (staff reasons, tier criteria, bonus conditions) is the allow-list `TEXT_ALLOWED`; a schema-driven test fails when an audited table gets a new free-text column nobody decided about.

Size: `before` holds only the changed fields' old values and `after` the new ones (a minimal diff) with a SHA-256 `hash` of the full pre-truncation fields. Each column is cut to 15,000 characters (the column is about 16k): long strings are shortened, then values become their size, then only field names remain, always valid JSON with `"truncated": true`. Unicode is stored unescaped and counted in characters.

## Reasons

Actions that require a reason (`REASON_REQUIRED`): `dispute.resolve`, `tier.override`, `payment.refund`, `user.suspend`, `payout.approve`, `ledger.manual_adjustment`, `setting.update`, `app_setting.create`, `app_setting.update`, `app_setting.delete`. Pass the action name with `action=` and the text with `reason=`; the reason is scrubbed and cut to the 500-character column. Other actions take an optional reason. Add to the registry when a new UI requires a reason.

## Reads of safeguarding cases

`read_audited(repo, ctx, table, id, purpose=...)` and `list_audited(...)` (in `audit/reads.py`) write a `safeguarding_case.read` / `.list` row (ids only, never content) **before** returning the data; if the audit store is down the case is not shown. `AuditedRepository.get/list` refuse `safeguarding_case` and `safeguarding_action` (`UnauditedRead`). The later G tickets must read cases only through these helpers; case detail is served by a core-service endpoint, not read from Appwrite by a client.

## Read side

`list_audit(repo, roles=..., entity=, entity_id=, actor_id=, since=, until=, cursor=, limit=)` in `audit/query.py` (no UI: H08 builds it). Visibility: `opsadmin` and `superadmin` always; `safeguardinglead` while `AUDIT_LEAD_BLANKET_READ` is true (owner question 12, default true, the ADR 0012 deviation; false removes the lead and the lead's per-row read grant on new rows); nobody else. Pages are cursor-based (`limit` 1 to 100, store asked for `limit + 1`), newest first; entity and actor cannot be combined (no index for the pair). Rows are created with per-row read grants for opsadmin, superadmin and (flag) safeguardinglead and no write grants.

## Settings

`AUDIT_LEAD_BLANKET_READ` (true), `AUDIT_RECOVER_LOOKBACK_HOURS` (6), `AUDIT_RECOVER_GRACE_S` (300). In `services/core/.env.example` and the F09 inventory.

## Limits

Read these before relying on the log:

- **Console bypass.** A change made directly in the Appwrite console, or with another API key, does not go through the core service and writes no audit row. Mitigation is organisational: console access to production is limited to super admins and reviewed. The `permission-audit` job (ADR 0012) and the recovery job do not detect console edits.
- **Wiring, not enforcement.** `app.state.repository` is the audited wrapper, but code that builds its own raw repository, or passes the raw one around, can still write. Nothing in Appwrite stops the server API key.
- **Sequence under more than one instance.** `seq` is per entity, computed under an in-process lock. With more than one core instance, two instances can pick the same `seq` for concurrent changes to one entity (the rows are still both written, ordered by `occurred_at`); Appwrite has no conditional update, so the business writes themselves are last-writer-wins today.
- **Recovery is bounded.** It only sees intents inside the lookback window and compares hashes of a predicted after state; unverifiable cases are reported, never resolved automatically. Cost is about two reads per intent per sweep.
- **Retry replay.** A retried request re-applies its write; a very late retry could overwrite a newer change (same as any non-conditional update).
- **Redaction is by key and pattern.** A person's name typed into a free-text value that is not redacted by key cannot be found reliably; the rules are therefore keyed on free-text columns, and rows carry ids.

## Tripwires

Two AST scans (`audit/tripwire.py`) are **tripwires for literal table names, not coverage**, in the same spirit as the M04 append-only scan:

- `find_unaudited_writes(root)` flags a `create`/`update`/`delete` (and the SDK write-style methods) with an audited table written as a literal, or as a name bound anywhere in the same file to a literal, unless the receiver's name contains `audited` (the convention for variables holding an `AuditedRepository`). It runs over `src/hs_core` in the test suite.
- `find_unaudited_case_reads(root)` flags `get`/`list`/`get_row`/`list_rows` of `safeguarding_case` or `safeguarding_action` outside the audit package.

They **miss**: table names that are computed (f-strings, concatenation), passed as a function parameter, kept in a class attribute or container constant, supplied through `*args`/`**kwargs` splats, reached through `getattr`, a bound-method alias or dict dispatch; a raw repository stored in a variable called `audited...`; code outside the scanned tree. The run-time checks (context-free writes and plain case reads raise) are opt-in: they protect code that holds the wrapper. Review rule: a new data-layer module takes the audited repository, never the raw one. Mutation tests (`tests/test_audit_mutations.py`) make the suite fail if the update path skips the audit, a retry double-writes, secrets are logged, a failed change is not marked, the reason is not enforced, the audit comes after the write, truncation is dropped, or recovery does nothing.
