# apps/admin

## Purpose

Admin web dashboard (Next.js App Router, TypeScript; ADR 0004) for operations admins, curators, the safeguarding lead and super admins. F06 scaffold: Appwrite SSR session auth with an MFA step, layout shell (sidebar, top bar with search placeholder, admin and role, environment badge), route guards by role label, empty pages for every nav item, a server-paginated data table and server-validated form helpers. Screens themselves come in later tickets.

## Test command

`npm ci && npx tsc --noEmit && npm test` (run from this directory), then `npm run lint` and `npm run build`. `make test` from the repo root runs the same via `packages.json`. Tests are offline (vitest + React Testing Library, fake auth provider). The "no API key in the client bundle" test also scans `.next/static` when a build exists.

Playwright is not used: browser binaries need a download, and tests must run offline.

## Rules this app follows

- **No Appwrite API key, ever.** Every request builds a client scoped to the signed-in admin's session (`setSession`) so row permissions and the audit trail apply. Trusted writes go to core-service endpoints (SR 6.3). Enforced by `tests/no-api-key.test.ts`.
- Session cookie `hs_admin_session` (httpOnly, sameSite strict, secure in production) is set only by the server route `src/app/api/session/route.ts`. A pending-MFA cookie lives 10 minutes.
- MFA sits behind `AuthProvider` (`src/lib/auth/types.ts`): `AppwriteAuthProvider` for real, `FakeAuthProvider` for tests. Admins without MFA enrolled are refused.
- Roles are Appwrite user labels, alphanumeric: `opsadmin`, `curator`, `safeguardinglead`, `superadmin` (ADR 0012). Which role sees which nav item is in `src/lib/nav.ts` (assumption to confirm with the owner).
- Lists: Appwrite caps `total` at 5,000. `DataTable` shows `5,000+` and offers Previous/Next only at the cap (`src/lib/pagination.ts`).
- Environment badge (Staging/Production/Local) comes from `ADMIN_ENV`; missing or unknown means Local, never Production.

## Configuration

Copy `.env.example` to `.env.local` and fill `ADMIN_ENV`, `APPWRITE_ENDPOINT`, `APPWRITE_PROJECT_ID`. There is no key variable. Real login and MFA against a staging project is owner-blocked.
