# ADR 0005 — Transactional email provider: Resend, behind an `EmailSender` interface

- **Status:** Proposed — awaiting owner sign-off
- **Date:** 25 November 2026
- **Ticket:** D05 (#25)
- **Related:** System Requirements v0.2 §3 (stack), §8 (notifications), §14, DP-9; ADR 0002 (job runner); ADR 0003 (core service on Fly.io); tickets N02 (#127), #44

## Context

System Requirements v0.2 §3 already names Resend for receipts, alerts and safeguarding emails. This ADR records that choice against two alternatives, so the reasons are written down and the choice stays swappable.

What the platform sends by email:

| Category | Examples | Why it matters |
|---|---|---|
| Receipts and billing | payment receipt, failed payment, renewal reminder | Parents act on these |
| Operational alerts | session cancelled, substitute assigned (fallback when push fails, SR §8) | Time-critical |
| Safeguarding | notification to the lead and the backup at once, with escalation | Must not land in spam |
| Account | email verification, password reset (Appwrite sends its own auth mail; see Consequences) | Volume is small |

Year-one volume is small (a pilot of a few hundred families): well under 10,000 messages a month. All sending is from the core service (SR §8: templates stored centrally with variables, delivery status recorded per notification).

**Not decided here:** the sending domain (owner decision), and the proof of deliverability. Both need the owner's domain, a provider account and DNS access. They are listed in memory.md under "Blocked on owner".

## Decision drivers

| Driver | Weight |
|---|---|
| Inbox placement for Gmail, Yahoo, and Ugandan ISP/company mailboxes | High |
| Bounce and complaint events delivered by webhook, so the core service can suppress addresses (SR §8 delivery status) | High |
| Safe, simple to swap (provider behind an interface; no provider types leak into business code) | High |
| Cost at pilot scale and at 10x | Medium |
| Templates managed centrally with variables | Medium |
| Developer ergonomics: HTTP API, idempotency, Python SDK or plain HTTP | Medium |
| DP-9: a data processing agreement is available | Medium |

## Options considered

Prices below are from the providers' public pricing pages, fetched on **25 November 2026** by an agent. They are **unverified list prices** (no account, no quote) and change often; confirm before relying on them.

### A. Resend — chosen (System Requirements v0.2)

- **Price (unverified, 25 Nov 2026):** Free $0 (3,000 emails/month, 100/day, 1 webhook, 30-day retention); Pro $20/month (50,000 emails, 5 webhooks). Free is enough for staging; the daily cap of 100 is the thing to watch in the pilot.
- **Deliverability:** shared sending IPs by default; reputation depends on the provider's other tenants and on our domain's own reputation. A dedicated IP is available on higher plans (not needed at this volume). No independent deliverability data was gathered for this ADR.
- **Templates:** a send takes `html`/`text`, or a hosted template with variables. Decision: keep templates in our repo and render in the core service (one source, version controlled, testable offline); do not depend on provider-side templates.
- **Webhooks:** events for sent, delivered, delivery delayed, bounced, complained (plus opens/clicks, which we leave off). Deliveries are signed (Svix-style `svix-id`, `svix-timestamp`, `svix-signature` headers). Verify the exact event names and signature scheme against the current docs when N02 is built.
- **Pros:** simple HTTP API, small surface, good fit for a small team, free tier for staging, idempotency key supported on send (verify in N02).
- **Cons:** younger company than the alternatives; fewer deliverability tools and less public track record; webhook count is limited on lower plans (1 on Free, 5 on Pro).

### B. Postmark

- **Price (unverified, 25 Nov 2026):** Free 100 emails/month; Basic $15/month for 10,000 emails (overage $1.80 per 1,000); 45-day message retention.
- **Deliverability:** the strongest reputation of the three for transactional mail, because transactional and broadcast traffic are kept in separate message streams and the service screens senders. Claimed, not measured here.
- **Templates:** server-side templates with layouts and variables.
- **Webhooks:** delivery, bounce (with typed reasons), spam complaint, and more; configurable per stream.
- **Pros:** best-known deliverability and bounce handling; mature.
- **Cons:** costs more at the small end (no usable free tier: 100 a month); approval process for new accounts can be slower.

### C. Amazon SES

- **Price (unverified, 25 Nov 2026):** $0.10 per 1,000 emails; dedicated IP $24.95/month per IP; new accounts get Free Tier credit (up to $200 for 6 months).
- **Deliverability:** good when set up well, but reputation management is on us (shared pool by default, dedicated IPs optional). New accounts start in a sandbox until production access is requested.
- **Templates:** SES has its own templates but they are basic; we would render ourselves.
- **Webhooks:** no direct webhook. Events go through configuration sets to SNS (or EventBridge/Firehose), and we would subscribe an HTTPS endpoint and confirm the SNS subscription. More moving parts.
- **Pros:** cheapest by far at scale; the same cloud as many other services.
- **Cons:** most set-up work (IAM, SNS, sandbox exit); a new AWS dependency for a small team; weakest ergonomics.

### Comparison

| | Resend | Postmark | Amazon SES |
|---|---|---|---|
| Cost at pilot (about 5,000 a month), unverified | $0 to $20 | about $15 | under $1 |
| Free tier usable for staging | Yes (3,000/month, 100/day) | Barely (100/month) | Credit-based |
| Deliverability reputation | Good, newer | Best known | Depends on our set-up |
| Templates | Hosted or ours | Hosted | Basic |
| Bounce/complaint webhooks | Direct, signed | Direct | Via SNS |
| Set-up effort | Low | Low | High |
| Swappable behind our interface | Yes | Yes | Yes |

## Decision

**Use Resend** as the transactional email provider for the MVP, as already settled in System Requirements v0.2. Reach it only through an `EmailSender` interface in the core service so Postmark or SES can replace it with a new adapter and no change to business code. If the deliverability test (below) fails for any of Gmail, Yahoo or a Ugandan host, the fallback is Postmark.

Status stays Proposed until the owner signs off and the deliverability checks have been run (they are blocked on owner).

### The `EmailSender` contract (to be built in N02, #127)

```python
class EmailSender(Protocol):
    def send(self, message: EmailMessage, *, idempotency_key: str) -> SendResult: ...
    def parse_webhook(self, headers: Mapping[str, str], body: bytes) -> list[EmailEvent]: ...
```

| Item | Rule |
|---|---|
| `EmailMessage` | `to`, `subject`, `html`, `text` (always both), `from_address`, optional `reply_to`, `tags` (template name, notification id). No provider-specific fields |
| `SendResult` | `provider_message_id`, `accepted` (bool), `error` (typed: `rate_limited`, `invalid_address`, `provider_down`, `rejected`) |
| Idempotency | `idempotency_key` is the notification id plus attempt group; retries must never double-send. The job runner (ADR 0002) retries only `rate_limited` and `provider_down` |
| `parse_webhook` | Verifies the signature first (raises on failure, caller returns 401), then maps provider events to `EmailEvent(provider_message_id, kind, occurred_at, reason)`. Unknown events are ignored, never an error |
| Fakes | `FakeEmailSender` records messages and lets tests inject events; all N02 tests run offline against it |
| Secrets | API key and webhook signing secret come from environment variables, never from the repo |

### Webhook event mapping

| Our event (`kind`) | Resend | Postmark | Amazon SES (via SNS) | What the core service does |
|---|---|---|---|---|
| `delivered` | `email.delivered` | `Delivery` | `Delivery` | Set notification delivery status to delivered |
| `bounced` (hard) | `email.bounced` | `Bounce` (hard types) | `Bounce`, type Permanent | Mark status bounced; add the address to the suppression list; flag the account's email as undeliverable; for safeguarding mail, alert an admin and use push/phone |
| `bounced` (soft/transient) | `email.delivery_delayed` / transient `email.bounced` | `Bounce` (transient) | `Bounce`, type Transient, or `DeliveryDelay` | Record; retry per the job runner; suppress only after repeated failures |
| `complained` | `email.complained` | `SpamComplaint` | `Complaint` | Suppress the address immediately; never send non-essential mail to it again; review the template |

Exact provider event names and fields are written from memory of the public docs and are **to be verified** when the adapter is built; the mapping table is the contract, the names are the adapter's detail.

Rules that follow:

- A suppressed address is never sent to, except safeguarding mail where a human decides.
- Webhook handling is idempotent: events can repeat and arrive out of order (a `delivered` after a `bounced` must not reopen the address).
- The webhook endpoint is on the core service, public, signature-checked, and rejects stale timestamps.

## Sending domain plan (template; domain not chosen)

The owner has not chosen the domain. This is the plan with placeholders. `<domain>` is the registered domain the owner picks; recommended default is to use the organisation's main domain with a **dedicated subdomain for sending**, so a reputation problem cannot hurt the main domain's mail and the main domain's mail is not touched.

| Item | Recommended default |
|---|---|
| Sending subdomain | `mail.<domain>` (envelope/return-path and DKIM live here) |
| From address | `no-reply@mail.<domain>` for receipts and alerts; `safeguarding@mail.<domain>` for safeguarding mail |
| Reply-to | a monitored mailbox on the main domain, `support@<domain>` (a no-reply address on its own is a trust and compliance problem) |
| Separate streams | Use separate from-addresses (and, if the provider allows, separate API keys) for safeguarding vs billing so a billing complaint spike does not hurt safeguarding mail |

DNS records. Values come from the provider's dashboard when the domain is added; none are known yet. Do not invent them.

| Record | Name | Type | Value | Notes |
|---|---|---|---|---|
| SPF | `send.mail.<domain>` (provider's return-path host; Resend uses a `send` subdomain, verify) | TXT | `v=spf1 include:<provider-spf-host> ~all` from the dashboard | Only one SPF record per name; do not merge with the main domain's SPF |
| MX for bounces | same return-path host | MX | from the dashboard | Lets the provider receive bounces |
| DKIM | `<selector>._domainkey.mail.<domain>` | TXT or CNAME | from the dashboard | 1 or more selectors; keep them published for rotation |
| DMARC | `_dmarc.mail.<domain>` | TXT | `v=DMARC1; p=none; rua=mailto:dmarc@<domain>; adkim=s; aspf=s` to start | Start at `p=none` and read reports for 2 to 4 weeks, then `p=quarantine`, then `p=reject` once only our sources appear |
| DMARC for main domain | `_dmarc.<domain>` | TXT | `v=DMARC1; p=none; rua=mailto:dmarc@<domain>` | Required to receive reports for the whole domain; subdomains inherit unless `sp=` is set |

Alignment: the From domain (`mail.<domain>`) must align with the DKIM `d=` domain and the return-path domain, so DMARC passes on both SPF and DKIM.

### Owner checklist (blocked on owner)

1. Decide `<domain>` (default: the organisation's main domain) and confirm you control its DNS.
2. Create a Resend account (Free plan is enough for staging); add `mail.<domain>` as a sending domain; choose region (default: the one nearest Kampala that is offered; EU is acceptable, note DP-7/DP-9).
3. Create the DNS records Resend shows (SPF TXT, bounce MX, DKIM) at the DNS host; click Verify.
4. Add the DMARC records above at `p=none`.
5. Create a sending API key restricted to sending, and a webhook signing secret. Put both in the Fly.io secrets of the staging core service (ADR 0003); never in the repo or in chat.
6. Sign the provider's data processing agreement (DP-9) and note it in the processors register.
7. Send the staging test (next section) and report the results.

## Deliverability test, including the Ugandan-hosts edge case

Not run (needs the owner's domain, account and DNS; no real emails are sent by agents). The procedure, to run once the checklist above is done:

| Step | Pass criterion |
|---|---|
| Send the same transactional test (receipt template) from staging to: a Gmail address, a Yahoo address, a local Ugandan ISP or company mailbox (for example a MTN, Airtel or Africell-hosted or `.ug` company address), and one more (Outlook/Microsoft) | Lands in the inbox, not spam or promotions, for each |
| In each received message, view "show original" / headers | `spf=pass`, `dkim=pass`, `dmarc=pass`, aligned to `mail.<domain>` |
| Send to an invalid address on a real domain | A bounce webhook arrives and maps to `bounced` |
| Mark a test message as spam from a test mailbox | A complaint webhook arrives and maps to `complained` |
| Repeat for the Ugandan host a week later | Same result; reputation can differ over time |

Edge case, **provider blocks some Ugandan mail hosts**: some small local hosts and ISP relays use blocklists or greylisting, and some are known to reject mail from shared IP ranges. Mitigation, in order:

1. Make sure SPF, DKIM and DMARC pass, the From/reply-to are real, the body has a plain-text part, and the subject is plain.
2. Ask the local host or recipient to allow-list the sending domain; use the provider's logs to read the SMTP rejection reason (a bounce webhook carries it).
3. For safeguarding and payment-critical mail, never rely on email alone: send push as well (SR §8 already sends safeguarding by push and email together) and use SMS or a phone call by an admin for a hard-bounced safeguarding contact.
4. If the same Ugandan host rejects repeatedly, ask the provider for a dedicated IP, or switch to Postmark (the interface makes this a new adapter).
5. Record the bouncing host in the suppression notes so support can tell the parent to use another address.

## Consequences

- Delivery work is isolated behind `EmailSender`; N02 (#127) builds the Resend adapter, a fake, the webhook endpoint, and a suppression list, all with offline tests. Changing provider means adding one adapter and re-running the deliverability test.
- Appwrite Auth has its own SMTP for verification and password-reset mail. Decision for later (A-tickets): point Appwrite SMTP at the same verified domain so those messages also pass SPF/DKIM/DMARC, using Resend's SMTP endpoint. Not decided here.
- Resend is a data processor holding parents' and teachers' email addresses and message content: DP-9 needs a signed DPA and an entry in the processors register. Safeguarding emails should carry minimal detail ("a new case needs your attention, open the app"), not child names or case text, so a mailbox leak reveals little.
- Pricing is unverified and will drift. Pilot volume fits Free for staging and Pro ($20) for production at most; re-check before launch.
- The daily cap of 100 on Free makes it unsuitable for production; budget the Pro plan for the pilot.
- DMARC at `p=none` first means spoofing is not blocked at the start; moving to `quarantine` and `reject` is a tracked follow-up after two to four weeks of clean reports.
- Not done and **blocked on owner**: the domain choice, the Resend account, DNS changes, and the real inbox tests (three or more providers). Until then this ADR cannot move to Accepted.

## Sign-off

- [ ] Owner approves this ADR (change Status to *Accepted*)
- [ ] Owner has chosen the sending domain and completed the checklist
- [ ] Deliverability test passed for Gmail, Yahoo and a Ugandan host
