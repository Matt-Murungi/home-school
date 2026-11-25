# Retention enforcement (C03 #141)

Policy and legal text: `docs/legal/retention.md`, generated schedule `docs/legal/retention-schedule.md`. This page is the operator and reviewer view of the mechanism in `services/core/src/hs_core/retention/`.

## Parts

| Module | What it does |
|---|---|
| `policy.py` | One `Rule` per schema table: data class, basis, action (hard delete, anonymise, keep, cascade), period, legal floor, clock column, links to parent rows (restrict, cascade, anonymise, ignore), subject columns, tombstones, Storage files, what a deletion request does. `purge_order` puts children before parents. Coverage test: a schema table without a rule fails the suite. |
| `periods.py` | Kampala calendar arithmetic (leap day, month ends, UTC versus Kampala date). |
| `holds.py` | Explicit holds (`retention_hold`), the implicit hold of an open safeguarding case, inheritance through references. Fails closed. |
| `purger.py` | `RetentionPurger`: one table, one chunk: due check, hold check, restrict/cascade plan (reads), quarantine, files first, one delete site. Anonymise with a marker prefilter. |
| `run.py`, `jobs.py` | The `retention-purge` F04 job: purge guard, lease, dry run, stages, per-table isolation, summary row, one audit event. |
| `files.py` | `RetentionFileStore` (Appwrite Storage adapter + fake), orphan file sweeper. |
| `deletion.py` | `DeletionService` (deletion_request lifecycle) and `execute_deletion(repo, files, kind, id)`; C05 calls these. |
| `render.py` | Renders the schedule document. |

Tables (migration `0011_retention_schema.py`, `appwrite/schema/retention.json`): `retention_hold`, `deletion_request`, `retention_run` (lease generations and run summaries). All server-only (M05 `SERVICE_ONLY_TABLES`).

## The job

`retention-purge`, daily 03:30 Kampala, `max_attempts` 3, `catch_up` 1 day. A run is several F04 chunks (about 200 writes or 6 seconds each); the cursor holds `{seq, now, mode, i, after, stats}`. The run clock is fixed in the first chunk.

Stages: N01 notification payload expiry (`expire_chunk`, resumed by its cursor, 90 days) then every purgeable table in `purge_order`, then the orphan file sweep.

Paging: `order_asc($id)` + `cursor_after`, at most 100 rows per list, never offsets (Appwrite caps offsets and totals at 5,000). Purged rows disappear, so the resume cursor is the last row that still exists; if the cursor row vanished the table restarts (every step is idempotent).

### Safeguards

1. **Dry run by default.** `RETENTION_ENFORCE` is false everywhere, including staging and production. A dry run walks the same path, writes nothing except its own run rows and audit event, and reports `would_delete`, `would_anonymise`, `would_quarantine`.
2. **Purge guard.** The first chunk refuses (status `refused`, alert `retention_guard_refused`) when the clock year is outside 2026-2100 or earlier than the last run's start (5 minutes of skew allowed).
3. **Lease.** One run at a time: `retention_run` rows `lease-<n>` are created with a deterministic id, so exactly one of any number of starters wins generation n; the holder renews each chunk; a crashed holder is replaced after `RETENTION_LEASE_S` (600). A second run stands down (`Done: another purge run holds the lease`).
4. **Blast radius.** Per table, before the first row: if more than `RETENTION_MAX_DELETE_PCT` (10) percent of a table (of at least 50 rows) is selected, the table is refused (alert `retention_blast_limit`). `RETENTION_BLAST_OVERRIDE=true` lifts it for a deliberate first big purge. At most 5,000 rows per table per run.
5. **Holds** (see above): checked per page and again right before each write; a lookup failure aborts the chunk for that table. Residual race: a hold placed in the few milliseconds between the final check and the delete is not seen; the 14-day quarantine is the second line for soft tables.
6. **Quarantine.** Soft tables are soft-deleted first (`quarantine_patch` is checked against `schema_build.soft_delete_patch`) and hard-deleted `RETENTION_QUARANTINE_DAYS` later. Hard tables have no quarantine: the backup is the restore.
7. **Per-table isolation.** An exception on one table is counted, alerted (`retention_table_failed`) and the next table runs. An error on one row is counted and the table continues.
8. **Logs.** Counts, table names and row ids only. Tests plant canary values in rows and assert they never reach a log, a run row, an alert or an audit event.

### Audit (M06)

One `audit_log` row per run via `AuditService.record_external` (key `retention:<seq>`, action `retention.purge` or `retention.dry_run`, entity `retention_run`): totals and counts per table; row ids only for tables where at most 50 rows were purged (safeguarding cases, documents). Not row by row: millions of rows would bury the log. A deletion request writes one `deletion.execute` event. The purger uses the raw repository because it is the one allowed hard-deleter (an audited delete would write one audit row per purged row).

### Restore story (owner-blocked)

- Deletion by the job is irreversible once the quarantine window has passed. Recovery is from backup: Appwrite Cloud database backups and Storage buckets. **Not proven**: the owner must confirm the backup plan, its retention (a deleted record also lives in backups until they roll off: state this in the privacy notice) and run one restore drill into a scratch project before `RETENTION_ENFORCE` is switched on in production (memory.md "Blocked on owner").
- Within the quarantine window a soft-deleted row is restored with `schema_build.soft_restore_patch` (never a bare `{"deleted_at": None}`).

## The append-only exception path

`ledger_entry`, `ledger_transaction`, `audit_log`, `safeguarding_action` are append-only (M04): `AppendOnlyRepository` refuses delete and is unchanged. The retention purger holds the raw repository, so it CAN delete from them; that capability is deliberately narrow, an exception path, never the normal path:

1. **Never for the ledger.** `ledger_*` rules are `never_purge` (and have no age clock); code refuses them even with every switch on.
2. **Owner switch.** `RETENTION_ALLOW_APPEND_ONLY_PURGE` (default false) must be true, or `skip_reason` returns `append_only` and the table is not purged.
3. **Policy.** The table needs a rule that is not `never_purge`, with a legal `floor`. Today only the cascade from a closed safeguarding case reaches `safeguarding_action`; `audit_log` is KEEP.
4. **Checked per row.** The row's own timestamp (`acted_at`, `occurred_at`) must be older than the floor, re-verified for every row, every call, again at the moment of the write.
5. **Holds** apply as everywhere.
6. Every purged append-only row is counted (`append_only_purged`), logged by table and id, and appears in the run's audit summary.

### What the tripwire cannot see

`find_append_only_violations` and `find_unaudited_writes` match LITERAL table names; the purger passes a table name taken from the policy, which they do not see (the tests prove this limit on a synthetic file). Two other tests compensate: (a) no code outside an allowlist (`repository.py`, `audit/repository.py`, `permissions/fake.py`, the OTP cleanup) may call `.delete(` at all, and the ones outside `retention/` may only name hard tables; (b) inside `retention/` exactly one place deletes table rows (`RetentionPurger.erase_row`). Not covered: an alias (`d = repo.delete`), `getattr`, a delete through the Appwrite SDK directly, the console. Review rule: no new `.delete(` outside this module; the M01-M05 "retention is the only hard-deleter" rule stays a convention plus these tests.

## Data-subject deletion

See `docs/legal/retention.md` and `deletion.py`. Holds block the whole request at scheduling and again at execution. `execute_deletion` is idempotent (crash-injected after every write). The request row holds ids and states, never values; `result` holds counts per table.

## Settings

`RETENTION_ENFORCE` (false), `RETENTION_PERIODS` (JSON), `RETENTION_QUARANTINE_DAYS` (14, 0-90), `RETENTION_MAX_DELETE_PCT` (10, 1-100), `RETENTION_BLAST_OVERRIDE` (false), `RETENTION_ALLOW_APPEND_ONLY_PURGE` (false), `RETENTION_DELETION_GRACE_DAYS` (30, 1-90), `RETENTION_LEASE_S` (600, 60-3600). All in `.env.example` and the F09 inventory; invalid values fail startup.

## Known limits

- The child clock needs the child to be soft-deleted (`deleted_at`): B03/C05 must soft-delete a child when the family cancels or closes the account; a child that is never soft-deleted is never purged (safe direction). Follow-up for B03/C05.
- Legacy soft tables with natural unique values (`user_profile.phone`, `.email`) keep the value reserved while quarantined (M01 follow-up); the row is freed at the hard delete.
- `topic_progress` is derived: it is removed with the child, and rebuilt progress after logs expire shows fewer topics.
- Appwrite `notStartsWith`, `isNull` and `total` behaviour is exercised with the in-memory fake and SDK query builders only (live check owner-blocked).
- The real Storage adapter is mock-tested only.
