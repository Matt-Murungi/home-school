# apps/teacher

## Purpose

Teacher mobile app (Flutter): onboarding, availability, session check-in, logs, earnings.

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

Phone sign-in (A01) calls the core service at `HS_CORE_URL` (https outside dev; in dev an emulator can use `http://10.0.2.2:8080`). `HS_ENV` is `dev` (default), `staging` or `prod`. Android minSdk is 26 (Android 8). Application ids use the placeholder `ug.homeschool.home_school_teacher`; final bundle/app ids are an owner decision.

## Application flow (V01)

`lib/application/` is the teacher application wizard (T-03 .. T-08): personal details and photo, qualifications and curricula, documents (placeholder until V02), referees, zones and availability, review and submit. The draft is kept in a local Drift database (`teacher_application.sqlite`), pushed to the core service (`/v1/teacher/application`) after a short pause and retried when offline; the server copy is reconciled by revision. Strings are in `lib/l10n/app_en.arb` (`flutter gen-l10n`); the local database code is generated (`dart run build_runner build --delete-conflicting-outputs`). Validation rules are shared with the server through `test/fixtures/application_cases.json`.
