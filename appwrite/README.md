# appwrite

## Purpose

Appwrite project definition for the three Cloud projects (development, staging, production; Frankfurt): `appwrite.config.json` (tables, buckets, functions; applied with `appwrite push`), numbered data migrations in `migrations/` run by `migrate.py`, API-key scope manifest `keys.md`, and `environments.example.json`. Build from the tickets and System Requirements, never from the live project (read-only, ADR 0012). Nothing here is ever applied to the live project by an agent.

## Test command

`python3 -m unittest discover -s tests -v` (run from this directory), or `make test` from the repo root, which runs every package listed in `packages.json`. Stdlib only, offline (fake client, mocked HTTP).

## Files

| File | What |
|---|---|
| `appwrite.config.json` | Skeleton in Appwrite CLI format: database `main`, `schema_migration` and sample table, bucket `teacher-docs`, no functions yet. ADR 0012 rules: row security on, empty table permissions, no client writes. Real tables arrive in M01-M04. |
| `environments.example.json` | Names, endpoint, project-id placeholders, the env var that holds each key. Never a key. Copy to `environments.json` (git-ignored). |
| `migrate.py` | Runner. `python3 migrate.py --env staging`. Reads each key from the env var named in `keyEnvVar`. Exit 0 ok, 1 failed (prints `PROMOTE=false`, also writes `promote=` to `$GITHUB_OUTPUT`), 2 invalid migrations, 3 seed refused in production. |
| `migrations/NNNN_name.py` | Each defines `up(ctx)` (`ctx.client`, `ctx.environment`). Names `NNNN_seed_*.py` are seeds: dev and staging only. Never edit an applied script; add a new one. |
| `appwrite_client.py` | Real adapter (urllib over Appwrite REST). Tested with a mocked opener only. |
| `fake_client.py` | In-memory stand-in used by the tests. |
| `keys.md`, `check_keys.py` | Who gets which scopes; `python3 check_keys.py` validates it. |

## CI sequence (wired by F07 #33; not run from here)

1. `appwrite push` with the CI deploy key to the target environment (staging first).
2. `python3 migrate.py --env staging`. If it exits non-zero, stop: no production job.
3. Production only after staging is green and a reviewer approves the protected environment.
