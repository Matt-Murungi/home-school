# Lawyer brief (D06)

**Not legal advice.** Written by the engineering side for a qualified Ugandan lawyer / data protection officer. Nothing is confirmed. Companion: ADR 0006 (options), ADR 0012 (DP-7 facts), `policy-inventory.md`, `pdpo-registration-checklist.md`.

## Product in brief
Home tutoring alongside formal school (not homeschooling), Uganda. Parents book plans; vetted teachers visit homes; platform matches, schedules, verifies attendance (GPS check-in), collects payment (MTN MoMo, Airtel, cards later), pays teachers weekly with tiers and bonuses. Data: children (name, class, school, concerns, home address and map pin, session logs, assessments), parents, teachers (ID, certificates, police clearance, payout numbers), safeguarding cases. Hosting: Appwrite Cloud and Fly.io, Frankfurt (see ADR 0012 facts). Planned: phased launch, closed pilot first.

## Questions

### A. Positioning and contracts
1. Marketplace/introducing agent vs tutoring provider vs employer (ADR 0006 options A1-A4): which is defensible given we set rates, tiers, cancellation pay and verify attendance? Which wording must the terms, teacher agreement and marketing avoid?
2. Is "home tutoring / learning support alongside school" safe given the 2024 Attorney General statement and NCDC position (Concept Note)? Any licensing or registration with the Ministry of Education, NCDC or a teachers' body needed for the company or for teachers? Must teachers be registered teachers?
3. Holiday packages: are they permitted given the government's stance on holiday coaching? If not, confirm removal.
4. Tax: income-tax withholding on teacher payouts, VAT/commission treatment, invoicing and receipts, URA registration, mobile-money payout rules.
5. Does collecting parents' money and paying teachers require a payment-services licence or arrangement with a licensed provider?
6. Liability for harm to a child or damage at a home; insurance expectations; limits and disclaimers that are enforceable with consumers.
7. Teacher agreement: independent status indicators, IP/confidentiality, non-solicitation (parents going direct), termination, data duties, governing law and dispute route.
8. Child-safety duties: statutory reporting duties for incidents, vetting requirements (Certificate of Good Conduct), who may hold a child-abuse report, duty to police/probation officers, records on safeguarding cases and their retention.
9. Parental consent: who may consent for a child (second guardian, non-parent guardian), age thresholds, whether the child's own feedback (W04) needs special handling.
10. Electronic signature and click-accept validity for the safeguarding policy (typed name + checkbox) and terms; evidence needed.

### B. Data protection (Data Protection and Privacy Act 2019)
11. Roles: platform as controller with vendors as processors; are teachers separate controllers for what they record?
12. Registration (DP-1): who must register (platform, any local entity, foreign processors), when, fee, validity and renewal, annual compliance report, documents required. Is registration needed before a closed pilot?
13. Is a DPO required or advisable? Is a DPIA required for children's data?
14. Lawful basis per purpose; consent form for children's data; separate consent for transfers (DP-2).
15. Privacy notice content and languages (DP-4); cookie/permissions notice.
16. Data subject rights and timelines: view, export, delete (DP-5); how to treat deletion vs safeguarding and tax retention.
17. Retention periods (DP-6): child records after cancellation, logs, teacher documents after deactivation, safeguarding cases, payment records (tax law period).
18. Breach procedure (DP-8): notification to PDPO and individuals, timelines, content, when data sits abroad.
19. Processor agreements (DP-9): minimum content; are providers' standard DPAs acceptable?

### C. DP-7 questions carried over from ADR 0012 (verbatim in substance)
20. Does hosting in Frankfurt satisfy the Act's cross-border conditions? Is the EU treated as adequate, or must we rely on consent, contract or PDPO approval? Is there PDPO guidance or a recognised-country list? (Secondary sources say no per-transfer approval but documented safeguards; confirm.)
21. If consent is the basis: what form works for children's data; per-child consent (DP-2) enough; can it be bundled with terms or must it be separate and specific to the transfer?
22. Must any data category (children's data, safeguarding cases, address, identity documents, payout numbers) stay in Uganda or have extra safeguards?
23. Does onward access by provider non-EU staff or US parent companies count as a further transfer? What must the DPAs with Appwrite, Fly.io and the other processors say?
24. What must the privacy notice say about hosting and transfers, and in which languages?
25. Is a DPIA required (see 13)?
26. Breach-notification duties when data sits abroad (see 18).
27. Does any sector rule (education, payments, safeguarding) require local copies or hosting?
28. For the closed pilot, is a lighter route acceptable (explicit consent with a short notice), and what must change before public launch?

### D. Retention mechanism (C03, `retention-schedule.md`)
29. Please confirm or replace each placeholder period in `docs/legal/retention-schedule.md` (generated, one row per table): child records after cancellation (3 years), session logs and school reports (3 years), home address (1 year), check-in coordinates (2 years), teacher documents and profile after deactivation (7 years), safeguarding cases (25 years after closing), notifications, and the proof records that have no end date (consent, policy acceptance, audit log).
30. Tax records: the statutory retention period for payments, invoices, earnings, payouts, subscriptions, sessions and the ledger (7 years is a placeholder), and whether a stable one-way pseudonym in place of the payer or teacher reference, with the provider callback text removed, satisfies "personal fields minimised". What happens to the ledger after the period (archive, aggregate, keep)?
31. Deletion requests (DP-5): is a 30 day grace period (a cancellation window) acceptable and lawful; what must be kept and for how long (consent records, ledger, audit log, safeguarding cases, the request itself); can a teacher with open payouts or disputes be deleted, and when?
32. Backups and quarantine: deleted data stays in provider backups until they roll off and for 14 days as soft-deleted rows. Is that acceptable and what must the privacy notice say? Who may place a legal hold (safeguarding lead, owner), and is an open safeguarding case an acceptable automatic hold on the child's and the teacher's data?

## What we will give counsel
Data inventory by table (F02/M01-M04), processor list and regions (to be completed), access model (ADR 0012), retention proposals (C03), policy outlines (`policy-inventory.md`), draft flows (consent, teacher signature).

## Requested output
A written legal note (stored in `docs/legal/`), the final texts of the eight policies, registration steps with timeline and fees, and a yes/no on holiday packages and on in-country hosting.
