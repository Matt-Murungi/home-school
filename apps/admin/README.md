# apps/admin

## Purpose

Admin web dashboard (Next.js App Router, TypeScript; ADR 0004) for operations admins, curators, the safeguarding lead and super admins. F06 scaffold: Appwrite SSR session auth with an MFA step, layout shell (sidebar, top bar with search placeholder, admin and role, environment badge), route guards by role label, empty pages for every nav item, a server-paginated data table and server-validated form helpers. Screens themselves come in later tickets.

## Test command

`npm ci && npx tsc --noEmit && npm test` (run from this directory), then `npm run lint` and `npm run build`. `make test` from the repo root runs the same via `packages.json`. Tests are offline (vitest + React Testing Library, fake auth provider). The "no API key in the client bundle" test also scans `.next/static` when a build exists.

Playwright is not used: browser binaries need a download, and tests must run offline.

## Rules this app follows

- **No Appwrite API key, ever.** Every request builds a client scoped to the signed-in admin's session (`setSession`) so row permissions and the audit trail apply. Trusted writes go to core-service endpoints (SR 6.3). Enforced by `tests/no-api-key.test.ts`.
- Session cookie `hs_admin_session` (httpOnly, sameSite strict, secure in production) is set only by the server route `src/app/api/session/route.ts`. The pending-MFA cookie is HMAC-signed with `ADMIN_COOKIE_SECRET` and carries an issue time the server checks (10 minutes); an expired or cancelled pending MFA deletes its password-only Appwrite session (best effort). `/api/session` accepts same-origin form POSTs only (403 cross-site, 400 non-form) and has no GET, so logout cannot be triggered by a link. `GET /api/session/clear` is an internal redirect target for an invalid session: it clears the cookie only when it is not a valid admin session.
- MFA sits behind `AuthProvider` (`src/lib/auth/types.ts`): `AppwriteAuthProvider` for real, `FakeAuthProvider` for tests. Admins without MFA enrolled are refused.
- Roles are Appwrite user labels, alphanumeric: `opsadmin`, `curator`, `safeguardinglead`, `superadmin` (ADR 0012). Which role sees which nav item is in `src/lib/nav.ts` (follows SR 6.2 and A-30 where they speak; `superadmin` is not implicit and sees only Overview and Settings unless it also holds another label; unconfirmed guesses are listed in the file).
- **Every admin page, server action and route handler must call `requireAccess("/route")` from `src/lib/session.ts`.** The layout guard is not enough: Next.js skips layouts on client navigations and on RSC requests that already hold them (`tests/page-guard.test.ts` parses every page, route handler, layout and server action under `src/app` with the TypeScript compiler and requires a real top-level `await requireAccess(...)` call; intentionally public routes are an allowlist in that test, each with a reason). A session is only accepted when MFA is enrolled (`src/lib/auth/guard.ts`).
- Lists: Appwrite caps `total` at 5,000. `DataTable` shows `5,000+` and offers Previous/Next only at the cap (`src/lib/pagination.ts`).
- Environment badge (Staging/Production/Local) comes from `ADMIN_ENV`; missing or unknown means Local, never Production.

## Configuration

Copy `.env.example` to `.env.local` and fill `ADMIN_ENV`, `APPWRITE_ENDPOINT`, `APPWRITE_PROJECT_ID` and (production, required) `ADMIN_COOKIE_SECRET` (`openssl rand -hex 32`). There is no key variable. Real login and MFA against a staging project is owner-blocked.
