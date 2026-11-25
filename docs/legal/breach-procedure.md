# Personal data breach procedure (C04, DP-8)

**Not legal advice. Everything about Ugandan law below, including whether and when the regulator (PDPO) and affected people must be told, is unverified, for lawyer confirmation (lawyer brief Q18, Q26; ADR 0012 DP-7 question 7).** Until the lawyer answers, this procedure uses the strictest plausible internal clock (72 hours from awareness, section "Notification clock") so we are never late under a shorter rule. All message templates are **non-binding drafts** for the lawyer to approve. Roles, phone numbers and contacts are placeholders to be filled by the owner; no contact details or secrets are committed here.

Related: processor list and breach contacts `processor-register.md`; secrets leak response `docs/runbooks/secrets.md` (F09); alert rules `ops/alerts.json` and monitoring runbook `docs/ops/runbook.md` (F08); registration `pdpo-registration-plan.md`; pointer from `docs/runbooks/data-breach.md`.

## What counts as a breach

Any accidental or unlawful loss, destruction, alteration, unauthorised disclosure of, or access to, personal data we hold or that a processor holds for us. Includes: a stolen device with a signed-in admin session, a leaked key, a teacher shown a family's address they are not assigned to, a storage bucket readable by the wrong people, a vendor telling us they were breached. If unsure, treat it as a suspected breach and start the clock; stand down later.

## Detection

| Source | How it reaches us |
|---|---|
| Monitoring | Alerts in `ops/alerts.json` (5xx rate, job failure, unacknowledged safeguarding case, uptime) via `docs/ops/runbook.md`; unusual auth or permission errors in Appwrite logs |
| Secret scanning / CI | GitHub secret scanning and `make secrets`; see `docs/runbooks/secrets.md` "Leak response" |
| Users | A parent or teacher reports seeing someone else's data (support inbox, safeguarding channel) |
| Staff | An admin loses a device, notices wrong access, or mis-sends a message |
| Vendors | A processor's breach notice (contact listed per processor in `processor-register.md`) |
| Audit log | Review of `audit_log` (SEC-6) for access outside the normal pattern, at least weekly while pilot is live |

Anyone who suspects a breach reports it to the incident lead immediately (no blame). Report channel placeholder: TBD by owner (phone number and email in the owner's private contact sheet).

## Roles

| Role | Default person | Duty | Backup |
|---|---|---|---|
| Incident lead | Owner | Decides severity, runs the clock, signs notifications | Safeguarding lead |
| Safeguarding lead | TBD (owner to appoint) | Child-safety impact, contact with affected families, case records | Incident lead |
| Technical responder | Owner/engineer on call | Contain, preserve evidence, fix | Second engineer or contractor (TBD) |
| Lawyer / DPO | Ugandan lawyer (not yet engaged, owner-blocked) | Confirms duties and approves wording | TBD |
| Communications | Incident lead | Parent/teacher messages, press line | Safeguarding lead |

Owner-blocked: naming the backups, and a reachable 24-hour contact for the incident lead.

## Severity matrix (triage within 1 hour of awareness)

| Severity | Criteria (any) | Examples | Response |
|---|---|---|---|
| S1 Critical | Children's data, home addresses, safeguarding cases, ID documents or payment numbers exposed to an unauthorised person or the public; or admin/service key abused | Public bucket with teacher IDs; production API key leaked and used; wrong teacher given a child's address | Page lead now; contain within 1 hour; notify the lawyer; PDPO and families per clock |
| S2 High | Personal data exposed to a small known group, no sign of misuse; or credentials leaked but unused | Key committed then revoked within the hour; lost admin phone, MFA on, session revoked | Contain within 4 hours; lawyer consulted; notification decision documented |
| S3 Moderate | Limited non-sensitive data (names, emails) to a known recipient, quickly recalled | Email to wrong parent with only a first name | Fix, log, decide notification |
| S4 Low / near miss | No personal data exposed | Alert fired, bucket rule found wrong before data was uploaded | Log and learn |

When in doubt, rate one level higher. Every event, including S4, is entered in the breach log (section "Breach log").

## Notification clock

Statutory duty and deadline: **unverified, for lawyer confirmation.** Secondary summaries mention a short notice to the PDPO (the regulator) and to affected people, possibly "immediately" or within 72 hours; we have not read the Act, Regulations or any PDPO guidance (checked sources are listed in ADR 0006; verify at https://www.pdpo.go.ug). Questions for the lawyer: the exact period and its start (awareness vs confirmation), content of the notice, whether a notice is needed when risk is low, notifying individuals, duties for a processor's breach, whether the clock stops for weekends, and which form.

Working clock (internal default, to be replaced by the lawyer's answer):

| Time from awareness (T) | Milestone |
|---|---|
| T+0 | Record exact time and who noticed; tell incident lead |
| T+1 h | Severity assigned; containment started |
| T+4 h | Contained, or escalation to the vendor; lawyer contacted for S1/S2 |
| T+24 h | Facts known as far as possible: what, whose, how many, how, risk to individuals; notification decision made and logged |
| T+48 h | PDPO notice and parent/teacher messages drafted and approved |
| T+72 h | PDPO notified (if required or if in doubt, decide with lawyer); affected people told without undue delay |
| After | Updates to PDPO as facts change; review within 10 working days |

If the facts are incomplete at T+72 h, notify with what is known and say so; follow up (a late notice is worse than an incomplete one under most regimes; unverified).

## Containment (technical responder, first)

1. Stop the harm first, evidence second, but never delete before copying logs.
2. Leaked key or token: follow `docs/runbooks/secrets.md` leak response (rotate within the hour budget, revoke the old key). Appwrite keys, Fly token, provider keys, webhook secrets: each has a procedure there.
3. Lost or compromised admin device: sign out all sessions for that user in Appwrite, reset password, re-enrol MFA, remove device tokens; check `audit_log` for the session's actions.
4. Wrong permissions: set the bucket or table permission back to default deny (ADR 0012, SEC-2, SEC-5), confirm with an unprivileged test account, then list who accessed what in the window.
5. Wrong person saw data: remove the assignment or grant, revoke cached access (app drops data on realtime event, best-effort per ADR 0012), ask the recipient in writing to delete and confirm.
6. Vendor breach: confirm scope with the vendor, rotate every credential we hold for it (names in `docs/ops/secrets-inventory.json`), stop sending if needed (exit steps in `processor-register.md`).
7. Do not tell the public or post on social media before the lawyer and incident lead approve.

## Evidence preservation

- Make a private incident folder (owner-controlled, not in git) named `INC-YYYYMMDD-n`.
- Save before changing: relevant Appwrite audit/function logs, Fly logs, Sentry events, GitHub audit log, screenshots of permission settings, the offending message or commit (hash only, never paste the secret).
- Record a timeline with timestamps in UTC and Africa/Kampala; who did what; commands run (no secret values).
- Note the chain of custody for any exported file; hash exports (sha256).
- Keep the folder for the retention period set by C03 (placeholder: at least 6 years pending lawyer; unverified).

## Assess

Answer in writing (the PDPO notice and the family message both draw on this): what data and which categories (child, address, ID document, phone, payment); how many people and which roles; how it happened; was it accessed or misused or only exposed; can it be recalled; risk to children and adults (physical safety if an address leaked; fraud if payout numbers leaked); what we did; what we will change.

## Notify the PDPO

Only after the lawyer confirms the duty and channel (owner-blocked until the lawyer is engaged and we hold the PDPO portal account, `pdpo-registration-plan.md` task 4). Prepare: our registration number (once issued), incident summary from "Assess", dates, number of people affected, categories, measures taken, contact person. Keep a copy of the submission and the acknowledgement in the incident folder.

## Notify affected parents and teachers

- Decide with the lawyer whether notice is required; default to telling people when there is a real risk to them or their children, and always for S1.
- Contact each affected adult directly (parent, guardian, teacher) by email plus app notice or phone for S1; never put child names in push text. For a child's data the parent receives the message.
- Say plainly what happened, what data, what we did, what they should do, and how to reach us. No blame, no jargon, English plus the local language where needed.
- The safeguarding lead calls families first when a child's address or schedule was exposed to someone who should not have it.

## Templates (non-binding drafts; lawyer to approve; replace the angle-bracket parts)

**A. PDPO notice (draft)**
Subject: Personal data breach notification, <company legal name>, registration no. <number>
We are notifying you of a personal data breach. Date and time we became aware: <date/time EAT>. Nature of the breach: <summary>. Categories and approximate number of data subjects: <...>. Categories of data: <...>. Likely consequences: <...>. Measures taken or proposed: <...>. Contact: <name, role, phone, email>. We will update you as we learn more.

**B. Parent notice (draft)**
Subject: Important: a security incident affecting your family's information
Dear <name>, on <date> we found that <plain description>. This involved <data>. We have <actions>. We do not currently know of misuse / we know that <...>. What you can do: <steps>. We are sorry. You can reach <name> on <phone/email>. We have told the data protection regulator <if applicable>.

**C. Teacher notice (draft)**
Same structure as B, adding what the teacher should do (change password, check payout number, report suspicious contact).

**D. Vendor notice (draft)**
We believe <provider> may have caused or been affected by an incident on <date>: <facts>. Under our agreement dated <date> please confirm scope, timeline, data affected, steps taken, and send your incident report within <period from DPA>.

**E. Internal incident log entry**
ID, time aware (UTC and EAT), reported by, severity, systems, data categories, count, status, containment time, PDPO decision and time, families notified and time, owner of actions, review date.

## Breach log

Keep a private register of every event (S1 to S4) with the template E fields and the decision whether to notify (unverified whether the law requires keeping one; keep it anyway). The register itself is not committed.

## Post-incident review

Within 10 working days: blameless review (timeline, what worked, what slowed us, root cause, contributing causes), actions with owners and dates, update of this procedure, the secrets runbook, alerts (`ops/alerts.json`) and the processor register if a vendor was involved, brief note to the lawyer. File the review in the incident folder; link the actions as GitHub issues (without personal data).

## Tabletop exercise (owner-blocked: needs real people; run before launch and yearly)

Participants: incident lead, safeguarding lead, technical responder, backup, lawyer if available. Duration 60 minutes. One facilitator reads the scenario in stages ("injects"); the team states aloud what they do, who they call and which document they open; a scribe records gaps. No production systems are touched.

Script:
1. (5 min) Brief: purpose, rules, this procedure open.
2. (10 min) Inject 1: scenario start. Questions: who is told, what time is T+0, what severity?
3. (15 min) Inject 2: more facts arrive (new detail below). Questions: what do we contain, what evidence do we save, who do we call?
4. (15 min) Inject 3: the clock reaches T+24 h and T+48 h. Questions: who decides to notify, what do we draft, what is missing (contact list, lawyer, PDPO account)?
5. (10 min) Fill the incident log and draft template A and B for real wording.
6. (5 min) Gaps list with owners and dates; update this document.

Scenarios:

| # | Scenario | Facts revealed in stages | What we want to see |
|---|---|---|---|
| 1 | Lost phone with admin session | Admin reports phone lost at a taxi stop; later: MFA was on but phone unlocked and app signed in; audit log shows a student record export 20 minutes after | Remote sign-out, password and MFA reset, audit log review, S1/S2 decision |
| 2 | Leaked API key | Secret scan alert on a public commit; key is the production `APPWRITE_API_KEY`; Appwrite logs show a read from an unknown IP | Follow `docs/runbooks/secrets.md` leak response, rotate within the hour, assess data read, notification decision |
| 3 | Wrong teacher saw an address | A parent reports a teacher not assigned to them mentioned their street; assignment table shows an old grant not revoked | Remove access, check how long, ask teacher to confirm deletion, call family, safeguarding review, severity S1 |
| 4 | Bucket permissions error | A deploy changes the teacher-documents bucket to "any" read; found after 3 days by an external reporter | Reset to default deny, access logs for the window, identify which documents were retrieved, notify teachers, lawyer on S1 |

Record each exercise (date, participants, scenario, gaps, actions) in the incident folder and list it as evidence in the PDPO plan. The first real exercise is owner-blocked.

## Owner-blocked items (exact steps and defaults)

1. Engage the lawyer on `lawyer-brief.md`; ask Q18, Q26 and the clock questions above. Default meanwhile: the 72 h internal clock.
2. Appoint safeguarding lead and backups; add their names to a private contact sheet (not in git). Default: owner as lead, safeguarding lead as backup.
3. Create the incident folder location (private cloud folder with two-person access).
4. Get breach-notice contacts from each processor DPA; enter them in `processors.json` `breach_contact`.
5. Run the tabletop with real people and file the notes.
6. Replace template drafts with lawyer-approved text.
