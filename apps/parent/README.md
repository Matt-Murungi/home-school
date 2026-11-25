# apps/parent

## Purpose

Parent mobile app (Flutter): onboarding, plans, scheduling, session day, payments.

## Test command

`flutter test` (run from this directory), or `make test` from the repo root, which runs every package listed in `packages.json`.

## Status

Scaffolded (F05). Boots to a themed welcome screen, then a bottom-tab shell with placeholder tabs (Design Brief section 8). Uses `packages/flutter_shared`.

Run against an environment (only public values; never secrets):

```
flutter run --dart-define=HS_ENV=staging \
  --dart-define=HS_APPWRITE_ENDPOINT=https://<host>/v1 \
  --dart-define=HS_APPWRITE_PROJECT=<project-id> \
  --dart-define=HS_CORE_URL=https://<core-service-host>
```

Phone sign-in (A01) calls the core service at `HS_CORE_URL` (https outside dev; in dev an emulator can use `http://10.0.2.2:8080`). `HS_ENV` is `dev` (default), `staging` or `prod`. Android minSdk is 26 (Android 8). Application ids use the placeholder `ug.homeschool.home_school_parent`; final bundle/app ids are an owner decision.
