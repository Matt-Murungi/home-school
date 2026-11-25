# ADR 0006 — Legal positioning, policies and data-protection registration

- **Status:** Proposed
- **Date:** 25 November 2026
- **Ticket:** D06 (#26)
- **Related:** Concept Note §3; PRD policies row and risk "Legal positioning"; System Requirements v0.2 §11 (DP-1..DP-9); ADR 0012 (DP-7 brief); `docs/legal/`

**Not legal advice.** The author is not a lawyer. Every statement about Ugandan law below is labelled either *unverified, for lawyer confirmation* or *secondary source seen 25 Nov 2026* (law-firm or press summaries found by web search; the statute text itself was not read). Nothing here is a legal conclusion. The owner and a Ugandan lawyer decide.

## Context

The product is home tutoring that supports, and does not replace, formal school (Concept Note §3). Teachers are vetted and matched to families; the platform takes parents' payments and pays teachers weekly; children's data and home addresses are held. Four legal questions block launch copy and the compliance epics (#139 C01, #141 C03, #142 C04):

1. **Positioning:** is the platform a marketplace or agent introducing independent teachers, or does it act as the tutoring provider or employer of teachers?
2. **Data-protection roles and registration** with the Personal Data Protection Office (PDPO) (DP-1).
3. **Which policies are needed at launch**, who owns them, and which tickets consume them.
4. **Holiday packages** (Concept Note: "check with a lawyer given the government's stance on holiday coaching").

What the Concept Note records (the owner's own notes, not verified here): in November 2024 the Attorney General told Parliament that the law requires enrolment in formal school and does not provide for homeschooling; NCDC recommended regulation; no policy is in force. Hence the wording "home tutoring" / "learning support", never "homeschooling".

What we found by web search (secondary sources, 25 Nov 2026, **for lawyer confirmation**):

- The Data Protection and Privacy Act, 2019 (Act 9 of 2019; ULII: https://www.ulii.org/akn/ug/act/2019/9). Summaries say s.29 requires everyone who collects or processes personal data to register with the PDPO, and s.19 restricts transfers outside Uganda unless adequate safeguards or equivalent contractual protection exist.
- A 2025 PDPO decision against Google reportedly held registration mandatory for controllers, collectors and processors including those without a Ugandan presence, and applied s.19 to a foreign entity; the PDPO reportedly said no per-transfer approval is needed but compliance must be documented (summaries: DLA Piper, 13 Aug 2025 https://privacymatters.dlapiper.com/2025/08/uganda-data-protection-regulator-clarifies-compliance-requirements-for-offshore-entities ; Cliffe Dekker Hofmeyr, Aug 2025). Whether the decision was appealed is unknown to us.
- Registration reportedly runs through the PDPO portal (Form 2), is valid one year and renewed annually; one third-party guide gives a fee of UGX 100,000 (unconfirmed); sources disagree on the annual compliance report deadline. All unverified. Verify on pdpo.go.ug.

Nothing was verified against the statute or PDPO site directly. Treat every item as a question.

## Options

### A. Positioning of the platform towards teachers

| Option | What it means | Trade-offs (all *unverified, for lawyer confirmation*) |
|---|---|---|
| **1. Marketplace / introducing agent; teachers are independent contractors** | Platform vets, matches, schedules, collects payment as a collecting agent, pays teachers net of a commission. Teacher agreement says independent. | Lowest payroll burden (no employer statutory contributions), fits the product (teachers choose offers). Risks: the platform sets rates, tiers, schedule rules, cancellation pay and logs sessions, which may look like control and weaken the "independent" label; tax withholding and VAT or income-tax duties on commission and on payouts must be checked; liability for harm to a child may still be argued against the platform as the party that vetted and matched (duty of care); handling parent money may need payment-service licensing (**ask**). |
| **2. Platform as tutoring provider (principal)** | Platform contracts with parents to deliver tutoring; teachers deliver on its behalf, as contractors or staff. | Clearer consumer contract and a single accountable party for quality and safety; more liability and possibly business-licensing/education-sector registration; teacher status risk is higher. |
| **3. Employer** | Teachers are employees (part-time or casual). | Strongest control for safeguarding and quality; highest cost (payroll taxes, social security, leave) and rigidity; does not fit the offer-and-accept model or the tier/bonus pay design. |
| **4. Hybrid** | Marketplace framing for terms, but safeguarding rules, vetting and code of conduct are non-negotiable conditions of access. | Likely the practical shape of option 1; keeps safety controls without claiming to be the employer. Wording must not contradict reality. |

### B. Data-protection roles

| Option | Trade-offs |
|---|---|
| **1. Platform is the data controller; Appwrite, Fly.io, Resend, Africa's Talking, Google, Firebase, Sentry, payment providers are processors** (default thinking) | Simple, matches how the product decides purposes. Needs data processing agreements (DP-9) with each. |
| 2. Platform and teacher are joint or separate controllers for data teachers hold about children (session logs, handover) | Reflects that teachers use the data independently; complicates notices and teacher agreement duties. Ask the lawyer. |

### C. Registration and timing (DP-1)

| Option | Trade-offs |
|---|---|
| 1. Register before the closed pilot | Safest if real children's data is used; costs time before the pilot. |
| 2. Register before public launch only; pilot with explicit consent and short notice | Faster; depends on the lawyer accepting a lighter pilot route (ADR 0012 question 9). Risk: processing unregistered data is reportedly a breach. |

### D. Holiday packages

| Option | Trade-offs |
|---|---|
| 1. Keep in scope, behind a feature flag, off until the lawyer clears | Keeps design options open. |
| 2. Remove from MVP scope entirely | Matches the issue's edge case if advice is negative; no other ticket currently depends on it (none found in the backlog). |

## Decision

**PROPOSAL pending lawyer review and owner choice.**

None of this is final. It is the engineering side's default so work can proceed:

1. **Positioning: A4 (hybrid marketplace).** Terms and teacher agreement describe the platform as an introducer and payment agent for independent teachers, while vetting, safeguarding policy, code of conduct and session verification are conditions of use. Public language: "home tutoring", "learning support". Never "homeschooling" or "school".
2. **Data roles: B1.** Platform is controller; vendors are processors under DPAs. Lawyer to rule on B2.
3. **Registration: C1 if real children's data is used in the pilot, else C2.** Treat registration as a **launch blocker** (issue edge case). Prepare the file now (`docs/legal/pdpo-registration-checklist.md`).
4. **Holiday packages: D1.** Out of MVP; nothing built until the lawyer clears. If advice is negative, remove (D2).
5. **Policies:** the eight in `docs/legal/policy-inventory.md`, drafted by the lawyer from our outlines, versioned in admin (C01 #139, A05 #47).
6. **Cross-border (DP-7):** keep Frankfurt (ADR 0012) pending the lawyer; if in-country hosting is required, ADRs 0003/0012 are reopened.
7. **Questions:** `docs/legal/lawyer-brief.md` (new questions plus the DP-7 questions from ADR 0012).

## Consequences

- Product copy and legal texts cannot be final until a lawyer reviews. Tickets C01, C03, C04, A05, V05 can build the mechanism (versioned text store, acceptance records, retention jobs) against placeholder texts.
- Blocked on owner: engaging a lawyer, choosing the positioning, approving the pilot registration route, doing the registration itself and paying fees.
- Wording risk: if the lawyer picks option 2 or 3, the teacher agreement, tier/commission design (T-epic) and payout flows change; the cost of the choice is highest after the pay epic is built, so decide early.
- Registration may take longer than the build: tracked as a launch blocker (R07 #155).
- Proposed tests: `python3 docs/legal/check-policy-inventory.py` (inventory completeness and ticket references); `python3 docs/adr/check-adrs.py`.
- Sign-off: owner and lawyer. Status stays Proposed until both.
