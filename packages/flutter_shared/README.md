# packages/flutter_shared

## Purpose

Dart package shared by both Flutter apps: Appwrite client adapter, models, theme, Kampala time helpers.

## Test command

`flutter test` (run from this directory), or `make test` from the repo root, which runs every package listed in `packages.json`.

## Status

Scaffolded (F05). Contents: design tokens and theme (`lib/src/theme`), `AppConfig` (`--dart-define` environments), `ApiClient` + `FakeApiClient`, `AuthService` + `FakeAuthService` + `AppwriteAuthService` adapter, placeholder models, Drift offline queue (`AppDatabase`, `OfflineQueue`), go_router `buildAppRouter`, widgets, and strings in `lib/src/l10n/app_en.arb`.

Phone sign-in (A01): `AuthService.requestOtp/verifyOtp/refreshSession`, `CoreOtpApi` (calls the core service), `OtpFlowController` (Riverpod), `PhoneEntryScreen`/`CodeEntryScreen` (routes `/sign-in/phone`, `/sign-in/code`), `SessionVault` over `flutter_secure_storage`, `SessionRefresher`. `test/fixtures/ug_phone_cases.json` is the phone-format contract shared with `services/core`.

Roles and routing (A03): `auth/roles.dart` (labels, `AppId`, `decideApp`), `SessionController`/`sessionProvider`, the guarded `buildAppRouter` (`nav/route_guard.dart`: every route is protected unless listed public; deep links remembered; no protected content is built before the session is known; `landingPath` and `tabBuilder` hooks), `WrongAppScreen`, `SuspendedScreen`, `StartScreen`. `docs/ops/roles-matrix.json` is the role contract shared with the core service and the admin app. Design: `docs/ops/roles.md`.

Generated code is committed. After editing the ARB run `flutter gen-l10n`; after editing Drift tables run `dart run build_runner build --delete-conflicting-outputs`.
