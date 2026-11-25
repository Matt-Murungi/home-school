# Secrets management and environment configuration (F09, #35)

Policy for every credential the platform needs (SR §4, SEC-3; ADR 0003 Fly.io; ADR 0002 job runner; ADR 0005 Resend; ADR 0012 Appwrite; `appwrite/keys.md`).

| Artefact | Where |
|---|---|
| Inventory (machine-readable, names and metadata only) | `docs/ops/secrets-inventory.json` |
| Config matrix (variable by environment) | `docs/ops/config-matrix.md` |
| Rotation, leak response, drill | `docs/runbooks/secrets.md` |
| Validator | `python3 scripts/secrets_check.py` (also `make test`, via the `root-tests` package) |
| Secret scanner | `make secrets` (`scripts/secret_scan.py`), gitleaks via pre-commit, GitHub secret scanning |

## What lives where

| Store | Holds | Notes |
|---|---|---|
| Fly secrets, one app per environment (ADR 0003 names: `homeschool-core-staging`, `homeschool-core-prod`; development uses the optional `homeschool-core-dev` or a local `.env`; in procedures `<fly-app>` means the app of that environment) | Everything the core service reads: Appwrite runtime key, `JOB_TICK_SECRET`, MoMo, Airtel, card gateway, FCM service account, Resend, Africa's Talking, Google Maps server key, Sentry DSN | `fly secrets import -a <fly-app>` (reads `NAME=VALUE` pairs from stdin, so values stay out of shell history and arguments; per the flyctl reference. The `fly secrets set NAME=-` stdin form is **unverified**, do not use it) |
| Appwrite function variables (per environment project) | `JOB_TICK_SECRET` for the job-tick function | Same value as the core copy |
| GitHub Actions environment secrets (`development`, `staging`, `production`) | CI deploy Appwrite key, `FLY_API_TOKEN`, `SENTRY_AUTH_TOKEN` | Production environment requires reviewers |
| Provider console | APNs auth key (inside the Firebase project) | Not in any of our stores |
| App build-time defines | Public client keys only: restricted Google Maps keys, Sentry client DSN, Appwrite project id and endpoint | Public by design; restrictions are in the inventory |

Clients (apps, admin browser) never hold a server secret. Appwrite has no anon key; the public project id and endpoint are the only Appwrite values in a client.

## Rules the validator enforces

1. One entry per secret and consumer; every entry has a known consumer, kind (`server_secret`, `public_client_key`, `non_secret`) and location.
2. A store name ends with its environment; no store is shared between environments; `value_per_environment` is true. Hence a shared dev key, or a production key in dev or staging, fails.
3. `live` provider values exist only in production; development and staging use sandboxes. `local-dev` consumers may hold development values only.
4. Server secrets: owner, interval (1-365 days), blast radius, a procedure of at least 3 steps, overlap stated (no overlap requires a downtime note), never in an app bundle.
5. Public client keys: build define only, with a stated restriction.
6. Every variable read by `services/core` settings or by app code (`process.env.X`, `String.fromEnvironment('X')`) is in the inventory; `SecretStr` settings are server secrets; public-looking app variables that look secret (`NEXT_PUBLIC_*KEY`) fail.
7. `services/core/.env.example` lists every setting, all empty, with nothing extra.
8. Appwrite key rotation intervals are no longer than the expiry in `appwrite/keys.md`.
9. Generated tables in the matrix and runbook match the inventory; the runbook has routine rotation, leak response, committed-to-git and drill sections.

## Rotation intervals (defaults)

Appwrite keys, Fly and CI tokens, JOB_TICK_SECRET: 90 days. Payment, email, SMS, Maps, FCM, webhook secrets: 180 days. Low-sensitivity write-only identifiers (Sentry DSN, APNs key): 365 days. Any suspicion: immediately (leak response).

## Status of the ticket's criteria

| Criterion | Status |
|---|---|
| No secrets in repo or app bundles except public keys | Enforced by scanner (`make secrets`, extended with Google, Resend, Sentry DSN, Fly and Africa's Talking patterns), validator rules 2-6, gitignore of `.env*` |
| Secret scanning enabled on the repo | GitHub secret scanning and push protection are **enabled** (read from the repo settings API on 25 Nov 2026). The `non_provider_patterns` and `validity_checks` options are off; owner may turn them on |
| Rotation runbook | `docs/runbooks/secrets.md` (issue text says that path) |
| Runbook tested once on staging | **Owner-blocked**: needs the staging Fly app and Appwrite staging project; the drill checklist is ready |
| Deployed to staging | **Owner-blocked** (see below) |

## Owner-blocked steps (with recommended defaults)

Decision: **use Fly secrets (per app) for runtime secrets and GitHub environment secrets for CI**. No separate secrets manager at this scale; Fly secrets are encrypted at rest, per app, and fit ADR 0003. Revisit (for example Doppler or 1Password Secrets Automation) if a team larger than three people needs audit trails beyond Fly and GitHub logs.

1. **Create the Fly apps** `homeschool-core-staging` and `homeschool-core-prod` as ADR 0003 names them (region `fra`), plus optionally `homeschool-core-dev`; the names are proposals and must be globally unique on Fly, so if you pick others change the inventory stores and the GitHub `FLY_APP` variable together. F07's `docs/ops/ci.md` example says `homeschool-core-production`: use `homeschool-core-prod`, or change both. Needs the owner's Fly account.
2. **Create the three Appwrite projects** (`hs-development`, `-staging`, `-production`) if not already done, and the API keys per `appwrite/keys.md` (names `hs-core-<env>`, `hs-ci-<env>`, 90 day expiry). The live project stays read-only for builders.
3. **Set runtime secrets**: `fly secrets import -a <fly-app>` with `APPWRITE_API_KEY=<value>` on stdin (and the other names in the matrix, as each provider account is created). Use sandbox provider credentials in development and staging.
4. **GitHub environments** `development`, `staging`, `production`: Settings > Environments > New environment; on `production` add **required reviewers** (the owner) and restrict deployment branches to `main`. Add secrets `APPWRITE_API_KEY`, `FLY_API_TOKEN` (`fly tokens create deploy -a <fly-app>`, app-scoped, never an org token) and `SENTRY_AUTH_TOKEN` per environment.
5. **Provider accounts** (MoMo sandbox and live, Airtel, card gateway choice, Resend domain, Africa's Talking, Google Cloud project with restricted keys and budget alerts, Firebase projects per environment with the APNs key, Sentry projects): owner creates; then set values per steps 3-4. Never paste values into chat or the repo.
6. **Optional GitHub toggles**: secret-scanning non-provider patterns and validity checks; `.github/CODEOWNERS` already covers review.
7. **Run the rotation drill on staging** from `docs/runbooks/secrets.md` once steps 1-4 are done; record it in the rotation log.
8. **Codemagic and app-store signing credentials** (owner-blocked; in the inventory with `owner_blocked: true` and store `TBD`): Play upload keystore and password, iOS distribution certificate and provisioning profiles, App Store Connect API key, Codemagic secure variables. They already have an owner, interval, blast radius and a draft `signing-credential` procedure; the owner creates the Codemagic account, chooses the store for each (Codemagic secure variable groups recommended) and replaces `TBD`. M01 and the release tickets own the build setup.

Fly redeploys the app's machines when a secret changes (flyctl docs: `--stage` skips that deployment). Whether the rollout is rolling and whether the old value keeps serving meanwhile is **unverified** until the first staging drill.

## Follow-ups for other tickets

- F04 (job runner): accept `JOB_TICK_SECRET_PREVIOUS` during rotation for fully overlapping rotation, and read the secret through `Settings` so the validator sees it (the inventory does **not** list it: F04 merged without a previous-secret mechanism, so rotating `JOB_TICK_SECRET` can miss one tick, as the `job-tick-secret` procedure says).
- Payout ticket: add a core-service switch to pause disbursements; until then the `payment-credential` procedure pauses payouts by hand (do not approve payout batches in admin, revoke the disbursement credential).
- F06 (admin Sentry): `docs/ops/setup.md` plans `NEXT_PUBLIC_SENTRY_DSN` (public client DSN, restrict by allowed origins and rate limit in Sentry) and a server `SENTRY_DSN` for app-admin. No admin code reads them yet, so the validator cannot require rows; when F06 adds the reads, add `NEXT_PUBLIC_SENTRY_DSN` as a `public_client_key` with a restriction and `SENTRY_DSN` (app-admin) as a server secret.
- F06: choose the admin host (deferred by ADR 0004) and set `ADMIN_COOKIE_SECRET` per environment (32+ random characters, identical on every admin instance; see the `admin-cookie-secret` procedure).
- F05/F06/M01: when apps read `--dart-define` or `process.env` values, run `python3 scripts/secrets_check.py`; it fails until each variable is in the inventory.
- F07 (CI): run `python3 scripts/secrets_check.py` and `make secrets` on every PR; keep push protection on.
- F08 (CI/CD or deploy): use the GitHub environment secrets named here; do not add repository-level secrets for production.
