# ADR 0012 — Appwrite Cloud (Frankfurt) as the backend, and the permissions model that replaces Postgres RLS

- **Status:** Proposed — awaiting owner sign-off
- **Date:** 25 November 2026
- **Ticket:** D03 (#23)
- **Related:** System Requirements v0.2 §3, §4, §6, §10 (SEC-2, SEC-3, SEC-5), §11 (DP-7), §12 (NFR-6, NFR-15), §14 (open item 2); ADR 0002 (job runner); ADR 0003 (Fly.io core service); ADR 0004 (admin dashboard)

## Context

The earlier plan used Supabase: Postgres, with Row Level Security (RLS) written as SQL policies, so the database itself decided who could read which row. System Requirements v0.2 replaced it with **Appwrite Cloud, Frankfurt**: Auth, TablesDB, Storage, Realtime and Functions. An Appwrite project already exists.

Appwrite differs from Postgres in ways that matter for a platform holding children's data and money:

- **Permissions are data on each row.** A row carries a list such as `read("user:abc")`. They are not computed from a policy expression. Someone has to write that list, and keep it correct, as business facts change.
- **Permissions are per row (and per file), not per column.** A row's readers see all of its columns.
- **No conditional update, no triggers, no exclusion constraints, and no multi-row constraint checks.** Appwrite's own transactions feature, if available on our plan, is not relied on here (verify before using).
- **List totals are capped at 5,000** (ADR 0004).
- **A server API key bypasses permissions entirely.** It is held only by the core service (SR §6.3).

SR §6.2 says: "Clients hold read permission only; the core service grants and revokes it as assignments, cases and roles change (ADR 0012)". This ADR makes that concrete.

## Decision drivers

| Driver | Weight |
|---|---|
| A parent, teacher or admin can never read data they are not entitled to (SEC-2, SR §6.2) | Critical |
| Clients cannot change money, tier, session status or check-in validity (SR §6.3) | Critical |
| Access that has ended is revoked promptly, notably the home address 24 h after the last session | High |
| When a permission change and a business change disagree, the failure is safe (too little access, never too much) and is detected | High |
| Testable: access rules are tested by acting as each role (NFR-15) | High |
| Region and legal position (DP-7) | Open |
| Cost and operational burden | Medium |

## Options considered

### Backend platform

| Option | Verdict |
|---|---|
| **A. Appwrite Cloud, Frankfurt** | **Chosen.** Already provisioned; Auth with phone OTP and MFA, Storage, Realtime and Functions in one product; EU region; no database administration. Weaker data guarantees than Postgres (see Consequences). |
| B. Supabase (Postgres, RLS) | The earlier plan. Strongest data integrity and an expressive RLS model. Replaced by SR v0.2: the project is already on Appwrite, and phone OTP, storage and realtime come bundled. Revisit only for the ledger (last section). |
| C. Self-hosted Appwrite | Full control and a possible route to in-country hosting if the lawyer requires it (DP-7). Costs operations effort and uptime responsibility (NFR-5, NFR-6). Not now; this is the fallback if hosting in the EU is ruled out. |
| D. Appwrite plus a dedicated Postgres for money only | A hybrid. This is the open question in the ledger section. |

### Access-control approach

| Option | Verdict |
|---|---|
| **1. Core service computes permissions; clients get read only** | **Chosen.** One place writes permissions, from business state. Easy to test and to audit. |
| 2. Let clients write rows and rely on Appwrite per-user write permission | Rejected: column-level control is impossible, so a parent could edit a price or a teacher a session status. |
| 3. Everything goes through the core service, none through Appwrite reads | Rejected as the general rule: loses Realtime and offline-friendly direct reads, and puts every list on the core service's capacity. Individual endpoints may still do this for sensitive data (see "When to skip direct reads"). |

## Decision

Adopt Appwrite Cloud in Frankfurt (A) with access control model 1.

### Principles

1. **Tables are split by audience.** Because permissions apply to whole rows, anything with a different audience goes in a different table. The home address is not a column on `child` or `family`; it is the table `home_address`. Safeguarding case content is separate from the status the reporter sees. Teacher documents are separate from the public teacher profile.
2. **Clients hold `read` only.** No table grants `create`, `update` or `delete` to `users`, `any`, a label or a team. The only exceptions are a deliberately narrow table of the user's own non-sensitive preferences (display name, language, notification settings), with `update("user:<id>")` on the row, and the curator catalogue tables (SR §6.2 gives curators write access). Everything else is written by the core service with its API key.
3. **Default deny.** SEC-2: no table or bucket with user data has `any` or `users` read. Row security is on for every such table and bucket. Table-level permissions are empty for clients; access comes only from row permissions.
4. **Roles are Appwrite user labels.** Labels are set by the core service (`users.updateLabels`), never by clients. Labels: `parent`, `guardian`, `teacher`, `opsadmin`, `curator`, `safeguardinglead`, `superadmin`. A user can have several. Permission strings use `label:<name>`. **Labels are alphanumeric.** SR §6.1 role names are used with the underscores removed (`ops_admin` becomes `opsadmin`, `safeguarding_lead` becomes `safeguardinglead`, `super_admin` becomes `superadmin`). The rule comes from the SDK doc comment for `users.updateLabels` ("each up to 36 alphanumeric characters long", node-appwrite `users.d.ts`), not from a test against the live service. Re-verify in A03 and M05 with the staging project, and rename here if the service behaves differently. Tickets and code must use these labels, never the underscore forms.
5. **Individuals are granted by user ID, groups by label.** Per-person access (a teacher's access to one child) uses `read("user:<id>")`. Blanket staff access uses `read("label:<role>")`. Teams are not used at the start, because a team per family or per case would add a second thing to keep in sync.
6. **The core service computes desired permissions from business state, then writes them.** It never adds or removes one entry in isolation. A function `desired_permissions(entity)` returns the full list from the current assignment, case and role data; the service replaces the row's permissions with it. This makes every grant and revoke idempotent and safe to repeat.

### What the core service sets, per SR §6.2 row

`P` = each parent or guardian user ID of the family (one entry per person); `T` = the teachers entitled to the child (see each row). Table names are proposals for F02.

| SR §6.2 data (table) | Permissions on each row | When the core service changes them |
|---|---|---|
| Own account (`user_profile`) | `read("user:<self>")`, `read("label:opsadmin")`, and `update("user:<self>")` on the non-sensitive preferences table only | Created at sign-up. Role changes alter labels, not this row |
| Children (`child`) | `read("user:<P>")`, `read("label:opsadmin")`, `read("user:<S>")` only while a case is open that concerns this child (safeguarding scope rule below), plus `read("user:<T>")` for teachers with a current or upcoming assignment. **Substitute:** `read("user:<substitute>")` from assignment until 24 h after the covered session ends | Teacher added when an assignment is accepted or a substitute is confirmed; removed when the assignment ends or, for a substitute, 24 h after the covered session (see Revocation) |
| Home address (`home_address`) | `read("user:<P>")`, `read("label:opsadmin")`, `read("user:<S>")` only while a case concerns the family. **Teacher:** `read("user:<T>")` only after the teacher accepts, and only until 24 h after the last session. **Substitute:** after the substitute accepts, until 24 h after the covered session | Added on acceptance; removed by the `address-revoke` job 24 h after the last session of the assignment (ADR 0002 lists this job), or at once on cancellation or a safeguarding hold |
| Session logs (`session_log`) | `read("user:<P>")` for the child's parents, `read("user:<author teacher>")` while assigned, `read("label:opsadmin")`, `read("user:<S>")` only for a child with an open case; a substitute reads from assignment until 24 h after the covered session | Written by the core service when a log is submitted. Teacher entries follow the assignment (see the history rule below) |
| Child history visible to a currently assigned teacher | The same `session_log` rows, with `read("user:<T>")` for each assigned teacher while the assignment lasts | Added and removed with the assignment, like `child` |
| Handover summaries (`handover_summary`) | `read("user:<P>")`, `read("user:<T>")` for the assigned teacher, `read("label:opsadmin")`; a substitute reads it from assignment until 24 h after the covered session | Teacher entry added on assignment, removed when it ends; substitute entry removed 24 h after the covered session |
| Teacher documents (`teacher_document`, bucket `teacher-docs`) | `read("user:<owner teacher>")`, `read("label:opsadmin")`, `read("user:<S>")` only while a case concerns that teacher. Files get the same | On upload. Verification writes go through the core service |
| Payments and earnings (`payment`, `invoice`, `earning`) | Parent rows: `read("user:<P>")`, `read("label:opsadmin")`. Teacher rows: `read("user:<teacher>")`, `read("label:opsadmin")`. Payout approval is a core-service endpoint, not a client write | On creation. Never changed afterwards for ledger-style rows |
| Catalogue and questions (`topic`, `question`) | Published rows: `read("users")` (an intentional exception: published catalogue content is not user data, so SEC-2 and M05's "no `any`/`users` permissions" criterion apply to user-data tables only, and M05 should be read that way), plus `read("label:curator")` and `read("label:opsadmin")`. Drafts: `read("label:curator")`, `read("label:opsadmin")` only. Curators get `create`/`update`/`delete` through `label:curator` on the catalogue tables only | The core service moves a row from draft to published, so `read("users")` is added only by an authorised publish |
| Safeguarding cases (`safeguarding_case`) | `read("label:safeguardinglead")`. This table is the case itself, so the label fits SR §6.2 "Read (cases)". Reporters do **not** read this row; writes by the core service only | On case creation |
| Case status for reporters (`case_status`) | A separate table: `read("user:<reporter>")`, `read("label:safeguardinglead")`. It holds only a reference, status and dates | Core service updates the status |
| Audit log (`audit_log`) | `read("label:opsadmin")`, `read("label:safeguardinglead")` (**deviation for the owner to accept**: the audit log is not a case row, and blanket read lets leads see who accessed case data; see memory.md owner questions). Append-only: the core service creates and never updates | On every audited action (SEC-6) |

Four rules need extra explanation:

- **Safeguarding scope (S).** SR §6.2 gives the safeguarding lead "Read (cases)", not blanket read of children, addresses and logs. So `child`, `home_address`, `session_log` and `teacher_document` carry no `label:safeguardinglead` entry. When a case is created or linked to a child, family or teacher, the core service adds `read("user:<S>")` for each safeguarding lead (S) assigned to the case on the related rows, and removes it when the case is closed and retention rules allow (the `permission-reconcile` job recomputes it from `safeguarding_case`). Cost: extra reconcile writes per case, and a case must be opened before a lead can see a record. A label-wide read would be simpler but is wider than the SR.
- **Substitute window.** A substitute teacher reads the child, `handover_summary` and `session_log` rows only from assignment until 24 h after the covered session ends, and the address from acceptance until 24 h after that session. `desired_permissions` takes the substitute window (assignment time, covered session end) as input. The `address-revoke` job (address) and `permission-reconcile` (other rows) remove the entries after the 24 h.

- **A teacher's past logs after an assignment ends.** A teacher keeps read access to earnings history, but must not keep the child's data. So for each submitted log a second, anonymised row is written in `teacher_session_history`, holding only the child's first name, class, date and duration, with `read("user:<teacher>")`. The full `session_log` row loses the teacher's read when the assignment ends.
- **Several guardians.** Each guardian's user ID is added separately. Adding or removing a guardian is a core-service action that recomputes permissions for the child and for every table holding that child's rows.
- **Staff by label.** `opsadmin` reading all payments is a blanket permission by label. The `superadmin` label has no read permission of its own on the data tables above; where a super admin needs data, the person also holds the relevant functional role (SR §6.1 allows several roles). This keeps super-admin power to settings and role management.

### Revocation

Appwrite cannot expire a permission on its own, so revocation is an explicit write.

**How it happens**

- **Event-driven.** When an assignment ends, a substitution replaces a teacher, a guardian is removed, a case closes, or a role is withdrawn, the core service recomputes the affected rows' permissions and writes them. Role withdrawal also updates labels, and the core service ends that user's sessions where needed.
- **Time-driven.** The 24 h address rule is a scheduled job, `address-revoke`, run by the ADR 0002 job runner every few minutes. It selects assignments whose last session ended more than 24 h ago and recomputes the address row's permissions. Because the computation is "desired from state", running it twice or late is harmless.
- **Order of operations.** For any change: (1) enqueue a `permission-reconcile` job for the entity (idempotent by `dedupe_key`), (2) write the business change, (3) recompute and write permissions straight away. If step 3 fails, the queued job from step 1 retries it within about a minute. If step 2 fails, the job finds nothing to change.
- **Client copies.** A revoked teacher's app may hold cached data locally. The app drops data for ended assignments when it syncs and on the Realtime event for the row. This is best-effort. It is a residual risk, and counsel should see it in the privacy notice work (DP-4).

**Failure mode: the grant and the business write diverge**

There is no transaction across the two writes. The possible states:

| Case | Business state | Permission state | Effect | Handling |
|---|---|---|---|---|
| Grant fails after the business write | assignment exists | teacher not yet granted | Under-grant: the teacher cannot see the child. Safe | Reconcile job retries; alert if it still fails after N attempts |
| Revoke fails after the business end | assignment ended | teacher still has read | **Over-grant: unsafe** | Reconcile job retries within about a minute; alert after 2 failed attempts; the drift check catches anything left |
| Grant written before the business write, and the write fails | no assignment | teacher can read | Over-grant | Prevented by the ordering rule: permissions are only written after the business write, from state |
| Substitute window ends | session ended 24 h ago | substitute still has read | **Over-grant** | `address-revoke` and `permission-reconcile` remove it from state; the drift check catches leftovers |
| Case closed, lead's grant not removed | case closed | lead still has read on the child, address or logs | **Over-grant** | `permission-reconcile` recomputes from `safeguarding_case`; the drift check catches leftovers |
| Two writers change one row's permissions at once | n/a | last write wins | A lost update could keep a revoked entry | All permission writes go through one function that recomputes from state, so the last write is also a correct one. Concurrent runs for one entity are serialised with a deterministic job ID (ADR 0002 rule 1) |

New jobs: `permission-reconcile` and `permission-audit` are not yet in the ADR 0002 job table; add them in F04.

Fail-safe default: grants are lazy and revokes are eager. When unsure, compute fewer permissions.

### Drift check

A daily job, `permission-audit`, uses the ADR 0002 runner (chunked, resumable) to compare stored permissions with what `desired_permissions` computes:

1. For each protected table and bucket, page through rows by cursor (not offset, because of the 5,000 cap) and compare each row's `$permissions` with the desired set.
2. **Over-grant** (stored has an entry that desired lacks): high severity. The job removes it and raises an alert (NFR-17).
3. **Under-grant** (desired has an entry that stored lacks): low severity. The job adds it and logs it.
4. **Forbidden entries** anywhere in user-data tables: any `any` or `users` read; any `create`, `update` or `delete` for non-core principals, other than the preferences table and curator catalogue tables.
5. It records counts and a drift rate. Any over-grant also writes an audit-log row.

Complementing the audit, **role-played tests** run in CI against a throwaway Appwrite project (NFR-15): for each role in SR §6.1, create users, run the real flows (accept an assignment, end it, add a guardian, open a case), then assert exactly which rows each user can read and that every write attempt from a client fails. These tests are needed in F02 and E02 and require a non-live project, so they are blocked on the owner (see open items).

### When to skip direct reads

For the most sensitive fields (the home address), the core service may serve the data through an endpoint that checks the assignment and time window on each call, instead of an Appwrite row read. That would make revocation immediate. It is **not adopted now**, and is the first hardening step if the job cadence for the 24 h rule is judged too loose.

### Roles and the admin dashboard

The Next.js admin (ADR 0004) signs admins in with Appwrite Auth and MFA (SEC-4) and reads with each admin's own session, so row permissions apply. Server-side admin actions that need broader access go to the core service, which authorises by label and then acts with its API key. No admin page uses the API key in the browser.

## Appwrite Cloud plan and cost

**These are estimates.** The official page, `appwrite.io/pricing`, was read on 25 November 2026 and confirmed only these things: Pro keeps running and bills overage, while a Free project freezes at its limit; credits apply to Pro; database compute tiers start at $10 a month. The plan prices and allowances below come from third-party summaries found by web search the same day ([Appwrite Pro announcement](https://appwrite.io/blog/post/announcing-appwrite-pro), plus several review sites), which **disagree**: Pro is described as $25 a month per project in the newer sources and as per member in older ones. **Confirm on the console's billing page before committing.**

| Item | Figure used (unverified unless marked) |
|---|---|
| Pro | $25 a month per project; includes about 2 TB bandwidth, 150 GB storage, 3.5 million function executions, 200,000 monthly active users |
| Overage | Bandwidth about $15 per 100 GB; executions about $2 per million; storage about $2.80 per 100 GB; database reads about $0.06 per 100,000 and writes about $0.10 per 100,000 |
| Free | Project freezes at its limits (official page). Not suitable for production |

Year-one usage against that: the job tick is about 43,200 executions a month (ADR 0002), about 1% of the stated allowance. 5,000 children, 1,000 teachers and 20,000 sessions a month is far below 200,000 users. Database reads and writes are the least certain line. If a session causes about 20 row writes, that is about 400,000 writes a month, about $0.40 at the stated rate. Reads depend on app behaviour and Realtime use; measure in staging.

### Year-one monthly cost for the stack (USD, estimate)

| Scenario | Appwrite | Fly.io (ADR 0003) | Total |
|---|---|---|---|
| Minimum: production on Pro; staging on Free; dev local | $25 | about $20 | **about $45** |
| Recommended: production and staging each on Pro | $50 | about $20 | **about $70** |
| Recommended plus Fly Standard support | $50 | about $49 | **about $99** |
| Adding a dedicated Postgres for the ledger (option D) | as above | plus about $4–10 for the entry tiers of Fly managed Postgres (the $4 entry price is from Fly's pricing page; sizing unverified) | plus $4–10 |

Not included: SMS (Africa's Talking), Resend, Google Maps, Sentry, Firebase, MoMo and card gateway fees, domain, and app-store accounts. Backups: the stated Pro allowance is daily backups for 7 days. NFR-6 asks for point-in-time recovery and a quarterly restore test. **Whether Appwrite Cloud offers point-in-time recovery on our plan is unverified and is an open item.**

### Latency to Appwrite Frankfurt

Measured numbers for the Kampala to `fra.cloud.appwrite.io` path (median about 83 ms to connect, about 420 ms for a fresh HTTPS request, 20 samples, a Kampala fixed-line network, mobile not measured) are recorded in ADR 0003 together with the method.

## Consequences

- **Positive:**
  - One place (the core service) decides access, from business state, with tests and a daily check.
  - Clients cannot write money, status or tier, whatever bugs the apps have.
  - Appwrite handles Auth, OTP, storage, Realtime and functions with no database administration.
- **Negative:**
  - Access is only as good as the permission-writing code. A bug can over-grant, and Appwrite will not notice. The audit job and role-played tests are required work, not extras.
  - Row-level permission means many tables split by audience, and any query that joins them is done in the service.
  - Revocation is eventual: the address stays readable for up to the job interval after the 24 h point. Fixing this needs the endpoint-mediated read above.
  - No database constraints: balance rules, uniqueness (beyond row IDs) and conditional updates are enforced in code and by deterministic IDs (ADR 0002).
  - Data is processed in the EU (DP-7); a legal ruling for in-country hosting would force option C.
- **Follow-ups:**
  - F02 (#28): build the schema from this table split, record the diff against the live 35 tables, and add `desired_permissions` tests per row type.
  - Before relying on the plan, confirm price, allowances and point-in-time recovery (open items).

## The ledger: open question (SR §14, item 2)

**Question.** Does the money ledger (SR §7.10: double-entry, append-only, balances derived) stay in Appwrite, or move to a dedicated Postgres before automated payments?

| | Stay in Appwrite | Move to Postgres |
|---|---|---|
| Atomic multi-row posting (the debit and the credit together) | Not guaranteed. A crash between the two writes leaves an unbalanced entry. Mitigation: a deterministic transaction ID with entries written in a fixed order, and a reconciliation job that finds orphan halves | Native: one database transaction |
| "Entries always balance" | Enforced only in code and by reconciliation | Enforceable by a deferred constraint trigger |
| Exactly-once posting (NFR-8) | Deterministic row ID per entry gives duplicate protection | Unique constraint on the idempotency key |
| Append-only | The core service never updates; permissions deny client writes. Anyone holding the API key could still edit | A database role without `UPDATE` or `DELETE` on the ledger tables, which also protects against the core service's own bugs |
| Balances from the ledger | Sum by paging rows, with the 5,000 cap. Use balances materialised by jobs and checked by reconciliation | A `SUM` query |
| Cost and effort | None extra | A second datastore, backups and credentials. About $4–10 a month on Fly managed Postgres; more effort than money |
| Access model | The same one as everything else | A separate model: the core service is the only client, no end-user access |

**Recommended default (for owner to confirm).** Do **not** decide now. Keep the ledger behind a `LedgerStore` interface in the core service, implement it on Appwrite for the pilot (manual or low-volume payouts, where an admin reconciles by hand), and **decide before automated payouts are switched on**, with a short spike that tests a crash between the two postings. Today's lean is towards Postgres at that point, because the double-entry guarantees are the weakest part of Appwrite for money. This ADR does not close SR §14 item 2.

## Staging environment: runbook (not yet executed)

**Nothing below has been run.** The live Appwrite project is read-only to agents; no staging project exists. Do not point any step at the existing project. The Fly.io half is in ADR 0003.

1. **Console: create the project.** Sign in to the Appwrite Cloud console. Create (or choose) the organisation. Create a project named `hs-staging`. **Choose the Frankfurt region** when asked. Copy the project ID and the endpoint (`https://fra.cloud.appwrite.io/v1`).
2. **Plan.** Decide Free or Pro for staging (see the cost table). Production uses Pro.
3. **Auth.** Enable Email/Password and Phone (OTP). Enable MFA. Set the session length. Create a test user per role, and set labels with a core-service script (not by hand) once F03 exists.
4. **API key.** Create a server API key for the core service with only the scopes it needs: database and tables read/write, users read/write (for labels), storage files read/write, functions execute, and any others F02 and F03 require. Set an expiry. Put it in the Fly secret `APPWRITE_API_KEY` for **this** project only.
5. **Database.** Create database `main`. Apply the schema by script (F02): tables split by audience, row security on, no client create/update/delete, table-level permissions empty. Never create tables by hand in the console.
6. **Storage.** Create private buckets (`teacher-docs`, `school-reports`, `question-media`) with file security on and no public read.
7. **Functions.** Deploy the `job-tick` function (`spikes/jobs-demo`) with `CORE_URL=https://homeschool-core-staging.fly.dev` and `JOB_TICK_SECRET` set to the same value as the Fly secret. Cron: every minute.
8. **Platforms.** Add the web and mobile app IDs as platforms so the SDKs are accepted.
9. **Check.** Run the role-played access tests (above) against this project. Run `permission-audit` once and expect zero over-grants.
10. **Record.** Store the project ID and key in the team password manager, not in the repository.

## Cross-border transfer note (DP-7): brief for a lawyer or data protection officer

**Status: open item. Not legal advice.** This brief is written by the engineering side to be reviewed by a qualified Ugandan lawyer or DPO. Nothing here has been confirmed with counsel, and no counsel has been engaged yet.

**Facts**

- The platform processes personal data of children (names, class, school level, home address with map pin, session logs, assessments), of parents and guardians (name, phone, email, payment records), and of teachers (identity documents, qualifications, payout numbers). Safeguarding cases are the most sensitive.
- Data subjects are in Uganda. Data is stored at Appwrite Cloud, Frankfurt (Germany, EU), and processed by the core service at Fly.io, Frankfurt.
- Appwrite and Fly.io are providers whose parent companies may be established outside the EU (we believe the United States for Fly.io; **we have not verified** the corporate and sub-processor arrangements of either). Provider staff may be able to access data from outside the EU.
- Other processors named in SR §9 also receive data: Resend (email), Africa's Talking (SMS), Google (Maps), Firebase (push), Sentry (errors), and the payment providers. Their regions are not yet recorded.
- Frankfurt was chosen on technical grounds: the project already exists there, and the region is close to Uganda compared with the Americas. It was not chosen on a legal analysis.

**Our reading (to be confirmed by counsel)**

- The Data Protection and Privacy Act, 2019 (Uganda) restricts processing and storage of personal data outside Uganda unless the destination has adequate protection at least equivalent to the Act, or the data subject consents (we recall this as section 19; **the citation needs checking**).
- Registration with the Personal Data Protection Office (PDPO) is required before launch (DP-1). Whether transfers must be declared there is not known to us.

**Questions for the lawyer or DPO (open)**

1. Does hosting in Frankfurt satisfy the Act's cross-border conditions? Is the EU (GDPR) treated as adequate, or must we rely on consent, contract, or a PDPO approval? Is there PDPO guidance or a list of recognised countries?
2. If consent is the basis: what form works for children's data? Is parental consent per child (DP-2) enough, can it be bundled with the terms, or must it be separate and specific to the transfer?
3. Do any data categories (children's data, safeguarding cases, address, identity documents, payout numbers) have to stay in Uganda, or need extra safeguards?
4. Does onward access by a provider's non-EU staff, or by a provider's US parent company, count as a further transfer? What must the Data Processing Agreements (DP-9) with Appwrite, Fly.io and the other processors say? Are the standard DPAs these providers offer acceptable?
5. What must the privacy notice say about hosting and transfers (DP-4, DP-7), and in which languages?
6. Is a data protection impact assessment required for children's data at this scale?
7. What are the breach-notification duties and timelines when the data sits abroad (DP-8)?
8. Does anything in sector rules (education, payments, safeguarding) require local copies or local hosting?
9. For the pilot (closed, reduced scope), is a lighter route acceptable (for example explicit consent with a short notice), and what must change before public launch?

**Impact if the answer is "must be hosted in Uganda or Africa"**

- Appwrite Cloud has no in-country region. The fallback is option C, self-hosted Appwrite, on a local or African host. The team then owns uptime, backups (NFR-6) and upgrades, and the cost and staffing assumptions in this ADR no longer hold.
- The core service container could move to an African region (Fly.io has had a Johannesburg region; **availability and latency not verified**). ADR 0003 would be revised.
- Latency from Kampala would probably improve, but it is unmeasured.
- The decision should be taken before F02 and F03 build on the current hosting. Tickets #28, #29 and #65 are blocked by this one.

**What engineering will provide to counsel:** the data inventory by table (F02), the processor list, the access model above, and the retention rules (DP-6).

## Open items and blockers

All need the owner.

| Item | Recommended default |
|---|---|
| Appwrite plan: confirm Pro price (per project or per member), allowances, and whether staging needs its own Pro project | Pro for production and for staging; check the console billing page |
| Appwrite point-in-time recovery and backups against NFR-6 | Ask Appwrite support in writing; if PITR is unavailable, add a nightly export job to our own storage |
| A non-live Appwrite project (`hs-staging`) so role-played tests and staging can run | Create it in Frankfurt and give the team a scoped API key |
| Lawyer or DPO review of the DP-7 note above | Engage before public launch, and before the pilot if real children's data is used |
| Ledger store decision | Decide before automated payouts; spike first |
| Sign-off on this ADR | Accept |
| Safeguarding lead's blanket read of `audit_log` (deviation from SR §6.2 "Read (cases)") | Accept for the pilot, or scope to case-related entries |

## Sign-off

- [ ] Owner approves this ADR (change Status to *Accepted*)
