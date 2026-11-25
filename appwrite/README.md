# appwrite

## Purpose

Appwrite project definition for the three Cloud projects (development, staging, production; Frankfurt): `appwrite.config.json` (tables, buckets, functions; applied with `appwrite push`), numbered data migrations in `migrations/` run by `migrate.py`, API-key scope manifest `keys.md`, and `environments.example.json`. Build from the tickets and System Requirements, never from the live project (read-only, ADR 0012). Nothing here is ever applied to the live project by an agent.

## Test command

`python3 -m unittest discover -s tests -v` (run from this directory), or `make test` from the repo root, which runs every package listed in `packages.json`. Stdlib only, offline (fake client, mocked HTTP).

## Files

| File | What |
|---|---|
| `schema/*.json` | **Source of truth for tables**: one file per domain (`_base`, `core` (M01), later `catalogue`, `scheduling`, ...). A ticket edits only its own file. Format and rules in `schema_build.py`. |
| `schema_build.py` | `--check` (default, also in `make lint`): validates every table (row security, no public/client-write permissions, field types and sizes, FK indexes, deletion policy per SR 5.5, UTC and integer-money naming rules) and fails if generated files are stale. `--write` regenerates them. |
| `appwrite.config.json` | Appwrite CLI format. The `tables` array is **generated** from `schema/*.json` (never edit it by hand); database, bucket `teacher-docs` and functions (F04 `job-tick`) are hand-edited. |
| `diff_live.py` | `python3 diff_live.py <snapshot.json> [--only a,b] [--alias repo=live]`: offline report of repo schema vs a read-only live structure snapshot (names and structure only). Snapshots are never committed. |
| `../docs/erd/<domain>.md` | Generated Mermaid ERD per domain. |
| `environments.example.json` | Names, endpoint, project-id placeholders, the env var that holds each key. Never a key. Copy to `environments.json` (git-ignored). |
| `migrate.py` | Runner. `python3 migrate.py --env staging`. Reads each key from the env var named in `keyEnvVar`. Exit 0 ok, 1 failed (prints `PROMOTE=false`, also writes `promote=` to `$GITHUB_OUTPUT`), 2 invalid migrations, 3 seed refused in production. |
| `migrations/0002_core_schema.py` | Creates the core tables through `client.ensure_table` (convergent: creates what is missing, waits for columns to be `available` before creating indexes, raises `SchemaDrift` on a different column type/size/required/array/enum elements/default, index type/columns/orders, or table rowSecurity/permissions; never alters). A schema change = edit the domain JSON + a new migration calling `schema_build.apply_domain(client, domain, only=[...])`. |
| `migrations/NNNN_name.py` | Each defines `up(ctx)` (`ctx.client`, `ctx.environment`). Names `NNNN_seed_*.py` are seeds: dev and staging only. Never edit an applied script; add a new one. |
| `appwrite_client.py` | Real adapter (urllib over Appwrite REST). Tested with a mocked opener only. |
| `fake_client.py` | In-memory stand-in used by the tests. |
| `functions/job-tick/` | The only function (F04, ADR 0002): cron `* * * * *`, python-3.12, standard library only, no Appwrite scopes. Signs and POSTs `/internal/jobs/tick` on the core service; all schedules live in `services/core/src/hs_core/jobs/schedule.py`. Variables (set in Appwrite per environment, never in the repo): `CORE_SERVICE_URL`, `JOB_TICK_SECRET`. Config entry in `appwrite.config.json`; its test is in `services/core/tests/test_job_tick_function.py`. |
| `keys.md`, `check_keys.py` | Who gets which scopes; `python3 check_keys.py` validates it. |

## Deletion, insert-only tables and retention (DP-6, SR 5.5)

- **The retention job is the only allowed hard-deleter.** No other code path hard-deletes a row of a business table (M05/M06 make this explicit when they build the repositories); the few `join` tables (e.g. `teacher_zone`) are the documented exception because they carry no history.
- `soft` tables set `deleted_at` (the row is hidden, not removed); the retention job later hard-deletes soft-deleted rows when the retention period ends. `history` tables (`role_assignment`) keep revoked rows with `revoked_at` until retention. `immutable` tables (`consent_record`, `policy_acceptance`) are never updated or soft-deleted; a child's consent rows are removed with the child under the retention policy, so they meet DP-6 without a delete path for anyone else.
- **What is enforced today:** `consent_record` and `policy_acceptance` have no client write permission (row security, empty table permissions; checked by `schema_build.py`). **Not enforced yet:** the core service's own key can still update/delete them because the F03 `Repository` has no per-table guard. A consent/policy repository wrapper exposing only create/list/get (update/delete raise) is a follow-up for M05/M06, together with the permission audit.
- Overlapping `availability_slot` rows are allowed by the schema; the core service validates and merges them.

## CI sequence (wired by F07 #33; not run from here)

1. `appwrite push` with the CI deploy key to the target environment (staging first).
2. `python3 migrate.py --env staging`. If it exits non-zero, stop: no production job.
3. Production only after staging is green and a reviewer approves the protected environment.
