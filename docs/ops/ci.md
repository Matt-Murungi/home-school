# CI/CD (F07, #33): what runs, rules, owner checklist

| Workflow | Trigger | What |
|---|---|---|
| `ci.yml` | pull_request, push to `main` | path-filtered lint and tests per package; required check `ci-ok` |
| `deploy-staging.yml` | push to `main`, manual | schema, core, job-tick, admin to staging; inert until `DEPLOY_ENABLED=true` |
| `promote-production.yml` | manual only | production release behind the `production` environment approval |
| `mobile-builds.yml` | manual only | skeleton, does nothing (docs/ops/mobile-builds.md) |
| `dependabot.yml` | weekly | actions, uv, docker, npm, pub updates |

## How CI selects work

`scripts/ci_changes.py` diffs the PR base against the head and prints which job groups to run. Shared files
(`packages.json`, `Makefile`, `.github/**`, CI scripts) turn everything on; an unknown base fails safe to
"everything". `ci-ok` is the only job branch protection needs: it fails if any job failed or was cancelled and
passes when jobs were skipped by the path filter. Flutter packages build as a matrix of only the changed ones
(a change in `packages/flutter_shared` builds both apps). `HS_STRICT=1` is set for the whole workflow.

Caching: uv (`setup-uv` cache keyed on `services/core/uv.lock`), npm (`setup-node` cache on the admin lockfile),
Flutter SDK and pub (`flutter-action` cache). PR runs cancel superseded runs; push runs on `main` never do.

## Rules enforced by tests (`python3 scripts/ci_lint.py`, `scripts/tests/test_workflows.py`)

No `pull_request_target`; top-level `permissions: contents: read`; actions pinned to full commit SHAs; no secrets
or environments in pull_request workflows (fork PRs get none); `timeout-minutes` on every job; checkout without
persisted credentials; no untrusted `github.event.*`/`inputs.*` interpolated into shell; every deploy job/step behind
`vars.DEPLOY_ENABLED == 'true'`; schema job has its own non-cancelling concurrency group; API steps gated on
migration exit code and `promote == 'true'`; every implemented package in `packages.json` has a job that runs `python3 scripts/run_packages.py test|lint --only <id>`, so commands always come from
`packages.json` and cannot drift (or a recorded exemption: the two `spikes/`); Dependabot covers every lockfile.

actionlint 1.7.12 also runs in the `repo-checks` job (`uvx --from actionlint-py==1.7.12.25 actionlint`); it reported nothing on these
files when run locally by the builder.

## Owner checklist (all owner-only; nothing here was done by an agent)

1. **Branch protection** on `main` (Settings > Branches): require status check **`ci-ok`** (only that one), require
   branch up to date, restrict force-push and deletion, squash merge only. Actions must be enabled. First PR
   after merging F07 shows whether `ci-ok` is available to select.
2. **Fork PR safety** (Settings > Actions > General): "Require approval for all outside collaborators" (or first-time
   contributors), workflow token default permission "Read repository contents".
3. **Environments** (Settings > Environments): `staging` (no reviewers; deployment branches: `main`) and `production`
   (required reviewers: the owner; "prevent self-review" optional; deployment branches: `main` only).
4. **Variables** (repository): `DEPLOY_ENABLED` = `true` only when ready. Per environment variables:
   `APPWRITE_PROJECT_ID`, `FLY_APP` (e.g. `homeschool-core-staging` / `homeschool-core-production`, ADR 0003),
   `STAGING_CORE_URL` (staging) / `PRODUCTION_CORE_URL` (production).
5. **Environment secrets** (per environment, never repository-wide): `APPWRITE_API_KEY` (the `ci-deploy` key from
   `appwrite/keys.md`, scoped to that project), `FLY_API_TOKEN` (`fly tokens create deploy -a <app>`).
6. Create the Fly apps and the Appwrite projects (F02, F03, ADR 0003); set Fly runtime secrets (`docs/ops/secrets.md`);
   set the `job-tick` function variables in the Appwrite console.
7. Admin host decision (ADR 0004 names none), mobile provider and store accounts (mobile-builds.md).
8. Enable Dependabot alerts/updates in the repository security settings if not already on.
