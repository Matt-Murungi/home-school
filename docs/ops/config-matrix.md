# Configuration matrix (F09, #35)

Which variable exists in which environment, what kind it is and where it lives. The table is **generated** from `docs/ops/secrets-inventory.json` (`python3 scripts/secrets_check.py --write`) and checked in CI-style by `python3 scripts/secrets_check.py`, so it cannot drift from the inventory. Cells for secrets name the secrets store (never a value); `(sandbox)` and `(live)` say which provider account the value belongs to.

<!-- BEGIN GENERATED:matrix -->
| Variable | Kind | Consumer | development | staging | production | Where it lives |
|---|---|---|---|---|---|---|
| `ADMIN_COOKIE_SECRET` | server secret | app-admin | host-env:admin-development | host-env:admin-staging | host-env:admin-production | host_env |
| `AFRICASTALKING_API_KEY` | server secret | core-service | fly:homeschool-core-dev | fly:homeschool-core-staging | fly:homeschool-core-prod | fly_secret |
| `AIRTEL_CLIENT_SECRET` | server secret | core-service | fly:homeschool-core-dev (sandbox) | fly:homeschool-core-staging (sandbox) | fly:homeschool-core-prod (live) | fly_secret |
| `APNS_AUTH_KEY` | server secret | firebase-console | provider-console:firebase-development | provider-console:firebase-staging | provider-console:firebase-production | provider_console |
| `APPWRITE_API_KEY` | server secret | ci-deploy | github-env:development | github-env:staging | github-env:production | github_actions_secret |
| `APPWRITE_API_KEY` | server secret | core-service | fly:homeschool-core-dev | fly:homeschool-core-staging | fly:homeschool-core-prod | fly_secret |
| `APP_STORE_CONNECT_API_KEY` | server secret | codemagic | - | - | TBD (owner-blocked) | codemagic_variable |
| `CARD_GATEWAY_SECRET_KEY` | server secret | core-service | fly:homeschool-core-dev (sandbox) | fly:homeschool-core-staging (sandbox) | fly:homeschool-core-prod (live) | fly_secret |
| `CARD_GATEWAY_WEBHOOK_SECRET` | server secret | core-service | fly:homeschool-core-dev (sandbox) | fly:homeschool-core-staging (sandbox) | fly:homeschool-core-prod (live) | fly_secret |
| `CODEMAGIC_SECURE_VARIABLES` | server secret | codemagic | - | TBD (owner-blocked) | TBD (owner-blocked) | codemagic_variable |
| `FCM_SERVICE_ACCOUNT_JSON` | server secret | core-service | fly:homeschool-core-dev | fly:homeschool-core-staging | fly:homeschool-core-prod | fly_secret |
| `FLY_API_TOKEN` | server secret | ci-deploy | github-env:development | github-env:staging | github-env:production | github_actions_secret |
| `GOOGLE_MAPS_SERVER_KEY` | server secret | core-service | fly:homeschool-core-dev | fly:homeschool-core-staging | fly:homeschool-core-prod | fly_secret |
| `HS_HEARTBEAT_URL` | server secret | core-service | - | fly:homeschool-core-staging | fly:homeschool-core-prod | fly_secret |
| `IOS_DISTRIBUTION_CERTIFICATE` | server secret | app-stores | - | - | TBD (owner-blocked) | app_store_console |
| `IOS_PROVISIONING_PROFILES` | server secret | app-stores | - | - | TBD (owner-blocked) | app_store_console |
| `JOB_TICK_SECRET` | server secret | core-service | fly:homeschool-core-dev | fly:homeschool-core-staging | fly:homeschool-core-prod | fly_secret |
| `JOB_TICK_SECRET` | server secret | job-tick-function | appwrite-fn:job-tick-development | appwrite-fn:job-tick-staging | appwrite-fn:job-tick-production | appwrite_function_variable |
| `MOMO_COLLECTION_API_KEY` | server secret | core-service | fly:homeschool-core-dev (sandbox) | fly:homeschool-core-staging (sandbox) | fly:homeschool-core-prod (live) | fly_secret |
| `MOMO_COLLECTION_SUBSCRIPTION_KEY` | server secret | core-service | fly:homeschool-core-dev (sandbox) | fly:homeschool-core-staging (sandbox) | fly:homeschool-core-prod (live) | fly_secret |
| `MOMO_DISBURSEMENT_API_KEY` | server secret | core-service | fly:homeschool-core-dev (sandbox) | fly:homeschool-core-staging (sandbox) | fly:homeschool-core-prod (live) | fly_secret |
| `MOMO_DISBURSEMENT_SUBSCRIPTION_KEY` | server secret | core-service | fly:homeschool-core-dev (sandbox) | fly:homeschool-core-staging (sandbox) | fly:homeschool-core-prod (live) | fly_secret |
| `OTP_PEPPER` | server secret | core-service | fly:homeschool-core-dev | fly:homeschool-core-staging | fly:homeschool-core-prod | fly_secret |
| `PLAY_UPLOAD_KEYSTORE` | server secret | app-stores | - | - | TBD (owner-blocked) | app_store_console |
| `PLAY_UPLOAD_KEYSTORE_PASSWORD` | server secret | codemagic | - | - | TBD (owner-blocked) | codemagic_variable |
| `RESEND_API_KEY` | server secret | core-service | fly:homeschool-core-dev | fly:homeschool-core-staging | fly:homeschool-core-prod | fly_secret |
| `RESEND_WEBHOOK_SECRET` | server secret | core-service | fly:homeschool-core-dev | fly:homeschool-core-staging | fly:homeschool-core-prod | fly_secret |
| `SENTRY_AUTH_TOKEN` | server secret | ci-deploy | github-env:development | github-env:staging | github-env:production | github_actions_secret |
| `SENTRY_DSN` | server secret | core-service | fly:homeschool-core-dev | fly:homeschool-core-staging | fly:homeschool-core-prod | fly_secret |
| `GOOGLE_MAPS_ANDROID_KEY` | public client key | app-parent | build define | build define | build define | build_define |
| `GOOGLE_MAPS_IOS_KEY` | public client key | app-parent | build define | build define | build define | build_define |
| `SENTRY_DSN_APP` | public client key | app-parent | build define | build define | build define | build_define |
| `SENTRY_DSN_APP` | public client key | app-teacher | build define | build define | build define | build_define |
| `ADMIN_AUDIT_RATE_PER_MIN` | config | core-service | 60 | 60 | 60 | fly_env |
| `ADMIN_ENV` | config | app-admin | local | staging | production | host_env |
| `AFRICASTALKING_SENDER_ID` | config | core-service | (empty: provider default sender) | (empty: provider default sender) | (empty: provider default sender) | fly_env |
| `AFRICASTALKING_USERNAME` | config | core-service | sandbox in dev/staging | sandbox in dev/staging | sandbox in dev/staging | fly_env |
| `APPWRITE_CONNECT_TIMEOUT_S` | config | core-service | 3.05 | 3.05 | 3.05 | fly_env |
| `APPWRITE_DATABASE_ID` | config | app-admin | main | main | main | host_env |
| `APPWRITE_DATABASE_ID` | config | core-service | main | main | main | fly_env |
| `APPWRITE_ENDPOINT` | config | app-admin | https://fra.cloud.appwrite.io/v1 | https://fra.cloud.appwrite.io/v1 | https://fra.cloud.appwrite.io/v1 | host_env |
| `APPWRITE_ENDPOINT` | config | core-service | https://fra.cloud.appwrite.io/v1 | https://fra.cloud.appwrite.io/v1 | https://fra.cloud.appwrite.io/v1 | fly_env |
| `APPWRITE_PROJECT_ID` | config | app-admin | own project id | own project id | own project id | host_env |
| `APPWRITE_PROJECT_ID` | config | core-service | own project id | own project id | own project id | fly_env |
| `APPWRITE_READ_TIMEOUT_S` | config | core-service | 10 | 10 | 10 | fly_env |
| `APP_VERSION` | config | core-service | set by build | set by build | set by build | fly_env |
| `AUDIT_LEAD_BLANKET_READ` | config | core-service | true | true | true | fly_env |
| `AUDIT_RECOVER_GRACE_S` | config | core-service | 300 | 300 | 300 | fly_env |
| `AUDIT_RECOVER_LOOKBACK_HOURS` | config | core-service | 6 | 6 | 6 | fly_env |
| `CATALOGUE_BUNDLE_MAX_BYTES` | config | core-service | 1048576 | 1048576 | 1048576 | fly_env |
| `CATALOGUE_CACHE_ENTRIES` | config | core-service | 32 | 32 | 32 | fly_env |
| `CATALOGUE_CURRENT_MAX_AGE_S` | config | core-service | 60 | 60 | 60 | fly_env |
| `CATALOGUE_IMMUTABLE_MAX_AGE_S` | config | core-service | 31536000 | 31536000 | 31536000 | fly_env |
| `BILLING_GRACE_DAYS` | config | core-service | 3 | 3 | 3 | fly_env |
| `BILLING_MAX_PAUSE_DAYS` | config | core-service | 30 | 30 | 30 | fly_env |
| `BILLING_PAST_DUE_CANCEL_DAYS` | config | core-service | 14 | 14 | 14 | fly_env |
| `BILLING_RENEWAL_LEAD_DAYS` | config | core-service | 5 | 5 | 5 | fly_env |
| `BILLING_ROLL_CHUNK_SIZE` | config | core-service | 20 | 20 | 20 | fly_env |
| `CORE_API_URL` | config | app-admin | http://localhost:8080 or unset | https URL of the staging core service | https URL of the production core service | host_env |
| `CORE_SERVICE_URL` | config | job-tick-function | own core service URL | own core service URL | own core service URL | appwrite_function_variable |
| `GIT_SHA` | config | core-service | set by build | set by build | set by build | fly_env |
| `HS_DEBUG` | config | core-service | false | false | false | fly_env |
| `HS_ENV` | config | core-service | dev | staging | production | fly_env |
| `HS_PRODUCTION_ADMIN_URL` | config | ops-uptime | - | - | https base URL of the production admin app (hostname, owner-blocked until it exists) | provider_console |
| `HS_PRODUCTION_API_URL` | config | ops-uptime | - | - | https base URL of the production core service (hostname, owner-blocked until it exists) | provider_console |
| `HS_STAGING_ADMIN_URL` | config | ops-uptime | - | https base URL of the staging admin app (hostname, owner-blocked until it exists) | - | provider_console |
| `HS_STAGING_API_URL` | config | ops-uptime | - | https base URL of the staging core service (hostname, owner-blocked until it exists) | - | provider_console |
| `JOB_ALERT_AFTER_FAILURES` | config | core-service | 3 | 3 | 3 | fly_env |
| `LOG_LEVEL` | config | core-service | DEBUG | INFO | INFO | fly_env |
| `MOMO_COLLECTION_API_USER` | config | core-service | own user id | own user id | own user id | fly_env |
| `MOMO_DISBURSEMENT_API_USER` | config | core-service | own user id | own user id | own user id | fly_env |
| `NOTIFY_ESCALATE_AFTER_MIN` | config | core-service | 15 | 15 | 15 | fly_env |
| `NOTIFY_LEASE_S` | config | core-service | 120 | 120 | 120 | fly_env |
| `NOTIFY_MAX_ATTEMPTS` | config | core-service | 4 | 4 | 4 | fly_env |
| `NOTIFY_QUIET_END_HOUR` | config | core-service | 7 | 7 | 7 | fly_env |
| `NOTIFY_QUIET_START_HOUR` | config | core-service | 21 | 21 | 21 | fly_env |
| `NOTIFY_RATE_LIMIT` | config | core-service | 5 | 5 | 5 | fly_env |
| `NOTIFY_RATE_WINDOW_S` | config | core-service | 600 | 600 | 600 | fly_env |
| `NOTIFY_RETRY_BASE_S` | config | core-service | 60 | 60 | 60 | fly_env |
| `OTP_APP_HASH` | config | core-service | (empty until the release signing key exists) | (empty until the release signing key exists) | (empty until the release signing key exists) | fly_env |
| `OTP_DORMANT_DAYS` | config | core-service | 180 | 180 | 180 | fly_env |
| `OTP_IP_REQUESTS_PER_DAY` | config | core-service | 60 | 60 | 60 | fly_env |
| `OTP_IP_REQUESTS_PER_HOUR` | config | core-service | 20 | 20 | 20 | fly_env |
| `OTP_IP_VERIFIES_PER_HOUR` | config | core-service | 60 | 60 | 60 | fly_env |
| `OTP_MAX_ATTEMPTS` | config | core-service | 5 | 5 | 5 | fly_env |
| `OTP_PHONE_FAILURES_PER_HOUR` | config | core-service | 10 | 10 | 10 | fly_env |
| `OTP_PHONE_REQUESTS_PER_DAY` | config | core-service | 10 | 10 | 10 | fly_env |
| `OTP_PHONE_REQUESTS_PER_HOUR` | config | core-service | 5 | 5 | 5 | fly_env |
| `OTP_RESEND_COOLDOWN_S` | config | core-service | 30 | 30 | 30 | fly_env |
| `OTP_TTL_S` | config | core-service | 300 | 300 | 300 | fly_env |
| `TRAVEL_CACHE_TTL_DAYS` | config | core-service | 7 | 7 | 7 | fly_env |
| `TRAVEL_DAILY_ELEMENT_BUDGET` | config | core-service | 1000 | 1000 | 1000 | fly_env |
| `TRAVEL_ELEMENT_COST_MICRO_USD` | config | core-service | 10000 | 10000 | 10000 | fly_env |
| `TRAVEL_FALLBACK_CIRCUITY` | config | core-service | 1.4 | 1.4 | 1.4 | fly_env |
| `TRAVEL_FALLBACK_SPEED_KMH` | config | core-service | 20 | 20 | 20 | fly_env |
| `TRAVEL_MONTHLY_ELEMENT_BUDGET` | config | core-service | 15000 | 15000 | 15000 | fly_env |
| `TRAVEL_NEGATIVE_TTL_S` | config | core-service | 300 | 300 | 300 | fly_env |
| `TRAVEL_WARM_BANDS` | config | core-service | evening_peak,off_peak | evening_peak,off_peak | evening_peak,off_peak | fly_env |
| `TRAVEL_WITHIN_ZONE_MINUTES` | config | core-service | 10 | 10 | 10 | fly_env |
<!-- END GENERATED:matrix -->

## Rules

1. **Three environments, three of everything** (SR §4): each has its own Appwrite project, Fly app, GitHub environment, keys, buckets and payment credentials. A store name always ends with its environment; the validator rejects a development entry that points at a production store and any store shared between environments.
2. **Production secrets never exist in development or staging.** Development and staging use provider sandboxes (MoMo sandbox, Airtel sandbox, card test mode). A `live` value outside production fails the validator. Developers' laptops (`local-dev`) may only hold development sandbox values.
3. **Server secrets never go into an app or browser bundle** (SEC-3). Only `public client key` rows may be build defines, and each must be restricted at the provider (package id or bundle id, API allowlist, quota cap). Appwrite has no anon key: clients carry the public project id and endpoint only.
4. **Fail fast.** The core service refuses to start in staging or production when a required variable is missing, blank, a placeholder, or insecure (F03 `load_settings`). The error names variables, never values. Tested in `services/core/tests/test_config_inventory.py`.
5. **`.env.example` lists every variable the service reads, all empty.** `services/core/.env.example` is checked against `Settings` by the validator and by the core test suite. `.env` and `.env.*` are git-ignored; gitleaks allow-lists `.env.example`, so the empty-value check is what protects it.
6. **Adding a variable** = add it to `Settings` (or the app), to `.env.example`, to the inventory (kind, stores, and for server secrets owner, interval, blast radius, procedure), then run `--write`. The validator fails until all four agree.

## Local development

Copy `services/core/.env.example` to `services/core/.env` (git-ignored). With `HS_ENV=dev` (the default) the service starts with no variables; to use a real Appwrite development project, put that project's development key in `.env`, never a staging or production one.
