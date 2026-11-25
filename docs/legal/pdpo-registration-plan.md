# PDPO registration plan (C04, DP-1)

**Not legal advice. Every requirement, form name, fee, validity period and timeline is unverified, for lawyer confirmation** until confirmed on the PDPO site (https://www.pdpo.go.ug) or by the lawyer. Source of the items: `pdpo-registration-checklist.md` (D06, secondary sources as of 25 Nov 2026). Positioning and roles: ADR 0006 (Proposed). Launch blocker tracked in R07 (#155).

Dates below are a **proposed schedule counted backwards from a target pilot start of T = to be fixed by the owner** (placeholder rule from D06: start at least 8 weeks before). Today is 25 Nov 2026; if the pilot is the date in SR/ADR 0013 (18 Jan 2027, per memory.md question 5), eight weeks before is 23 Nov 2026, i.e. already late: the owner should either move the pilot, or confirm with the lawyer whether a closed pilot with synthetic or consented data needs registration first. All task owners are people who must act; the builder agent cannot do any of them.

Evidence slots: each task has a reference code `EV-PDPO-nn`. Store the evidence in the owner's private evidence folder (not git; no passwords) and write the link or file name in the "Evidence link" column when done. The launch checklist item for C04 is only ticked when every slot required for launch is filled.

## Timeline (relative to pilot start T)

| When | Milestone | Tasks |
|---|---|---|
| T-10 weeks (week of 3 Nov 2026 equivalent if already late: this week) | Engage lawyer; collect entity documents | 1, 2, 3 |
| T-9 | Lawyer answers roles and registration questions; PDPO portal account | 2, 4, 7 |
| T-8 | Engineering inputs complete (data inventory, processors, security summary) | 5, 6, 8, 9, 10 |
| T-7 | Retention, breach procedure and notice final; DPAs signed | 11, 12, 13, 14 |
| T-6 | Fee paid, application submitted | 15, 16 |
| T-2 | Certificate expected (processing time unknown: replace this row with the real figure) | 17 |
| Launch | Certificate on file; renewal reminder set | 17, 18 |

## Plan

| # | Task | Owner | Due (relative) | Prepared by agent (in repo) | Evidence slot | Evidence link | Status |
|---|---|---|---|---|---|---|---|
| 1 | Legal entity documents: incorporation certificate, TIN, registered address, directors | Owner | T-10w | Nothing (owner documents) | EV-PDPO-01 | | owner-blocked |
| 2 | Applicant category: controller / collector / processor; do foreign processors register separately; closed pilot needs registration? | Lawyer (brief Q11, Q12) | T-9w | `lawyer-brief.md`, ADR 0006 | EV-PDPO-02 (written note) | | owner-blocked |
| 3 | Contact person / DPO name, role, email, phone | Owner | T-10w | Role description in `breach-procedure.md` | EV-PDPO-03 | | owner-blocked |
| 4 | PDPO portal account (no password in repo) | Owner | T-9w | Steps below | EV-PDPO-04 (screenshot with account id) | | owner-blocked |
| 5 | Purposes of processing | Founders | T-8w | Draw from `policy-inventory.md` privacy notice outline | EV-PDPO-05 (approved purpose list) | | todo |
| 6 | Categories of data subjects and data | Engineering | T-8w | Data inventory by table from `appwrite/schema` (F02/M01-M04) and `processors.json` data categories | EV-PDPO-06 | | todo |
| 7 | Sensitive data and children's data, lawful basis | Lawyer | T-9w | Lawyer brief questions | EV-PDPO-07 | | owner-blocked |
| 8 | Recipients / processors with region and DPA status | Engineering | T-8w | `processor-register.md`, `processors.json` (validated) | EV-PDPO-08 (the register export) | | in repo |
| 9 | Cross-border transfers: countries, safeguards, contracts, any transfer undertaking | Lawyer | T-8w | Register `cross_border` fields; ADR 0012 DP-7 note | EV-PDPO-09 | | owner-blocked |
| 10 | Security measures summary | Engineering | T-8w | ADR 0012, SR section 10 (SEC-1..11), `docs/runbooks/secrets.md`, `ops/alerts.json`; write a one-page summary at submission time | EV-PDPO-10 | | todo |
| 11 | Retention schedule | Lawyer | T-7w | Proposals from C03 | EV-PDPO-11 | | owner-blocked (C03) |
| 12 | Breach procedure | Founders | T-7w | `breach-procedure.md` (draft, lawyer review pending); tabletop run once | EV-PDPO-12 (approved copy + tabletop notes) | | in repo / owner-blocked |
| 13 | Privacy notice final | Lawyer | T-7w | Outline in `policy-inventory.md` | EV-PDPO-13 | | owner-blocked |
| 14 | Processor agreements signed (or risk accepted) | Owner | T-7w | Steps in `processor-register.md`; validator shows `signed` only with link and date | EV-PDPO-14 (one slot per processor, 12 rows) | | owner-blocked |
| 15 | Fee payment (amount unverified) | Owner | T-6w | None | EV-PDPO-15 (receipt) | | owner-blocked |
| 16 | Submission | Owner | T-6w | Packet assembled from rows 5-14 | EV-PDPO-16 (reference, copy) | | owner-blocked |
| 17 | Certificate: number, expiry, renewal reminder 3 months before expiry | Owner | T-2w to launch | None | EV-PDPO-17 | | owner-blocked |
| 18 | Annual compliance report (deadline per PDPO; sources conflict) | Lawyer | Annual | Reminder entry | EV-PDPO-18 | | owner-blocked |

## Owner steps (exact, with defaults)

1. Pick the pilot start date T and tell the engineer; default: do not take real children's data until a certificate exists or the lawyer says in writing that a closed pilot is exempt.
2. Engage a Ugandan lawyer or DPO on `lawyer-brief.md` (D06). Ask first: who must register, form, fee, validity, processing time, closed pilot, foreign processors, breach clock.
3. Open the PDPO portal account at https://www.pdpo.go.ug (unverified path; confirm with the lawyer); keep credentials in the owner's password manager.
4. Gather EV-PDPO-01 and 03; give the lawyer the register and the data inventory.
5. Sign DPAs per `processor-register.md`; update `processors.json`; run `python3 docs/legal/check-processors.py`.
6. Pay the fee, submit, record the reference; calendar the certificate renewal reminder and annual report.
7. Edit this table: fill evidence links and mark statuses; tick the launch checklist item (#155) only with all required evidence links.
8. If a provider refuses a DPA: replace it or record a dated risk acceptance (see register).

## Evidence needed for the ticket's acceptance criteria

| Criterion | Evidence | Status |
|---|---|---|
| Registration certificate filed | EV-PDPO-17 | owner-blocked |
| DPAs filed | EV-PDPO-14 x12 | owner-blocked |
| Breach runbook with timelines and contacts | `breach-procedure.md` (timeline done, contacts owner-blocked) and `docs/runbooks/data-breach.md` | built, lawyer review owner-blocked |
