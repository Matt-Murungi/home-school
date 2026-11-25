# Configuration matrix (F09, #35)

Which variable exists in which environment, what kind it is and where it lives. The table is **generated** from `docs/ops/secrets-inventory.json` (`python3 scripts/secrets_check.py --write`) and checked in CI-style by `python3 scripts/secrets_check.py`, so it cannot drift from the inventory. Cells for secrets name the secrets store (never a value); `(sandbox)` and `(live)` say which provider account the value belongs to.

<!-- BEGIN GENERATED:matrix -->
| Variable | Kind | Consumer | development | staging | production | Where it lives |
|---|---|---|---|---|---|---|
| `AFRICASTALKING_API_KEY` | server secret | core-service | fly:hs-core-development | fly:hs-core-staging | fly:hs-core-production | fly_secret |
| `AIRTEL_CLIENT_SECRET` | server secret | core-service | fly:hs-core-development (sandbox) | fly:hs-core-staging (sandbox) | fly:hs-core-production (live) | fly_secret |
| `APNS_AUTH_KEY` | server secret | firebase-console | provider-console:firebase-development | provider-console:firebase-staging | provider-console:firebase-production | provider_console |
| `APPWRITE_API_KEY` | server secret | ci-deploy | github-env:development | github-env:staging | github-env:production | github_actions_secret |
| `APPWRITE_API_KEY` | server secret | core-service | fly:hs-core-development | fly:hs-core-staging | fly:hs-core-production | fly_secret |
| `CARD_GATEWAY_SECRET_KEY` | server secret | core-service | fly:hs-core-development (sandbox) | fly:hs-core-staging (sandbox) | fly:hs-core-production (live) | fly_secret |
| `CARD_GATEWAY_WEBHOOK_SECRET` | server secret | core-service | fly:hs-core-development (sandbox) | fly:hs-core-staging (sandbox) | fly:hs-core-production (live) | fly_secret |
| `FCM_SERVICE_ACCOUNT_JSON` | server secret | core-service | fly:hs-core-development | fly:hs-core-staging | fly:hs-core-production | fly_secret |
| `FLY_API_TOKEN` | server secret | ci-deploy | github-env:development | github-env:staging | github-env:production | github_actions_secret |
| `GOOGLE_MAPS_SERVER_KEY` | server secret | core-service | fly:hs-core-development | fly:hs-core-staging | fly:hs-core-production | fly_secret |
| `JOB_TICK_SECRET` | server secret | core-service | fly:hs-core-development | fly:hs-core-staging | fly:hs-core-production | fly_secret |
| `JOB_TICK_SECRET` | server secret | job-tick-function | appwrite-fn:job-tick-development | appwrite-fn:job-tick-staging | appwrite-fn:job-tick-production | appwrite_function_variable |
| `MOMO_COLLECTION_API_KEY` | server secret | core-service | fly:hs-core-development (sandbox) | fly:hs-core-staging (sandbox) | fly:hs-core-production (live) | fly_secret |
| `MOMO_COLLECTION_SUBSCRIPTION_KEY` | server secret | core-service | fly:hs-core-development (sandbox) | fly:hs-core-staging (sandbox) | fly:hs-core-production (live) | fly_secret |
| `MOMO_DISBURSEMENT_API_KEY` | server secret | core-service | fly:hs-core-development (sandbox) | fly:hs-core-staging (sandbox) | fly:hs-core-production (live) | fly_secret |
| `MOMO_DISBURSEMENT_SUBSCRIPTION_KEY` | server secret | core-service | fly:hs-core-development (sandbox) | fly:hs-core-staging (sandbox) | fly:hs-core-production (live) | fly_secret |
| `RESEND_API_KEY` | server secret | core-service | fly:hs-core-development | fly:hs-core-staging | fly:hs-core-production | fly_secret |
| `RESEND_WEBHOOK_SECRET` | server secret | core-service | fly:hs-core-development | fly:hs-core-staging | fly:hs-core-production | fly_secret |
| `SENTRY_AUTH_TOKEN` | server secret | ci-deploy | github-env:development | github-env:staging | github-env:production | github_actions_secret |
| `SENTRY_DSN` | server secret | core-service | fly:hs-core-development | fly:hs-core-staging | fly:hs-core-production | fly_secret |
| `GOOGLE_MAPS_ANDROID_KEY` | public client key | app-parent | build define | build define | build define | build_define |
| `GOOGLE_MAPS_IOS_KEY` | public client key | app-parent | build define | build define | build define | build_define |
| `SENTRY_DSN_APP` | public client key | app-parent | build define | build define | build define | build_define |
| `SENTRY_DSN_APP` | public client key | app-teacher | build define | build define | build define | build_define |
| `ADMIN_ENV` | config | app-admin | local | staging | production | host_env |
| `AFRICASTALKING_USERNAME` | config | core-service | sandbox in dev/staging | sandbox in dev/staging | sandbox in dev/staging | fly_env |
| `APPWRITE_DATABASE_ID` | config | core-service | main | main | main | fly_env |
| `APPWRITE_ENDPOINT` | config | app-admin | https://fra.cloud.appwrite.io/v1 | https://fra.cloud.appwrite.io/v1 | https://fra.cloud.appwrite.io/v1 | host_env |
| `APPWRITE_ENDPOINT` | config | core-service | https://fra.cloud.appwrite.io/v1 | https://fra.cloud.appwrite.io/v1 | https://fra.cloud.appwrite.io/v1 | fly_env |
| `APPWRITE_PROJECT_ID` | config | app-admin | own project id | own project id | own project id | host_env |
| `APPWRITE_PROJECT_ID` | config | core-service | own project id | own project id | own project id | fly_env |
| `APP_VERSION` | config | core-service | set by build | set by build | set by build | fly_env |
| `CORE_SERVICE_URL` | config | job-tick-function | own core service URL | own core service URL | own core service URL | appwrite_function_variable |
| `GIT_SHA` | config | core-service | set by build | set by build | set by build | fly_env |
| `HS_DEBUG` | config | core-service | false | false | false | fly_env |
| `HS_ENV` | config | core-service | dev | staging | production | fly_env |
| `JOB_ALERT_AFTER_FAILURES` | config | core-service | 3 | 3 | 3 | fly_env |
| `LOG_LEVEL` | config | core-service | DEBUG | INFO | INFO | fly_env |
| `MOMO_COLLECTION_API_USER` | config | core-service | own user id | own user id | own user id | fly_env |
| `MOMO_DISBURSEMENT_API_USER` | config | core-service | own user id | own user id | own user id | fly_env |
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
