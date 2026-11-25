# Mobile builds to Play internal testing and TestFlight (F07, #33) — OWNER-BLOCKED

Acceptance criterion "Mobile builds uploaded to Play internal testing and TestFlight" cannot be built or
verified by an agent: it needs the owner's store accounts, signing keys and a provider decision. This is the
documented skeleton. `.github/workflows/mobile-builds.yml` is dispatch-only, behind `DEPLOY_ENABLED` and
`MOBILE_BUILDS_ENABLED`, and does nothing today.

## Option A: Codemagic (recommended: macOS builders and store publishing included)

Add `codemagic.yaml` at the repo root when the owner connects the repo (not added now: no account, and no
guessing of bundle ids). Workflows `parent-internal` and `teacher-internal`: trigger on push to `main` touching
`apps/<app>/**` or `packages/flutter_shared/**`; `flutter build appbundle` / `flutter build ipa` with
`--dart-define=HS_ENV=staging --dart-define=HS_APPWRITE_ENDPOINT=... --dart-define=HS_APPWRITE_PROJECT=...`
(public values only, F05); publish to Google Play `internal` and App Store Connect TestFlight.

## Option B: Fastlane

`apps/<app>/android/fastlane` lane `internal` (`upload_to_play_store track: "internal"`) and
`apps/<app>/ios/fastlane` lane `beta` (`build_app`, `upload_to_testflight`) run from a macOS GitHub runner
in a `mobile` environment with required reviewers.

## Owner steps (exact)

1. Pick Codemagic or Fastlane.
2. Decide bundle/application ids (placeholders `ug.homeschool.home_school_parent|teacher`, F05) and the brand name.
3. Google Play Console: create the apps, enroll in Play App Signing, create a service account with Release Manager
   rights for the internal track; store the JSON as a Codemagic/GitHub environment secret (never in the repo).
4. Apple Developer: App Store Connect apps, an App Store Connect API key (Issuer ID, Key ID, .p8), distribution
   certificate and profiles (or Codemagic managed signing).
5. Release keystore for Android (generate once, back it up offline, store base64 as a secret).
6. Provide the staging `HS_APPWRITE_ENDPOINT` / `HS_APPWRITE_PROJECT` values (public client ids, F09).
7. Set repository variable `MOBILE_BUILDS_ENABLED=true` (Fastlane option) once secrets exist.

Never commit keystores, .p8 files or service-account JSON (`make secrets` and gitleaks guard this, F09).
