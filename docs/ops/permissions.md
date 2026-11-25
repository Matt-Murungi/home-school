# Row and file permissions (M05, ADR 0012, SR 6.2)

Clients (the apps and the admin dashboard) hold **read** permission only, on specific rows and files. The core service
writes everything and is the only writer of permissions. This page says what the rules are, how they are applied, what
runs on a schedule, how to check for drift, and what has not been verified against a real Appwrite project.

Code: `services/core/src/hs_core/permissions/`. Decisions per table: `rules.SPECS`. Tests: `services/core/tests/test_perm_*.py`.

## How it works

1. `desired_permissions(table, row, context)` (`rules.py`) is a pure function: the exact list of permission strings the
   row must carry now. It reads business facts (assignments, substitute sessions, cases, guardians, account status and
   labels) through `PermissionContext` (`context.py`). Instants are UTC; Africa/Kampala is for display only.
2. `PermissionService` (`service.py`) recomputes the rows an event can affect and writes only the difference
   (`repo.update(table, id, {}, permissions=[...])`: the whole list is replaced, no column changes, so the append-only
   tables only ever get their list rewritten). Applying twice is a no-op.
3. Order for a business change (`PermissionService.run`, ADR 0012): enqueue a `permission-reconcile` job for the event,
   write the business change, recompute and write permissions. A failing step 3 is retried by the job (about a minute,
   backoff after that); a failing step 2 leaves nothing to grant.
4. Three jobs in `jobs/schedule.py`: `address-revoke` (every 5 minutes), `permission-reconcile` (nightly, 02:00
   Kampala), `permission-audit` (daily, 03:00 Kampala). All recompute from state, run in chunks and are safe to repeat.

Events (`service.py` constructors): assignment started or ended, substitute cover, case created, assigned or closed,
role (label) change, guardian added or removed, account deactivated or deleted, a row created, catalogue changed.

## Rules in short

- Labels are exact, lower case and alphanumeric: `parent`, `guardian`, `teacher`, `opsadmin`, `curator`,
  `safeguardinglead`, `superadmin`. `ops_admin`, `OpsAdmin`, `opsadmin ` grant nothing. Only the core service sets labels
  (`policy.roles_to_labels` rejects anything else); Appwrite offers clients no way to set them.
- Per row: `read("user:<id>")` for individuals, `read("label:<x>")` for staff groups. `read("users")` only on published
  and retired catalogue content (curriculum, catalogue version, level, subject, strand, topic, struggle tag) and on files
  of an approved question. Never `any`.
- Role-based grants need the role's label and an `active` account. Own-account rows need only a non-deleted account (a
  suspended user keeps their own record and loses everything else). A deleted user is on no row.
- Teacher and child: access while an assignment is current or upcoming (`end_at` empty or in the future). When it ends the
  child record, logs, handover and sessions are withdrawn at once.
- Home address: from acceptance (the assignment exists) until 24 h after the last held session of an ended assignment.
  An assignment ended with `parent_cancelled`, `admin`, `teacher_withdrew` or `rematch_requested` withdraws it at once
  (`policy.IMMEDIATE_ADDRESS_REVOKE_REASONS`, owner question 27).
- Substitute: the covered session's row, the child, the address, the logs and the handover from the moment the session
  row exists until 24 h after the covered session ends; at once if that session is cancelled or voided.
- A teacher keeps `teacher_session_history` (child first name, class, Kampala date, duration) for their earnings history;
  `history.build_history_row` builds it when a log is submitted.
- Guardian: only the scopes in `guardian_link.grants` (comma separated: `children`, `logs`, `sessions`, `billing`,
  `address`, `consents`, or `all`). Empty or unknown means nothing.
- Safeguarding lead: only the rows of an OPEN case assigned to them (child, family address, logs, teacher documents) plus
  the case record itself (case, actions, status) which they keep after close (`Policy.lead_keeps_closed_case_record`).
  While a case is open the assigned lead also reads the case-linked account rows: the `user_profile` of the reporter, of
  the child's parent and of the teacher concerned, the family's `parent_profile`, the teacher's `teacher_profile`, and the
  `session` rows of the child. They are withdrawn at close (owner question 34; the admin global search H09 searches
  these tables with the lead's own session and would otherwise return nothing). Ops admin has no read on cases. Reporters read `case_status` for cases they raised, never the case.
- Union: several labels give the union of their rows and nothing more.

## Policy switches (`policy.Policy`, one line each)

| Switch | Default | Owner question |
|---|---|---|
| `audit_log_lead_read` | `True` (leads and ops read every audit row); when M06 is merged its `AUDIT_LEAD_BLANKET_READ` setting WINS: `wiring.wire_permissions` copies `settings.audit_lead_blanket_read` into the policy, so there is one switch | 12 |
| `unassigned_cases_visible_to_leads` | `False` | 28 |
| `lead_keeps_closed_case_record` | `True` | 28 |
| `address_grace`, `substitute_grace` | 24 h | |
| `reconcile_lookback` | 48 h | |

## Failure modes (ADR 0012 divergence table)

| Case | Result | Handling in code | Test |
|---|---|---|---|
| Business write fails | no grant (grants are computed from state, after the write) | the queued job finds nothing to change | `test_business_write_fails_nothing_is_granted...` |
| Grant fails after the business write | under-grant (safe) | event job retries within about a minute | `test_business_write_ok_but_grant_fails...`, `test_event_retry_job_repairs...` |
| Revoke fails after the business end | over-grant (unsafe) | retry job, `permission_over_grant` alert from the first job attempt, access window stays open | `test_business_end_ok_but_revoke_fails...`, `test_failed_revoke_is_retried...` |
| Some rows written, some not | partial | `PartialApplication`, rest retried; the diff is idempotent | `test_partial_application...` |
| Substitute or address window lapses | over-grant until the job runs | `address-revoke` finds due `access_window` rows by index | `test_address_access_is_revoked_by_the_job...` |
| Case closed, lead grant left | over-grant | recompute from `safeguarding_case`; audit finds leftovers | `test_closing_a_case...` |
| Two writers at once | last write wins | every run recomputes from fresh state | `test_concurrent_recompute...` |
| Worker killed mid-run | half done | next run completes (F04 lease takeover) | `test_killing_the_worker_midway...` |

## Malformed rows, stale writers, event ids

- A row the rules cannot evaluate (for example a user id that is not a valid Appwrite id) is skipped, counted
  (`ApplyReport.malformed`, `AuditSummary.malformed`), reported by table and row id only (never content), raised as a
  `permission_malformed_row` / `permission_drift` alert, and never stops the rest of the run. The audit is not green while any exist.
- Stale concurrent writers: the event path writes inline without a lock, so a stale recompute can re-grant an entry
  that a newer one just revoked. The exposure is bounded by that event's retry job (about 60 s) and the nightly reconcile;
  the audit reports it meanwhile (ADR 0012 accepts last writer wins with recompute-from-state).
- `PermissionEvent.event_id` must be deterministic per business operation AND versioned for repeatable ones (guardian
  added, removed, added again: put a version or timestamp in it), otherwise the second retry job is deduplicated away.
- Labels: the permission rules read labels from `role_assignment` history (`RoleAssignmentLabels`) until the
  Appwrite directory is wired. **Staging blocker:** wire the Appwrite Users label directory behind `LabelDirectory`
  first. A label set or removed only in Appwrite is not seen; A01 writes the `role_assignment` row after `add_label`, so a
  failure in between leaves a label with no row (nothing granted: safe, but a parent sees no data).

## Drift check

`cd services/core && uv run python scripts/permissions_check.py` builds the seeded dataset (every table), applies the
rules and audits: zero differences expected, exit 1 otherwise. It also fails when this page's matrix is stale
(`--write-docs` regenerates it). `--live` runs the same audit read-only against the configured project (owner step, below); it refuses (exit 2) unless the repository is a real `AppwriteRepository`, and reports `empty_tables` (a table with 0 rows) so an audit of nothing is never presented as green. The script also fails when a schema table has no decision.
The daily `permission-audit` job does the same against Appwrite and **reports only**: over-grants and forbidden entries
(`any`, `guests`, any create/update/delete, `users` outside the catalogue) raise a `permission_drift` alert; under-grants
are logged. Repairs are the reconcile job's work (ADR 0012 has the audit repairing too; the ticket asks for report-only).

## Creating and changing rows (every write path)

- A feature that creates a row creates it WITH its permissions:
  `repo.create(table, id, data, permissions=list(desired_permissions(table, data_with_id, ctx)))`, or creates it and
  then calls `PermissionService.run(row_created(...), ...)` / `sync_row`. A row created with no permissions is only
  visible to the core service (an under-grant, safe) until the next recompute.
- A feature that changes who may read something (assignment, substitute, case, role, guardian, account) wraps its
  business write in `PermissionService.run(event, business_write)` using the constructors in `service.py`.
- Never write `$permissions` any other way. The audit job reports any row whose list differs from the rules.

## Adding a table

Add it to `appwrite/schema/*.json`, then add a `Spec` in `rules.SPECS` (or list it as service-only). Until you do,
`test_every_table_has_a_decision` fails, and `desired_permissions` raises for it. Add a row to the seeded dataset
(`sample.py`) and to the persona expectations in `tests/test_perm_matrix.py`.

## Unverified assumptions (owner-blocked: needs a throwaway Appwrite project)

The fake (`permissions/fake.py`) assumes these Appwrite behaviours. None was run against a real project:

1. `read("label:<x>")` matches the user's labels exactly and case sensitively, and labels reject underscores
   (ADR 0012 cites the SDK doc comment only).
2. `read("user:<id>")` on a row, with `rowSecurity` on and empty table permissions, lets exactly that user (and nobody else)
   read the row; an unreadable row is absent from lists and a 404 on get.
3. `update_row(..., data={}, permissions=[...])` replaces `$permissions` and changes no column, including on rows that
   were created with a different list; the same for Storage `update_file(permissions=...)`.
4. A server API key bypasses all of it (so the service can read and write every row).
5. Invalid permission strings (and ids over 36 characters) are rejected with 400, and `permissions=[]` clears the list (the fake rejects them with `InvalidPermission`).
6. `$updatedAt` is queryable (`greaterThanEqual`) without an index; `cursorAfter` paging with `orderAsc("$id")`.
7. A permission update changes `$updatedAt` (the nightly reconcile then re-reads rows it just repaired once: harmless).

Owner steps to verify: see memory.md, ticket M05, "Owner-blocked".

## Generated matrix

<!-- BEGIN GENERATED: matrix (do not edit; run scripts/permissions_check.py --write-docs) -->

### Who reads which table

| Table | Parent / guardian | Teacher | Ops admin | Curator | Safeguarding lead | Super admin |
|---|---|---|---|---|---|---|
| `assessment` | own children; guardian with grant `children` | no | read (label) | no | no | no |
| `assessment_blueprint` | no | no | read (label) | read (label) | no | no |
| `assessment_item` | own children; guardian with grant `children` | no | read (label) | no | no | no |
| `assignment` | own children; guardian with grant `sessions` | own, while current or upcoming | read (label) | no | no | no |
| `audit_log` | no | no | read (label) | no | read (label); owner question 12, policy flag `audit_log_lead_read` | no |
| `availability_exception` | no | own | read (label) | no | no | no |
| `availability_slot` | no | own | read (label) | no | no | no |
| `bonus_rule` | no | no | read (label) | no | no | no |
| `cancellation` | own children; guardian with grant `sessions` | no | read (label) | no | no | no |
| `case_status` | the user who raised the case: reference, status and dates only | no | no | no | the assigned lead | no |
| `catalogue_version` | published and retired versions | no | read (label) | read (label) | no | no |
| `child` | own children; guardian with grant `children` | only while assigned (current or upcoming, incl. substitute window) | read (label) | no | while an open case concerns the child (assigned lead) | no |
| `consent_record` | own children; guardian with grant `consents` | no | read (label) | no | no | no |
| `curriculum` | read (published content) | no | read (label) | read (label) | no | no |
| `device_token` | own live token rows (revoked rows: nobody) | no | no | no | no | no |
| `dispute` | own children; guardian with grant `sessions` | no | read (label) | no | no | no |
| `earning` | no | own | read (label) | no | no | no |
| `feedback` | own; guardian with grant `sessions` | no | read (label) | no | no | no |
| `guardian_link` | own links; own link while invited or active | no | read (label) | no | no | no |
| `handover_rating` | no | own ratings | read (label) | no | no | no |
| `handover_summary` | own children; guardian with grant `logs` | the recipient teacher while assigned (substitute: covered session window) | read (label) | no | no | no |
| `home_location` | own; guardian with grant `address` | after accepting, until 24 h after the last session; substitute until 24 h after the covered session | read (label) | no | while an open case concerns the family (assigned lead) | no |
| `invoice` | own; guardian with grant `billing` | no | read (label) | no | no | no |
| `ledger_account` | no | no | read (label) | no | no | no |
| `ledger_entry` | no | no | read (label) | no | no | no |
| `ledger_transaction` | no | no | read (label) | no | no | no |
| `level` | of a published or retired version | no | read (label) | read (label) | no | no |
| `level_equivalence` | no | no | read (label) | read (label) | no | no |
| `match_offer` | no | no | read (label) | no | no | no |
| `match_request` | own; guardian with grant `sessions` | no | read (label) | no | no | no |
| `notification` | own notifications only | no | no | no | no | no |
| `notification_preference` | own (changes go through the core service) | no | no | no | no | no |
| `notification_template` | no | no | read (label) | no | no | read (label) |
| `parent_profile` | own | no | read (label) | no | the family of an open assigned case (assigned lead) | no |
| `payment` | own; guardian with grant `billing` | no | read (label) | no | no | no |
| `payout` | no | own | read (label) | no | no | no |
| `payout_batch` | no | no | read (label) | no | no | no |
| `plan` | no | no | read (label) | no | no | no |
| `policy_acceptance` | own | no | read (label) | no | no | no |
| `question` | no | no | read (label) | read (label) | no | no |
| `role_assignment` | no | no | no | no | no | read (label) |
| `safeguarding_action` | no | no | no | no | the assigned lead of the case only | no |
| `safeguarding_case` | no | no | no | no | the assigned lead only (also after close, policy flag lead_keeps_closed_case_record) | no |
| `school_upload` | own children; guardian with grant `children` | no | read (label) | no | no | no |
| `session` | own children; guardian with grant `sessions` | own sessions while the teacher may read the child (substitute: covered session window) | read (label) | no | sessions of a child with an open assigned case (assigned lead) | no |
| `session_checkin` | own children; guardian with grant `sessions` | no | read (label) | no | no | no |
| `session_confirmation` | own children; guardian with grant `sessions` | no | read (label) | no | no | no |
| `session_log` | own children; guardian with grant `logs` | while the teacher may read the child (assigned incl. substitute window) | read (label) | no | while an open case concerns the child (assigned lead) | no |
| `session_log_struggle` | as the log | as the log | read (label) | no | as the log | no |
| `session_log_topic` | as the log | as the log | read (label) | no | as the log | no |
| `strand` | of a published or retired version | no | read (label) | read (label) | no | no |
| `struggle_tag` | read (configuration list) | no | read (label) | read (label) | no | no |
| `subject` | of a published or retired version | no | read (label) | read (label) | no | no |
| `subscription` | own children; guardian with grant `sessions` | no | read (label) | no | no | no |
| `teacher_approval` | no | own | read (label) | no | no | no |
| `teacher_document` | no | own | read (label) | no | while an open case concerns the teacher (assigned lead) | no |
| `teacher_profile` | no | own | read (label) | no | the teacher of an open assigned case (assigned lead) | no |
| `teacher_session_history` | no | own anonymised history (first name, class, date, duration), kept after the assignment ends | read (label) | no | no | no |
| `teacher_zone` | no | own | read (label) | no | no | no |
| `tier` | no | no | read (label) | no | no | no |
| `tier_history` | no | own | read (label) | no | no | no |
| `topic` | of a published or retired version | no | read (label) | read (label) | no | no |
| `topic_progress` | own children; guardian with grant `logs` | while assigned | read (label) | no | no | no |
| `user_profile` | own account (even while suspended) | no | read (label) | no | the reporter, the child's parent and the teacher of an open assigned case (assigned lead) | no |
| `zone` | no | no | read (label) | no | no | no |

### Service-only tables (no client read for anyone)

- `access_window`: permission service bookkeeping (when time-limited access lapses)
- `app_setting`: H05 business-rule settings value rows, read and changed through the core API
- `app_setting_change`: H05 settings history (append-only), read through the core API
- `billing_claim`: B03 idempotency claims, server only (table arrives with its own ticket)
- `billing_credit`: B03 billing credits, server only (table arrives with its own ticket)
- `billing_payment`: B03 payment flags, server only (table arrives with its own ticket)
- `job_attempt`: F04 job runner attempts, server only (planned schema) (table arrives with its own ticket)
- `job_run`: F04 job runner state, server only (planned schema) (table arrives with its own ticket)
- `otp_challenge`: A01 one-time code hashes, server only (table arrives with its own ticket)
- `sample_item`: CI sample table
- `scheduling_issue`: S01 scheduling problems, server only (admin queue through an endpoint) (table arrives with its own ticket)
- `schema_migration`: migration runner bookkeeping
- `subscription_transition`: B03 subscription state log, server only (ops reads through an endpoint) (table arrives with its own ticket)
- `teacher_schedule_lease`: S01 per-teacher scheduling lease, server only (table arrives with its own ticket)
- `travel_time_cache`: X01 zone-pair travel time cache, server only (table arrives with its own ticket)
- `travel_usage`: X01 Google Routes usage counters, server only (table arrives with its own ticket)

### Buckets (file permission = the owning row's permission)

- `question-media`: files named by `question.image_file_id`, `audio_file_id`
- `school-reports`: files named by `school_upload.file_id`
- `teacher-docs`: files named by `teacher_document.file_id`

### Data clients can never write (core service only)

- money: `invoice`, `payment`, `ledger_account`, `ledger_transaction`, `ledger_entry`
- tier: `tier`, `tier_history`, `bonus_rule`, `teacher_profile`
- earning: `earning`
- payout: `payout_batch`, `payout`
- session status: `session`, `assignment`, `cancellation`, `dispute`, `session_confirmation`
- check-in validity: `session_checkin`
- safeguarding and audit: `safeguarding_case`, `safeguarding_action`, `case_status`, `audit_log`
- consent and policy: `consent_record`, `policy_acceptance`

Tables with any client create/update/delete: none.

Labels: `parent`, `guardian`, `teacher`, `opsadmin`, `curator`, `safeguardinglead`, `superadmin`.

<!-- END GENERATED: matrix -->
