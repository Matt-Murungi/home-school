# API keys: who gets which scopes

One key per consumer per environment, separate projects, fewest scopes needed, expiry set. **Key values are never written here or anywhere in the repo**; this file lists names and scopes only. The table is validated by `python3 check_keys.py` (allowlist per consumer in that script).

| Key | Consumer | Environment | Scopes | Expiry | Stored in |
|---|---|---|---|---|---|
| hs-core-development | core-service | development | databases.read, tables.read, rows.read, rows.write, users.read, users.write, files.read, files.write, buckets.read, execution.read, execution.write | 90d | Fly secret APPWRITE_API_KEY (dev app) |
| hs-core-staging | core-service | staging | databases.read, tables.read, rows.read, rows.write, users.read, users.write, files.read, files.write, buckets.read, execution.read, execution.write | 90d | Fly secret APPWRITE_API_KEY (staging app) |
| hs-core-production | core-service | production | databases.read, tables.read, rows.read, rows.write, users.read, users.write, files.read, files.write, buckets.read, execution.read, execution.write | 90d | Fly secret APPWRITE_API_KEY (production app) |
| hs-ci-development | ci-deploy | development | databases.read, databases.write, tables.read, tables.write, columns.read, columns.write, indexes.read, indexes.write, rows.read, rows.write, buckets.read, buckets.write, functions.read, functions.write | 90d | GitHub environment secret APPWRITE_API_KEY (development) |
| hs-ci-staging | ci-deploy | staging | databases.read, databases.write, tables.read, tables.write, columns.read, columns.write, indexes.read, indexes.write, rows.read, rows.write, buckets.read, buckets.write, functions.read, functions.write | 90d | GitHub environment secret APPWRITE_API_KEY (staging) |
| hs-ci-production | ci-deploy | production | databases.read, tables.read, tables.write, columns.read, columns.write, indexes.read, indexes.write, rows.read, rows.write, buckets.read, buckets.write, functions.read, functions.write | 90d | GitHub environment secret APPWRITE_API_KEY (production, required reviewers) |

## Why these scopes

- **core-service** (ADR 0012, SR 6.3): reads and writes rows (it writes every row and its permissions), sets user labels (`users.write`), writes files, executes functions. It cannot change schema, buckets or functions; a stolen runtime key cannot alter the structure.
- **ci-deploy**: applies `appwrite.config.json` (tables, columns, indexes, buckets, functions) and runs the migration runner (`rows.*` for `schema_migration`). No `users.*`, so CI cannot read or label accounts. The production key lacks `databases.write` (no database creation or deletion) and is released only through a protected GitHub environment.
- Clients (apps, admin browser) never hold a key (ADR 0012). The job-tick function runs with the function's own scoped key set in F04.
- Scopes to re-check against the real console list when the keys are created (owner step in memory.md); `check_keys.py` knows the names used here.

## Rotation

Rotate every 90 days or on suspicion: create the new key, update the secrets-manager entry, redeploy, delete the old key. Never paste a key into chat, issues or this file.
