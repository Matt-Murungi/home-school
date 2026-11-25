# Retention periods (C03, DP-6)

**Status: DRAFT, every period is a placeholder pending lawyer confirmation (lawyer brief Q17, Q29-Q32).** Not legal advice. The engineering side proposes conservative defaults (longer rather than shorter: a period that is too long is corrected by a setting, a record deleted too early cannot be restored). Nothing is deleted until the owner sets `RETENTION_ENFORCE=true` after the lawyer has confirmed the periods and a restore has been proven (see "Before enforcement is switched on").

The full table, one row per database table (data class, legal basis, action at expiry, period, legal floor, what a deletion request does), is generated from the code and reviewed here: **[retention-schedule.md](retention-schedule.md)**. Do not edit it by hand; the policy is `services/core/src/hs_core/retention/policy.py` and a test fails when the file is stale.

## The periods in one table (placeholders)

| Data | Period (placeholder) | Counts from | At expiry | Basis |
|---|---|---|---|---|
| Child record (name, date of birth, school, needs) | 3 years | end of the last subscription (cancellation), and only if no subscription is live | delete | operational, lawyer to confirm |
| Session logs, school reports, assessments, handover summaries, feedback | 3 years | submission / upload / start | delete (files first) | operational |
| Home address of a family | 1 year | account or address removal | delete | operational |
| Check-in coordinates of a session | 2 years | check-in | delete | operational |
| Teacher documents (identity, qualifications) | 7 years (floor 1 year) | teacher deactivation | delete (files first) | legal (employment/tax), lawyer to confirm |
| Teacher profile | 7 years (floor 1 year) | deactivation | delete; earnings keep a pseudonym | legal |
| Payments, invoices, earnings, payouts, subscriptions, assignments, sessions | 7 years (floor 7 years) | creation / end | **kept**, personal fields minimised (payer or teacher reference becomes a pseudonym; provider callback text and free text removed) | legal: tax records, lawyer to confirm the number |
| Ledger entries, transactions, accounts | no purge by code | - | **never purged**; archival after the tax period is a lawyer and owner decision | legal |
| Safeguarding cases and their actions | 25 years (floor 7 years) | case closed | delete only closed cases, only through the append-only exception path (off by default) | legal, brief Q8 |
| Consent records, policy acceptances | no purge | - | kept as proof | proof |
| Audit log | no purge | - | kept (append-only) | proof |
| Notifications | variables stripped after 90 days, row deleted after 1 year | creation | strip then delete | operational |
| One-time codes, travel cache, run summaries, closed access windows | days to 1 year | expiry / close | delete | operational |
| Data-subject deletion requests | 7 years | request date | delete (proof that a request was answered) | proof |

## How the periods are counted

Calendar days in Africa/Kampala. A row is due when today (Kampala date) is **after** the date the period ends: it is kept through the whole last day. A period that lands on a day that does not exist (29 February plus one year, 31 January plus one month) rolls forward to the 1st of the next month, so a record is kept one day longer, never shorter. A row created at 00:30 on 1 January in Kampala belongs to 1 January even though its UTC date is 31 December.

## What the rules guarantee (each has a test)

- **Legal hold.** A legal hold (`retention_hold`: a child, parent, teacher, user, safeguarding case or one row) stops any purge or anonymisation of the subject's rows, including rows that reference a held row. An **open safeguarding case holds its child and its teacher automatically**. If a hold cannot be looked up, nothing is deleted.
- **Payment records are kept for tax and minimised.** They are never deleted by the job; the payer reference becomes a stable pseudonym and the provider's raw callback (which can contain a phone number) is removed. A deletion request does the same immediately (the payment stays, the link to the person goes).
- **Children before parents; files first.** References are restricted (the parent is not purged while children rows remain), cascaded or replaced by a pseudonym. Storage objects (teacher documents, school reports, question media) are deleted before their row; an orphan sweeper removes files that no row references.
- **Soft delete first.** A row that is due is first soft-deleted (the M02 rule, derived unique keys cleared) and hard-deleted `RETENTION_QUARANTINE_DAYS` (14) later: a window to notice a mistake.
- **Safe by default.** Dry run until `RETENTION_ENFORCE`; a table is refused when more than `RETENTION_MAX_DELETE_PCT` (10) percent of it is due in one run; a clock that looks wrong stops the run; one run at a time; every run leaves one audit summary with counts per table.
- **Data-subject deletion (DP-5).** `requested -> verified -> scheduled (grace period, 30 days placeholder) -> executed -> confirmed`. Deleted: the subject's rows. Anonymised: money and session records. Retained and counted: consent, ledger, audit log, safeguarding cases. A hold blocks the whole request. Parent: the family's children go with the parent. Teacher: documents (and their files) and profile go, earnings and payouts keep a pseudonym. Mechanism: `hs_core.retention.deletion`; the user-facing flow is C05 (#143).

## Changing a period

Set `RETENTION_PERIODS` (JSON, e.g. `{"child": "5y"}`) in the deployed environment. A period may be lengthened freely; one shorter than the rule's legal floor (tax records 7 years, safeguarding 7 years, teacher documents 1 year) is refused at startup. To change a default, edit `policy.py` and regenerate `retention-schedule.md` (`cd services/core && uv run python -m hs_core.retention.render`).

## Before enforcement is switched on (owner)

1. The lawyer confirms or replaces every placeholder (questions 17, 29-32 of `lawyer-brief.md`).
2. A backup and restore of the Appwrite database and Storage buckets is proven (owner-blocked, `docs/ops/retention.md`).
3. Run in dry run in staging and read the first summary (`audit_log` entity `retention_run`, counts per table).
4. Set `RETENTION_ENFORCE=true` in staging, then production. The first big purge may need `RETENTION_BLAST_OVERRIDE=true` once.

Owner and lawyer decisions that are open: see the section "Questions for the owner" in `memory.md` (C03 items) and `lawyer-brief.md` section D.
