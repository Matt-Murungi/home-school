# ADR 0004 — Admin dashboard framework

- **Status:** Proposed — awaiting owner sign-off
- **Date:** 24 November 2026
- **Ticket:** D01 (#21)
- **Related:** System Requirements v0.2 §3 and §14; ADR 0012 (Appwrite Cloud replaces Supabase)

> **Numbering note.** The D01 ticket asks for `0001-admin-framework.md` and a spike wired to Supabase. That ticket predates ADR 0012. System Requirements v0.2 already cites this decision as ADR 0004, so this record uses that number and targets Appwrite.

## Context

The admin dashboard is a desktop-first web app (1280 px and wider, usable on tablet) for operations admins, curators, the safeguarding lead and super admins. It holds most of the platform's dense, data-heavy screens. Examples are work queues (A-02), the teachers, families and sessions lists (A-06, A-08, A-13), the matching console (A-10), payments, reconciliation and payout batches (A-20, A-21), safeguarding cases (A-30), the curriculum and question editors (A-40 to A-42), settings (A-50) and the audit log (A-53).

It must:

- meet NFR-4: lists and searches return within 2 seconds for up to 100,000 sessions
- use Appwrite Auth with MFA for admin roles (SEC-4)
- respect Appwrite row permissions (SR §6.2)
- never hold the server API key in the browser (SR §6.3)

The two mobile apps are Flutter, so Flutter Web is the natural alternative to a separate web stack.

## Decision drivers

| Driver | Weight | Why it matters here |
|---|---|---|
| Data tables | High | Server-side pagination, sorting, filtering, column choice, bulk actions and CSV export on most screens |
| Forms | High | Long validated forms: application review, settings, question editor, dispute resolution |
| Auth with Appwrite | High | Phone or email login, MFA, sessions handled server-side so row permissions apply to each admin |
| Developer and AI-agent familiarity | High | Tickets are sized for developers *and* AI coding agents. Output quality depends on how much public code exists for the stack |
| Hiring | Medium | Kampala and remote web developers are far more numerous for React than for Flutter Web |
| One language with the mobile apps | Medium | Shared models and API client; one skill set |
| Desktop web quality | Medium | Text selection, browser find, deep links, many open tabs, fast first load |

## Options considered

### A. Next.js (React, TypeScript) with the Appwrite SDKs — chosen

- **Tables:** mature headless and full-featured grids (TanStack Table, AG Grid Community, MUI X), plus admin frameworks (Refine, React Admin) if wanted.
- **Forms:** React Hook Form or server actions with Zod schemas. Validation can be shared between client and server.
- **Auth:** Appwrite supports server-side rendering (SSR) auth. The session cookie is set by a server route, and each request builds a client scoped to that session (`setSession`), so permissions apply to the signed-in admin. MFA (TOTP or email) uses the Appwrite Account API.
- **Familiarity:** the largest body of public examples of any web stack. AI agents produce correct Next.js and React code with the least correction.
- **Costs:** a second language and toolchain (TypeScript next to Dart and Python), and no code sharing with the Flutter apps.

### B. Flutter Web with the Appwrite Dart SDK

- **One language:** shared models, API client and auth package with the mobile apps.
- **Tables:** fewer options. `DataTable` and `PaginatedDataTable` are basic, and the richer grids (e.g. Syncfusion, PlutoGrid) are commercially licensed or less maintained.
- **Desktop web:** canvas rendering hurts text selection, browser find, accessibility and initial load size. These matter in a tool used all day.
- **Familiarity:** much less public code for admin-style Flutter Web apps, so agent output needs more review.

### C. Low-code admin (e.g. Appsmith, Retool) — rejected early

This option is fast for CRUD. The trade-offs are vendor lock-in and per-seat cost, plus weak support for the custom matching console and safeguarding workflows. It would also put a second permissions model alongside Appwrite's.

## Decision

Build the admin dashboard in **Next.js (App Router, React, TypeScript)**:

- Use the **Appwrite web and Node SDKs** to read data.
- Send trusted writes (money, tiers, session status, check-in validity, payouts) to **core-service endpoints**, never directly to Appwrite (SR §6.3).

## Consequences

- **Positive:** strong table and form tooling; the fastest delivery for agents and hires; a normal browser experience for admins.
- **Negative:** a third language in the codebase. Shared types between the admin and the core service will need generating (e.g. from the core service's OpenAPI schema) rather than sharing Dart models.
- **Security rules for F06 and every admin ticket:**
  - The Appwrite API key lives only in the core service. The admin app acts as the signed-in admin through an SSR session cookie, so row permissions and the audit trail (SEC-6) apply.
  - The spike uses an API key only to stay small; that is not a pattern to copy.
- **Pagination rule:**
  - Use offset pagination with page numbers where the total is small or filtered.
  - Use cursor pagination (`Query.cursorAfter`) for unfiltered lists that will reach 100,000 rows (NFR-4).
  - Every list query needs an Appwrite index that matches its filter and sort.
- **Reversal trigger:** if the spike or later work shows Appwrite SSR auth or MFA cannot be done cleanly in Next.js, record it here and re-open the decision (D01 edge case).

## Spike evidence

The spike is in [`spikes/admin-appwrite/`](../../spikes/admin-appwrite/). It builds a sessions table with server-side pagination and status filtering, a validated form that writes a row, and seed and timing scripts.

| Check | Target | Result |
|---|---|---|
| Builds, type-checks, lints | Clean | ✅ Pass (24 Nov 2026) |
| Paginated table of 10,000 rows, every page including the deepest offset | Under 2 s | ✅ Pass (25 Nov 2026). Full page response median 314–350 ms, worst 984 ms. Appwrite query 290–380 ms |
| Form writes a row and shows field errors | Works | ✅ Pass. Invalid input returns one error per field; valid input creates the row and redirects. Kampala time is stored as UTC |

How the timing was run:

- Five requests per page against the production build (`next start`) on a laptop in Kampala.
- Target: Appwrite Cloud Frankfurt, a temporary `zz_spike_sessions` table in the Home School project's `main` database, deleted after the run. The plan allows only one database.
- Pages tested: 1, 2, 100 and 200 (offset 9,950), plus two status-filtered pages.

**Finding: Appwrite caps list totals at 5,000.** `listRows` returned `total: 5000` for the 10,000-row table, although every row up to offset 9,950 was returned. Counting the true total by paging with a cursor took 4.7 s, too slow for a page load. Consequences for the admin dashboard:

- Lists show "5,000+" at the cap. Paging moves forward with "Next" while pages come back full, and offers no "Last" page.
- Where an exact count matters (overview numbers, reports, queue badges), the core service keeps counters, for example a count row updated on each state change, or computes them in a scheduled job. Pages never count at request time.
- Unfiltered lists that will grow towards 100,000 rows (NFR-4) use cursor pagination (`Query.cursorAfter`) rather than deep offsets.
- When the true count is an exact multiple of the page size, the last full page still offers "Next", which leads to an empty page. This is acceptable for admins; the empty page says "No sessions match".

## Sign-off

- [ ] Owner approves this ADR (change Status to *Accepted*)
