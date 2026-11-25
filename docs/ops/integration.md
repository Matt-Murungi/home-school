# Error reporting integration notes (apps, admin, worker)

All clients follow the same rules as `services/core/src/hs_core/observability/scrub.py` (see `runbook.md`, "PII rules"). The DSN comes from the build/runtime environment, is never committed, and an empty DSN disables reporting.

## Core service and worker (built, F08)

`ErrorReporter` (`hs_core.observability`): `SentryReporter` (own client, no global hooks), `NoopReporter` (no DSN), `FakeReporter` (tests). Config: `SENTRY_DSN` (https), `HS_ENV` as the Sentry environment, `GIT_SHA` (else `APP_VERSION`) as the release. Unhandled 5xx are reported once with the `request_id` tag. Jobs (F04) and payment/payout code report with `reporter.capture_message(..., tags={"alert": "job-failure"})` or `capture_exception`. Under pytest a real network transport is refused. Appwrite Functions (if any run Python/Node outside the core service) must use the same scrubber: copy `scrub.py` or call the core service.

## Flutter apps (parent, teacher) (hook for F05, not built here)

- Package `sentry_flutter`; initialise in `packages/flutter_shared` (one `initMonitoring()` used by both apps): `options.dsn = const String.fromEnvironment('SENTRY_DSN')`, `options.environment = const String.fromEnvironment('HS_ENV')`, `options.release` = app version; skip `SentryFlutter.init` when the DSN is empty.
- Build with `--dart-define=SENTRY_DSN=... --dart-define=HS_ENV=staging`.
- Required options: `sendDefaultPii = false`, `attachScreenshot = false`, `attachViewHierarchy = false`, `maxRequestBodySize = never`, `beforeSend` and `beforeBreadcrumb` ported from `scrub.py` (same key list, same regexes; add a Dart test with the same realistic payloads as `services/core/tests/test_scrub.py`). Set the user to the Appwrite user id only.
- Crash while offline (F08 edge case): the Sentry Flutter SDK writes envelopes to disk and sends them on next launch or when connectivity returns, so the crash is queued and sent later. Keep the default offline cache on; test with airplane mode on a device (owner/device step).

## Admin dashboard (Next.js, F06) (hook, not built here)

- `@sentry/nextjs`; DSN from `NEXT_PUBLIC_SENTRY_DSN` / `SENTRY_DSN`, disabled when empty; `sendDefaultPii: false`; `beforeSend` ported from `scrub.py`; no session replay (it records the screen: personal data).
- Tag errors with the request id returned by the core service (`X-Request-ID` response header) so a failed call can be matched to the core log line.

## Request ids across systems

Clients send `X-Request-ID` (`[A-Za-z0-9._-]{1,64}`); the core service echoes it, writes it on every log line and uses it as the Sentry `request_id` tag. Support looks up a user-reported id in both Sentry and the logs.

## Decisions and edge cases

- Monitoring down: heartbeat alert plus a second independent uptime monitor (`runbook.md`).
- Reporter failure never breaks a request (`SentryReporter` swallows transport errors); scrub failure drops the event.
- Quota exhaustion (free tier): Sentry rate limits silently; the monthly test and the heartbeat reveal a silent pipeline. Raise to a paid plan only with owner approval.
