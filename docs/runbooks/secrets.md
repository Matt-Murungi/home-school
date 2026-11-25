# Secrets runbook (F09, #35)

Covers routine rotation, leak response, a secret committed to git, and the rotation drill. Policy and the owner-blocked setup are in `docs/ops/secrets.md`; where each variable lives is in `docs/ops/config-matrix.md`; the machine-readable source is `docs/ops/secrets-inventory.json`. Never paste a secret into chat, issues, PRs, logs or this file.

The tables and procedures below are generated from the inventory (`python3 scripts/secrets_check.py --write`).

## Routine rotation

Rotate each server secret on its interval (table below). Put a calendar reminder per secret owner. Every procedure creates the new credential before revoking the old one, so there is no downtime: Fly restarts machines one at a time, and providers keep the old credential valid until you revoke it.

<!-- BEGIN GENERATED:schedule -->
| Secret | Consumer | Owner | Rotate every | Procedure | Overlap | Blast radius if leaked |
|---|---|---|---|---|---|---|
| `ADMIN_COOKIE_SECRET` | app-admin | platform-owner | 180 d | admin-cookie-secret | no | Forge the signed pending-MFA cookie of the admin sign-in, skipping the cookie-binding step (the Appwrite password and MFA challenge are still required for a real session). Must be 32+ random characters and identical on every admin instance of the environment. |
| `AFRICASTALKING_API_KEY` | core-service | platform-owner | 180 d | provider-key | yes | Send OTP and other SMS on our account (cost and phishing risk). |
| `AIRTEL_CLIENT_SECRET` | core-service | platform-owner | 180 d | payment-credential | yes | Airtel Money OAuth client secret. Phase 2. Production: collections and payouts through Airtel. |
| `APNS_AUTH_KEY` | firebase-console | platform-owner | 365 d | apns-key | yes | Send iOS pushes for our bundle id via Apple. Held in the Firebase console, not in our repo, Fly or GitHub. |
| `APPWRITE_API_KEY` | ci-deploy | platform-owner | 90 d | appwrite-api-key | yes | Change schema, buckets and functions of one environment's project; no user access. Production key lacks databases.write. |
| `APPWRITE_API_KEY` | core-service | platform-owner | 90 d | appwrite-api-key | yes | Read/write every row, set user labels, write files in one environment's Appwrite project. Cannot change schema. Production leak = full read/write of live family and child data. |
| `APP_STORE_CONNECT_API_KEY` | codemagic (owner-blocked) | platform-owner | 180 d | signing-credential | yes | Upload builds and manage TestFlight and metadata in App Store Connect with the role given to the key. Use the minimum role (App Manager or Developer). |
| `CARD_GATEWAY_SECRET_KEY` | core-service | platform-owner | 180 d | payment-credential | yes | Card gateway secret key (provider TBD, see memory.md questions). Production: charge and refund cards. |
| `CARD_GATEWAY_WEBHOOK_SECRET` | core-service | platform-owner | 180 d | webhook-secret | yes | Forge payment-confirmed webhooks if leaked (provider TBD). Production: mark unpaid plans as paid. |
| `CODEMAGIC_SECURE_VARIABLES` | codemagic (owner-blocked) | platform-owner | 180 d | signing-credential | yes | Group of per-environment Codemagic variables holding signing material and store API keys (public client keys are not secret). Leak lets an attacker build and publish as us. |
| `FCM_SERVICE_ACCOUNT_JSON` | core-service | platform-owner | 180 d | service-account | yes | Send push notifications to any device registered in one environment's Firebase project (phishing risk), no data read. |
| `FLY_API_TOKEN` | ci-deploy | platform-owner | 90 d | fly-token | yes | Deploy and read secrets of one Fly app. Must be an app-scoped deploy token, never an org token. Production token sits in the protected production environment. |
| `GOOGLE_MAPS_SERVER_KEY` | core-service | platform-owner | 180 d | provider-key | yes | Run Routes/Distance Matrix on our bill. Restrict by API and, where possible, by IP; budget alert on. |
| `HS_HEARTBEAT_URL` | core-service | platform-owner | 365 d | provider-key | yes | Capability URL: whoever holds it can fake 'job tick alive' to the uptime vendor and hide an outage (or, if leaked, spam false recoveries). It cannot read data. Rotate by creating a new heartbeat in the vendor and deleting the old one. |
| `IOS_DISTRIBUTION_CERTIFICATE` | app-stores (owner-blocked) | platform-owner | 365 d | signing-credential | yes | Sign iOS builds as the company; Apple certificates expire after 1 year. Revoke in the Apple Developer account on a leak. |
| `IOS_PROVISIONING_PROFILES` | app-stores (owner-blocked) | platform-owner | 365 d | signing-credential | yes | Profiles tie the certificate to the app ids; regenerated whenever the certificate is replaced. Low impact alone. |
| `JOB_TICK_SECRET` | core-service | platform-owner | 90 d | job-tick-secret | no | Forge a job tick: trigger payout, reminder and expiry jobs on demand in one environment (jobs are idempotent, so impact is load and timing, not duplicate payments). |
| `JOB_TICK_SECRET` | job-tick-function | platform-owner | 90 d | job-tick-secret | no | Same value as the core service copy; leak lets an attacker sign ticks. |
| `MOMO_COLLECTION_API_KEY` | core-service | platform-owner | 180 d | payment-credential | yes | Authenticate to MoMo Collections with our API user. Production: request payments from parents' wallets in our name. |
| `MOMO_COLLECTION_SUBSCRIPTION_KEY` | core-service | platform-owner | 180 d | payment-credential | yes | Call MoMo Collections as us (request payments). Production: live money movement into our account. |
| `MOMO_DISBURSEMENT_API_KEY` | core-service | platform-owner | 180 d | payment-credential | yes | Authenticate to MoMo Disbursements. Production: send payouts to arbitrary numbers; highest financial blast radius. |
| `MOMO_DISBURSEMENT_SUBSCRIPTION_KEY` | core-service | platform-owner | 180 d | payment-credential | yes | Call MoMo Disbursements as us. Production: pay out money from our wallet. |
| `OTP_PEPPER` | core-service | platform-owner | 365 d | otp-pepper | no | HMAC key for OTP code hashes and phone/IP pseudonyms (A01). With a copy of the otp_challenge table a leak lets an attacker recover live 6-digit codes offline and link rate-limit rows to numbers; it does not let anyone sign in without a live challenge. |
| `PLAY_UPLOAD_KEYSTORE` | app-stores (owner-blocked) | platform-owner | 365 d | signing-credential | yes | Sign app bundles as us for upload to Play. Play App Signing holds the real app signing key, so a leaked upload key can be reset through Play support; still a release-integrity risk. |
| `PLAY_UPLOAD_KEYSTORE_PASSWORD` | codemagic (owner-blocked) | platform-owner | 365 d | signing-credential | yes | With the keystore file, signs uploads to Play. Stored as a Codemagic secure variable; never committed. |
| `RESEND_API_KEY` | core-service | platform-owner | 180 d | provider-key | yes | Send email as our domain (receipts, safeguarding notices); phishing and reputation risk. Restrict to sending only (ADR 0005). |
| `RESEND_WEBHOOK_SECRET` | core-service | platform-owner | 180 d | webhook-secret | yes | Forge delivery and bounce events. |
| `SENTRY_AUTH_TOKEN` | ci-deploy | platform-owner | 90 d | github-env-secret | yes | Upload source maps and manage releases in Sentry; scoped to project:releases only. |
| `SENTRY_DSN` | core-service | platform-owner | 365 d | provider-key | yes | Write-only: submit fake events to our Sentry project. Low severity; rotate by creating a new client key and deleting the old one. |
<!-- END GENERATED:schedule -->

## Leak response

Goal: rotate a leaked secret **within one hour** and **without downtime**. Clock starts when someone first suspects the leak. Incident lead: the secret's rotation owner (the platform owner until a team exists).

1. **Contain (0-10 min).** Identify the secret and its environment from the inventory (name, consumer, store, blast radius). If it is production money (MoMo, Airtel, card), stop approving payout batches in the admin app first (there is no automatic payout switch yet). Do not wait to understand how it leaked.
2. **Rotate (10-40 min).** Follow the matching procedure below for that environment only: create the new credential, set it in the store, verify, then **revoke** the old one. Revoking is what ends the exposure, so do not delay it once the new credential is verified. If verification fails and the old credential is actively abused, revoke it anyway and accept a short outage, then fix forward.
3. **Audit (40-60 min and after).** Find out what the secret did while exposed: Appwrite console audit logs and function executions (project of that environment), Fly logs (`fly logs -a <app>`), the provider's API request log (MoMo/Airtel portal, Resend, Africa's Talking, Google Cloud billing and quota), GitHub audit log and Actions runs, Sentry for unusual errors. Record the exposure window, the actions seen and whether personal data was read.
4. **Notify.** Platform owner immediately. If personal data of parents, children or teachers may have been accessed, escalate to the DPO/lawyer the same day (PDPO breach duty, see `docs/legal/pdpo-registration-checklist.md`); the owner decides on notifying the regulator and affected people. If a provider can freeze or refund fraudulent activity, contact them from the provider's own support channel.
5. **Close.** Write a short post-incident note (timeline, cause, what was exposed, what changed) in the incident log, fix the cause (for example add a scanner rule, tighten a scope, shorten an interval), and add a line to the rotation log. No values anywhere.

### Committed to git

A secret in a commit is leaked even if the branch is private or the commit is "reverted".

1. Treat it as a leak: run the **Leak response** steps above first. Rotation matters more than cleaning history.
2. Find it: `python3 scripts/secret_scan.py` (prints file:line and rule, never the value), and GitHub secret scanning alerts.
3. If it never left your machine (not pushed), amend or rewrite the local commit and rotate anyway if it was ever in a pushed branch, a PR or a CI log.
4. If pushed: after rotating, remove it from history with `git filter-repo --replace-text` (or ask GitHub Support to purge cached views and PR refs), force-push only with the owner's approval, and ask collaborators to re-clone.
5. Prevent a repeat: keep `make secrets` and the pre-commit hook on, make sure GitHub push protection is enabled (owner step in `docs/ops/secrets.md`), and add a scanner rule if the key format was not detected.

### Shared or wrongly scoped key found

If the validator or a review finds one key used in two environments (or a production key in development or staging), treat it as a leak of the shared key: rotate it into separate per-environment keys using the procedure, then delete the shared one. `python3 scripts/secrets_check.py` rejects shared stores and production stores in non-production entries.

## Rotation procedures

<!-- BEGIN GENERATED:procedures -->
### Procedure `admin-cookie-secret`

Used by: `ADMIN_COOKIE_SECRET` (app-admin)

1. Generate a new value locally (`openssl rand -hex 32`, 32+ characters); never paste it into chat or an issue.
2. Set the same value on every admin instance of THIS environment in the admin host's environment settings (the host is chosen with F06 deployment, owner-blocked) and redeploy all instances together; a mix of old and new values breaks pending-MFA verification across instances.
3. Verify: sign in to the staging admin with MFA end to end (production: with the owner's account in a quiet window).
4. Discard the old value everywhere; only in-flight pending-MFA cookies (10 minutes) were invalidated.
5. Add a line to the rotation log (date, who, secret name, environment; no values).

### Procedure `apns-key`

Used by: `APNS_AUTH_KEY` (firebase-console)

1. In the Apple Developer account create a new APNs auth key (.p8); Apple allows two active keys.
2. Upload it to the Firebase project for THIS environment (Project settings > Cloud Messaging > APNs authentication key) and record the key id.
3. Verify with a test push to an iOS TestFlight build.
4. Revoke the old APNs key in the Apple Developer account.
5. Add a line to the rotation log. Keep the .p8 file out of the repo and out of chat.

### Procedure `appwrite-api-key`

Used by: `APPWRITE_API_KEY` (ci-deploy), `APPWRITE_API_KEY` (core-service)

1. In the Appwrite console of THIS environment's project (never another), create a new API key with the same scopes as appwrite/keys.md, name it hs-<consumer>-<env>-<yyyymmdd>, expiry 90d.
2. Set the new value in the store listed for that environment (Fly: `fly secrets import -a <app>` with `APPWRITE_API_KEY=<value>` on stdin (from a mode-600 file you delete afterwards); GitHub: environment secret). Fly redeploys the machines on a secrets change; whether the old key keeps serving during the rollout is unverified (confirm in the staging drill), so keep the OLD key valid until step 3 passes.
3. Verify: GET /health/ready on the app is 200 and one authenticated request succeeds; for CI, re-run the staging deploy workflow.
4. Delete the OLD key in the Appwrite console (this is the revoke step; on a leak do it as soon as step 3 passes, target within one hour).
5. Run `python3 appwrite/check_keys.py`, update appwrite/keys.md if scopes or names changed, and add a line to the rotation log (date, who, secret name, environment; no values).

### Procedure `fly-token`

Used by: `FLY_API_TOKEN` (ci-deploy)

1. Create a new app-scoped deploy token: `fly tokens create deploy -a <fly-app> -x 2160h` (90 days).
2. Replace the GitHub environment secret FLY_API_TOKEN for that environment (production: a required reviewer approves the change).
3. Verify by re-running the deploy workflow for staging (or the production workflow with approval).
4. Revoke the old token: `fly tokens list -a <fly-app>` then `fly tokens revoke <id>`.
5. Add a line to the rotation log.

### Procedure `github-env-secret`

Used by: `SENTRY_AUTH_TOKEN` (ci-deploy)

1. Create the new value at its source (provider dashboard, `fly tokens create`, Sentry settings) scoped to this environment only.
2. Update the GitHub environment secret for that environment (Settings > Environments > <env>); production requires a reviewer.
3. Re-run the workflow that uses it and confirm it succeeds.
4. Revoke the old value at its source.
5. Add a line to the rotation log.

### Procedure `job-tick-secret`

Used by: `JOB_TICK_SECRET` (core-service), `JOB_TICK_SECRET` (job-tick-function)

1. Generate a new random value (`openssl rand -hex 32`) locally; never paste it into chat or an issue.
2. Set it on the core service first (`fly secrets import -a <fly-app>` with `JOB_TICK_SECRET=<value>` on stdin), then on the job-tick function variable in the same environment's Appwrite project, then redeploy the function. F04 has no previous-secret mechanism, so rotation is NOT overlapping: ticks signed with the old value are rejected from the moment the core service has the new value until the function has it. Ticks are idempotent (ADR 0002) and retried on the next tick, so there is no data loss; expect at most a gap of one tick. Accepting a previous value during rotation is an F04 follow-up (docs/ops/secrets.md).
3. Verify: the next tick appears in the job-run log with status ok, and the uptime heartbeat (`HS_HEARTBEAT_URL`) is received.
4. Record the rotation in the log; there is no previous value to unset.

### Procedure `otp-pepper`

Used by: `OTP_PEPPER` (core-service)

1. Pick a quiet hour: rotating invalidates every OTP code in flight (at most 5 minutes of codes) and resets rate-limit counters, because code hashes and phone/IP pseudonyms are keyed by the pepper. There is no overlap mechanism.
2. Generate a new random value (`openssl rand -hex 32`) locally; never paste it into chat or an issue.
3. Set it on the core service (`fly secrets import -a <fly-app>` with `OTP_PEPPER=<value>` on stdin). Fly restarts the machines.
4. Verify: request a code in the staging app and sign in with it; check that `otp-cleanup` job runs stay ok. Users who were mid-sign-in simply request a new code.

### Procedure `payment-credential`

Used by: `AIRTEL_CLIENT_SECRET` (core-service), `CARD_GATEWAY_SECRET_KEY` (core-service), `MOMO_COLLECTION_API_KEY` (core-service), `MOMO_COLLECTION_SUBSCRIPTION_KEY` (core-service), `MOMO_DISBURSEMENT_API_KEY` (core-service), `MOMO_DISBURSEMENT_SUBSCRIPTION_KEY` (core-service)

1. Stop payouts for the environment by hand so no disbursement is in flight: in the admin app do not approve any payout batch until step 5 (collections keep working because they are retried by provider callbacks). If the DISBURSEMENT credential is the leaked one, revoke it at the provider first. The core service has no automatic payout switch today (payout ticket follow-up).
2. In the provider portal create a new API user key / secret for THIS environment (sandbox for development and staging; live only for production). MoMo: new API key for the existing API user, or a new API user if the user id is suspect.
3. Set the new values in the store (`fly secrets import -a <fly-app>` with `NAME=<value>` on stdin); Fly redeploys the app's machines when secrets change (whether the rollout is rolling, one machine at a time, is unverified: confirm in the staging drill).
4. Verify with one sandbox (or, in production, the smallest live) collection and, for disbursement credentials, one payout; confirm callbacks reach the service.
5. Revoke the OLD credential in the provider portal, resume approving payout batches, and reconcile the ledger for the window between steps 1 and 5 (ADR on idempotent payouts).
6. Add a line to the rotation log. In production, rotate in a low-traffic window (not weekdays 15:00-20:00 or weekends 08:00-18:00, SR NFR-5).

### Procedure `provider-key`

Used by: `AFRICASTALKING_API_KEY` (core-service), `GOOGLE_MAPS_SERVER_KEY` (core-service), `HS_HEARTBEAT_URL` (core-service), `RESEND_API_KEY` (core-service), `SENTRY_DSN` (core-service)

1. In the provider's dashboard for THIS environment (sandbox account for development and staging, live account only for production) create a second key; do not delete the first yet.
2. Set the new value in the store listed for that environment (`fly secrets import -a <app>` with `NAME=<value>` on stdin; Fly redeploys the machines; zero downtime is unverified until the staging drill).
3. Verify with the provider's smoke call in the staging runbook (send a test message / sandbox request) and that error rates in Sentry stay flat for 15 minutes.
4. Revoke the OLD key in the provider dashboard (this is the revoke step; on a leak do it immediately after step 3).
5. Add a line to the rotation log (date, who, secret name, environment; no values).

### Procedure `service-account`

Used by: `FCM_SERVICE_ACCOUNT_JSON` (core-service)

1. In the Firebase/Google Cloud console create a new service-account key (JSON) for THIS environment's Firebase project; keep the old key active.
2. Set it on the core service (`fly secrets import -a <fly-app>` with `FCM_SERVICE_ACCOUNT_JSON=<single-line JSON>` on stdin from a mode-600 file, then delete the local file).
3. Verify by sending a test push to a staging device.
4. Delete the OLD key in the service-account page (this is the revoke step).
5. Add a line to the rotation log.

### Procedure `signing-credential`

Used by: `APP_STORE_CONNECT_API_KEY` (codemagic), `CODEMAGIC_SECURE_VARIABLES` (codemagic), `IOS_DISTRIBUTION_CERTIFICATE` (app-stores), `IOS_PROVISIONING_PROFILES` (app-stores), `PLAY_UPLOAD_KEYSTORE_PASSWORD` (codemagic), `PLAY_UPLOAD_KEYSTORE` (app-stores)

1. Generate or request the replacement in the store console (Play Console > Setup > App signing > request upload key reset; Apple Developer > Certificates; App Store Connect > Users and Access > Integrations > Keys), keeping the old one valid until the new build is verified.
2. Store the new material only in the Codemagic secure variable group (or the store console) for the production release; never in the repo, chat or a laptop file that outlives the step.
3. Verify: run a Codemagic build and upload one internal-track (Play) or TestFlight (App Store) build signed with the new credential.
4. Revoke the OLD credential in the same console (this is the revoke step; on a leak do it as soon as step 3 passes).
5. Add a line to the rotation log (date, who, credential name; no values). Draft procedure until M01 sets up Codemagic.

### Procedure `webhook-secret`

Used by: `CARD_GATEWAY_WEBHOOK_SECRET` (core-service), `RESEND_WEBHOOK_SECRET` (core-service)

1. In the provider dashboard create a new webhook signing secret for THIS environment; most providers allow two active secrets during a window, so keep the old one.
2. Set the new value on the core service (`fly secrets import -a <fly-app>` with `NAME=<value>` on stdin); the service must verify against the new value.
3. Trigger a test webhook from the provider and confirm it verifies (HTTP 200, no signature errors in logs).
4. Remove the old signing secret at the provider.
5. Add a line to the rotation log.
<!-- END GENERATED:procedures -->

## Rotation drill

Run on **staging** once before launch (issue #35 "rotation runbook tested once on staging"), then after every procedure change and at least once a year. Use the staging environment only; never drill with production.

Checklist (tick each, note the time):

- [ ] Pick a staging secret with real use, for example `APPWRITE_API_KEY` for `core-service` in staging. Write the start time.
- [ ] Confirm the inventory entry, the store and the procedure are correct; note anything that was wrong or missing.
- [ ] Create the new credential; confirm the old one still works (overlap).
- [ ] Set the new value in the store; confirm machines restart one at a time and `/health/ready` stays 200 throughout (watch it from a loop in another terminal).
- [ ] Run a staging smoke test (sign in, create a row, a job tick) with the new credential.
- [ ] Revoke the old credential; confirm requests using it now fail (use a throwaway copy of the old value, then discard it).
- [ ] Audit step: find the old key's last-used time and the new key's first-used time in the Appwrite console; confirm nothing else used the old one.
- [ ] Practise the git case: commit a fake `standard_` + 64 hex characters in a scratch branch, confirm `make secrets` and the pre-commit hook block it, then delete the branch.
- [ ] Record end time. Target: under 60 minutes in total, zero failed user requests. Record the date, who, secret name, duration and fixes in the rotation log.
- [ ] Update this runbook and the inventory with anything learned.

Rotation log (append, no values): `date | who | secret | environment | reason (routine, drill, leak) | duration`.

| Date | Who | Secret | Environment | Reason | Duration |
|---|---|---|---|---|---|
| (drill pending: needs the staging Fly app and Appwrite project, owner-blocked) | | | | | |
