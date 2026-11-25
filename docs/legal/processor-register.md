# Processor register (C04, DP-9)

**Not legal advice.** Every statement about Ugandan law, controller/processor roles, cross-border transfer and breach duties here is **unverified, for lawyer confirmation** (see `lawyer-brief.md` Q11, Q19-Q23, ADR 0006). Vendor facts (regions, sub-processors, parent companies) are recalled, not verified against current vendor documents; each row says what the owner must collect. Machine-readable twin: `processors.json`; checked by `python3 docs/legal/check-processors.py` (every field present, every secret in the F09 inventory belongs to a processor, every integration in SR section 9 has an entry).

**Status today: no agreement is signed for any processor ("not signed", owner-blocked).** Signing needs the owner's accounts and the lawyer's view of minimum DPA content (DP-9, lawyer Q19). Roles assumed (ADR 0006 option B1, a proposal): the platform is the data controller; the vendors below are processors.


## Summary

| `id` | Provider | Phase | Data touched | Region / cross-border | Agreement | Owner-blocked |
|---|---|---|---|---|---|---|
| `appwrite-cloud` | Appwrite Cloud | MVP | child-data, parent-data, teacher-data, teacher-documents, phone, email, location, payment-data, safeguarding, credentials-tokens, logs-metadata | EU (Frankfurt) per ADR 0012 (cross-border: yes) | not signed | yes |
| `fly-io` | Fly.io | MVP | child-data, parent-data, teacher-data, phone, email, location, payment-data, credentials-tokens, logs-metadata | EU (fra) per ADR 0003 (cross-border: yes) | not signed | yes |
| `resend` | Resend | MVP | parent-data, teacher-data, email, child-data, logs-metadata | Chosen per sending domain when the domain is added (Resend docs, fetched 25 Nov 2026: https://resend.com/docs/dashboard/domains/introduction; the page does not list the regions or state data residency, so confirm in the dashboard). EU acceptable per ADR 0005. (cross-border: yes) | not signed | yes |
| `africas-talking` | Africa's Talking | MVP | phone, logs-metadata | Not confirmed: vendor site and privacy policy returned HTTP 403 to automated fetch on 25 Nov 2026, so headquarters, storage country and DPA availability remain unverified; owner to ask the vendor (cross-border: yes) | not signed | yes |
| `firebase-fcm-apns` | Firebase Cloud Messaging and Apple Push (APNs) | MVP | device-identifiers, parent-data, teacher-data, logs-metadata | Global (Google; US and other regions); Apple (global) (cross-border: yes) | not signed | yes |
| `google-maps` | Google Maps Platform | MVP | location, child-data, parent-data, device-identifiers, logs-metadata | Global (Google) (cross-border: yes) | not signed | yes |
| `sentry` | Sentry | MVP | logs-metadata, device-identifiers, credentials-tokens | EU (Frankfurt) or US (Iowa), chosen when the organisation is created and not changeable afterwards (Sentry docs, fetched 25 Nov 2026: https://docs.sentry.io/organization/data-storage-location/). Choose EU. (cross-border: yes) | not signed | yes |
| `mtn-momo` | MTN Mobile Money (Collections and Disbursements) | MVP | payment-data, phone, parent-data, teacher-data | Uganda (MTN Uganda; MoMo platform location not confirmed) (domestic) | not signed | yes |
| `airtel-money` | Airtel Money | Phase 2 | payment-data, phone, parent-data, teacher-data | Uganda (Airtel Uganda; not confirmed) (domestic) | not signed | yes |
| `card-gateway` | Card gateway (provider to be chosen) | Phase 2 | payment-data, parent-data, email | TBD with provider choice (cross-border: yes) | not signed | yes |
| `uptime-vendor` | Uptime monitoring vendor (Better Stack or UptimeRobot, owner chooses) | MVP | logs-metadata, email, phone | Vendor default region; not confirmed (cross-border: yes) | not signed | yes |
| `github` | GitHub (source, CI, project board) | MVP | source-code-only, credentials-tokens, teacher-data | Global (GitHub/Microsoft) (cross-border: yes) | not signed | yes |
| `codemagic` | Codemagic (CI/CD for the Flutter apps) | MVP | source-code-only, credentials-tokens | Vendor-hosted build machines; region not confirmed (cross-border: yes) | not signed | yes |
| `apple-app-store` | Apple App Store / App Store Connect | MVP | device-identifiers, logs-metadata, teacher-data | Apple, global (cross-border: yes) | not signed | yes |
| `google-play` | Google Play Console | MVP | device-identifiers, logs-metadata, teacher-data | Google, global (cross-border: yes) | not signed | yes |

## How to sign a processor agreement (owner steps, same for each row)

1. Ask the lawyer for the minimum DPA content (instructions only, confidentiality, security, sub-processor consent/notice, assistance with data-subject requests, breach notice to us within a stated time, return/deletion, audit, cross-border terms). Unverified until confirmed.
2. In the vendor account (never the live Appwrite project by an agent), accept or request the vendor's DPA. Where the vendor only offers click-through terms (Google, Apple, GitHub), record the acceptance date and the terms version.
3. Store the signed copy in the owner's evidence folder (outside the repo if it has commercial terms), then edit `processors.json`: `agreement_status` to `signed`, `signed_date`, and `dpa_link` to the evidence link. The validator rejects `signed` without a real http(s) link and date.
4. Collect the vendor's sub-processor list URL and breach-contact address into the entry.
5. If a vendor refuses (issue #142 edge case): replace the provider, or set `risk-accepted` with `risk_acceptance_ref` (a dated owner decision after the lawyer's advice) and keep the note in the PDPO plan item 14. Each entry has an `if_dpa_refused` default.
6. Review the register at every provider change and at least yearly (calendar reminder), and before the PDPO annual report.

Recommended defaults: sign the vendor's standard DPA where it exists; choose EU regions where the vendor lets you (Resend, Sentry); keep push payloads free of personal data; send Google only coordinates; keep GitHub free of production data.

## Entries

### `appwrite-cloud` Appwrite Cloud

- Role: processor. Phase: MVP. SR/ADR reference: Backend platform (ADR 0012).
- Purpose: Primary backend: authentication, database (all tables), file storage buckets (teacher documents, session evidence), realtime, functions (job tick).
- Data categories: child-data, parent-data, teacher-data, teacher-documents, phone, email, location, payment-data, safeguarding, credentials-tokens, logs-metadata.
- Region: EU (Frankfurt) per ADR 0012. Cross-border: yes. Stored in the EU while users and the controller are in Uganda; whether this needs a transfer undertaking or consent under the Act is open (ADR 0006, ADR 0012 DP-7 note, lawyer brief Q20-Q23). Appwrite Inc. is a US-parented company, so onward access is also a question. Legal position: unverified, for lawyer confirmation.
- Agreement: **not signed** (owner-blocked: yes). DPA link: TBD: link to the signed DPA copy (owner uploads to the evidence folder; never commit the PDF if it holds commercial terms).
- Sub-processors: Obtain Appwrite's current sub-processor list and change-notice terms with the DPA; list not yet collected.
- Secrets held for it (names only, `docs/ops/secrets-inventory.json`): `APPWRITE_API_KEY`.
- Exit and deletion procedure:
  1. Export every table and bucket with the core service export tooling plus the Appwrite console export (nightly export job if no point-in-time recovery, see memory.md question 7).
  2. Verify counts against the export, store the export encrypted in owner-controlled storage.
  3. Delete projects and keys in the console; request written deletion confirmation from Appwrite and file it as evidence.
  4. Revoke all API keys (docs/ops/secrets-inventory.json lists them) and rotate dependents.
- If the provider refuses a DPA: Record the refusal on the ticket. Replace the provider (preferred) or write a dated risk acceptance signed by the owner after the lawyer's advice; status becomes 'risk-accepted' with risk_acceptance_ref. Fallback noted in ADR 0012 option C: self-hosted Appwrite on EU infrastructure.
- Breach contact: TBD: vendor security/breach contact address from the signed DPA (breach duties: `breach-procedure.md`).

### `fly-io` Fly.io

- Role: processor. Phase: MVP. SR/ADR reference: Core service hosting (ADR 0003).
- Purpose: Hosts the core service (FastAPI) that processes API requests and jobs and holds runtime secrets.
- Data categories: child-data, parent-data, teacher-data, phone, email, location, payment-data, credentials-tokens, logs-metadata.
- Region: EU (fra) per ADR 0003. Cross-border: yes. Requests are processed in Frankfurt; transient processing and logs only, no primary database (ADR 0003). Fly.io Inc. is US-parented. Legal position: unverified, for lawyer confirmation.
- Agreement: **not signed** (owner-blocked: yes). DPA link: TBD: link to the signed DPA copy (owner uploads to the evidence folder; never commit the PDF if it holds commercial terms).
- Sub-processors: Obtain Fly.io's sub-processor list with the DPA; list not yet collected.
- Secrets held for it (names only, `docs/ops/secrets-inventory.json`): `FLY_API_TOKEN`.
- Exit and deletion procedure:
  1. Redeploy the core service to the replacement host from the container image (Dockerfile in services/core).
  2. Move secrets (docs/ops/secrets-inventory.json) and repoint DNS; run readiness checks.
  3. Destroy Fly apps and volumes; request deletion confirmation; revoke FLY_API_TOKEN.
- If the provider refuses a DPA: Record the refusal on the ticket. Replace the provider (preferred) or write a dated risk acceptance signed by the owner after the lawyer's advice; status becomes 'risk-accepted' with risk_acceptance_ref.
- Breach contact: TBD: vendor security/breach contact address from the signed DPA (breach duties: `breach-procedure.md`).

### `resend` Resend

- Role: processor. Phase: MVP. SR/ADR reference: Email provider (ADR 0005).
- Purpose: Transactional email (receipts, session notices, safeguarding notifications, account email).
- Data categories: parent-data, teacher-data, email, child-data, logs-metadata.
- Region: Chosen per sending domain when the domain is added (Resend docs, fetched 25 Nov 2026: https://resend.com/docs/dashboard/domains/introduction; the page does not list the regions or state data residency, so confirm in the dashboard). EU acceptable per ADR 0005.. Cross-border: yes. Email addresses, names and message content pass through the provider; region choice affects the transfer analysis. Resend is US-parented. Legal position: unverified, for lawyer confirmation.
- Agreement: **not signed** (owner-blocked: yes). DPA link: TBD: link to the signed DPA copy (owner uploads to the evidence folder; never commit the PDF if it holds commercial terms).
- Sub-processors: Obtain Resend's sub-processor list (it is understood to use AWS; confirm).
- Secrets held for it (names only, `docs/ops/secrets-inventory.json`): `RESEND_API_KEY`, `RESEND_WEBHOOK_SECRET`.
- Exit and deletion procedure:
  1. Switch the email adapter to the fallback provider (ADR 0005 names Postmark); re-verify SPF/DKIM/DMARC on the sending subdomain.
  2. Delete the domain, templates and logs in Resend; ask for deletion confirmation; revoke keys.
- If the provider refuses a DPA: Record the refusal on the ticket. Replace the provider (preferred) or write a dated risk acceptance signed by the owner after the lawyer's advice; status becomes 'risk-accepted' with risk_acceptance_ref. ADR 0005 fallback: Postmark.
- Breach contact: TBD: vendor security/breach contact address from the signed DPA (breach duties: `breach-procedure.md`).

### `africas-talking` Africa's Talking

- Role: processor. Phase: MVP. SR/ADR reference: Africa's Talking SMS.
- Purpose: SMS for one-time passwords only (SR section 9).
- Data categories: phone, logs-metadata.
- Region: Not confirmed: vendor site and privacy policy returned HTTP 403 to automated fetch on 25 Nov 2026, so headquarters, storage country and DPA availability remain unverified; owner to ask the vendor. Cross-border: yes. Phone numbers and OTP text are processed outside Uganda (country to be confirmed). Legal position: unverified, for lawyer confirmation.
- Agreement: **not signed** (owner-blocked: yes). DPA link: TBD: link to the signed DPA copy (owner uploads to the evidence folder; never commit the PDF if it holds commercial terms).
- Sub-processors: Obtain the sub-processor and carrier list; carriers are separate controllers of their own network data.
- Secrets held for it (names only, `docs/ops/secrets-inventory.json`): `AFRICASTALKING_API_KEY`.
- Exit and deletion procedure:
  1. Switch the SMS adapter to another gateway (or to email OTP, if the product allows).
  2. Close the account, delete message logs; request confirmation; revoke the API key.
- If the provider refuses a DPA: Record the refusal on the ticket. Replace the provider (preferred) or write a dated risk acceptance signed by the owner after the lawyer's advice; status becomes 'risk-accepted' with risk_acceptance_ref.
- Breach contact: TBD: vendor security/breach contact address from the signed DPA (breach duties: `breach-procedure.md`).

### `firebase-fcm-apns` Firebase Cloud Messaging and Apple Push (APNs)

- Role: processor. Phase: MVP. SR/ADR reference: Firebase Cloud Messaging, APNs.
- Purpose: Push notifications to the parent and teacher apps. Payloads must carry no child names, addresses or message bodies.
- Data categories: device-identifiers, parent-data, teacher-data, logs-metadata.
- Region: Global (Google; US and other regions); Apple (global). Cross-border: yes. Device tokens and notification payloads transit Google and Apple infrastructure. Apple is not a signable processor for APNs; terms are fixed (note for the lawyer). Legal position: unverified, for lawyer confirmation.
- Agreement: **not signed** (owner-blocked: yes). DPA link: TBD: link to the signed DPA copy (owner uploads to the evidence folder; never commit the PDF if it holds commercial terms).
- Sub-processors: Google Cloud/Firebase publishes a sub-processor list; accept via Google's data processing terms in the Firebase console. Apple's terms are non-negotiable.
- Secrets held for it (names only, `docs/ops/secrets-inventory.json`): `FCM_SERVICE_ACCOUNT_JSON`, `APNS_AUTH_KEY`.
- Exit and deletion procedure:
  1. Stop sending: unregister device tokens in Appwrite and the core service.
  2. Delete the Firebase project; revoke the APNs key in the Apple developer account.
- If the provider refuses a DPA: Google terms are accepted online (low refusal risk). If the lawyer objects, minimise payloads to 'you have an update' only and record risk acceptance for APNs, which cannot be negotiated.
- Breach contact: TBD: vendor security/breach contact address from the signed DPA (breach duties: `breach-procedure.md`).

### `google-maps` Google Maps Platform

- Role: processor. Phase: MVP. SR/ADR reference: Google Maps Platform.
- Purpose: Address pin, place search and travel-time estimates for matching.
- Data categories: location, child-data, parent-data, device-identifiers, logs-metadata.
- Region: Global (Google). Cross-border: yes. Home addresses/coordinates sent for geocoding and routing leave Uganda. Home address is sensitive for a child (safeguarding). Minimise: send coordinates not names, cache results (SR section 9), never send child names. Legal position: unverified, for lawyer confirmation.
- Agreement: **not signed** (owner-blocked: yes). DPA link: TBD: link to the signed DPA copy (owner uploads to the evidence folder; never commit the PDF if it holds commercial terms).
- Sub-processors: Google Cloud publishes sub-processors; DPA via Google Cloud's data processing amendment, accepted in the console.
- Secrets held for it (names only, `docs/ops/secrets-inventory.json`): `GOOGLE_MAPS_SERVER_KEY`, `GOOGLE_MAPS_ANDROID_KEY`, `GOOGLE_MAPS_IOS_KEY`.
- Exit and deletion procedure:
  1. Replace the maps adapter (SR section 9 requires adapters) with another provider; purge cached results older than the retention rule.
  2. Delete the Google Cloud project; revoke keys; remove client keys from app builds.
- If the provider refuses a DPA: Google terms are accepted online. If the lawyer rejects them, restrict to coordinates only or choose another provider via the adapter.
- Breach contact: TBD: vendor security/breach contact address from the signed DPA (breach duties: `breach-procedure.md`).

### `sentry` Sentry

- Role: processor. Phase: MVP. SR/ADR reference: Sentry.
- Purpose: Error tracking with PII scrubbing before send (docs/ops/runbook.md PII rules).
- Data categories: logs-metadata, device-identifiers, credentials-tokens.
- Region: EU (Frankfurt) or US (Iowa), chosen when the organisation is created and not changeable afterwards (Sentry docs, fetched 25 Nov 2026: https://docs.sentry.io/organization/data-storage-location/). Choose EU.. Cross-border: yes. Scrubbed events only: no request bodies, names, emails, phones or addresses by design (F08). Residual risk: free text in exceptions. Legal position: unverified, for lawyer confirmation.
- Agreement: **not signed** (owner-blocked: yes). DPA link: TBD: link to the signed DPA copy (owner uploads to the evidence folder; never commit the PDF if it holds commercial terms).
- Sub-processors: Sentry publishes a sub-processor list; obtain it with the DPA.
- Secrets held for it (names only, `docs/ops/secrets-inventory.json`): `SENTRY_DSN`, `SENTRY_AUTH_TOKEN`, `SENTRY_DSN_APP`.
- Exit and deletion procedure:
  1. Remove the DSNs from Fly secrets and app builds (the error reporter adapter falls back to the no-op).
  2. Delete the Sentry organisation/projects; request confirmation; revoke the auth token.
- If the provider refuses a DPA: Record the refusal on the ticket. Replace the provider (preferred) or write a dated risk acceptance signed by the owner after the lawyer's advice; status becomes 'risk-accepted' with risk_acceptance_ref. The error reporter has a no-op adapter, so Sentry can be dropped without code change.
- Breach contact: TBD: vendor security/breach contact address from the signed DPA (breach duties: `breach-procedure.md`).

### `mtn-momo` MTN Mobile Money (Collections and Disbursements)

- Role: processor. Phase: MVP. SR/ADR reference: MTN MoMo Collections and Disbursements.
- Purpose: Subscription collections (request-to-pay) and teacher payouts. MTN is probably also an independent controller of its own transaction data (lawyer to confirm).
- Data categories: payment-data, phone, parent-data, teacher-data.
- Region: Uganda (MTN Uganda; MoMo platform location not confirmed). Cross-border: no. Payer/payee MSISDN and amounts shared with MTN; likely domestic. If MTN's platform is hosted abroad that is outside our control. Legal position: unverified, for lawyer confirmation.
- Agreement: **not signed** (owner-blocked: yes). DPA link: TBD: link to the signed DPA copy (owner uploads to the evidence folder; never commit the PDF if it holds commercial terms).
- Sub-processors: Not applicable to us; MTN's API terms govern. Ask whether MTN will sign a DPA or only its standard API terms. Note: the MOMO_* variables read by packages/py_momo/scripts/manual_sandbox.py are a manual sandbox-script concern (owner's laptop, sandbox keys only, never CI); the production keys listed here are the core service's.
- Secrets held for it (names only, `docs/ops/secrets-inventory.json`): `MOMO_COLLECTION_SUBSCRIPTION_KEY`, `MOMO_COLLECTION_API_KEY`, `MOMO_DISBURSEMENT_SUBSCRIPTION_KEY`, `MOMO_DISBURSEMENT_API_KEY`.
- Exit and deletion procedure:
  1. Stop calling the API (feature flag in the payment adapter); settle pending transactions and reconcile.
  2. Close the product subscriptions; keep payment records per tax law (C03 retention).
- If the provider refuses a DPA: MTN is unlikely to negotiate. Record risk acceptance after the lawyer confirms the roles; the alternative is not to take mobile money, which is not viable for the pilot.
- Breach contact: TBD: vendor security/breach contact address from the signed DPA (breach duties: `breach-procedure.md`).

### `airtel-money` Airtel Money

- Role: processor. Phase: Phase 2. SR/ADR reference: Airtel Money.
- Purpose: Payments and payouts, Phase 2 only.
- Data categories: payment-data, phone, parent-data, teacher-data.
- Region: Uganda (Airtel Uganda; not confirmed). Cross-border: no. Phase 2; same analysis as MTN MoMo. Legal position: unverified, for lawyer confirmation.
- Agreement: **not signed** (owner-blocked: yes). DPA link: TBD: link to the signed DPA copy (owner uploads to the evidence folder; never commit the PDF if it holds commercial terms).
- Sub-processors: Not applicable until Phase 2.
- Secrets held for it (names only, `docs/ops/secrets-inventory.json`): `AIRTEL_CLIENT_SECRET`.
- Exit and deletion procedure:
  1. Not integrated yet: nothing to exit. Before Phase 2, sign/record the agreement.
- If the provider refuses a DPA: Same as MTN MoMo; do not integrate until the position is recorded.
- Breach contact: TBD: vendor security/breach contact address from the signed DPA (breach duties: `breach-procedure.md`).

### `card-gateway` Card gateway (provider to be chosen)

- Role: processor. Phase: Phase 2. SR/ADR reference: Card gateway.
- Purpose: Card payments, Phase 2; provider to be chosen (SR section 9).
- Data categories: payment-data, parent-data, email.
- Region: TBD with provider choice. Cross-border: yes. Unknown until a provider is picked. Prefer a PCI-compliant hosted checkout so we never store card data. Legal position: unverified, for lawyer confirmation.
- Agreement: **not signed** (owner-blocked: yes). DPA link: TBD: link to the signed DPA copy (owner uploads to the evidence folder; never commit the PDF if it holds commercial terms).
- Sub-processors: TBD with provider.
- Secrets held for it (names only, `docs/ops/secrets-inventory.json`): `CARD_GATEWAY_SECRET_KEY`, `CARD_GATEWAY_WEBHOOK_SECRET`.
- Exit and deletion procedure:
  1. Not integrated yet: nothing to exit. At selection, add the provider's real name, region and exit steps here.
- If the provider refuses a DPA: Choose a provider that signs a DPA; the DPA is a selection criterion.
- Breach contact: TBD: vendor security/breach contact address from the signed DPA (breach duties: `breach-procedure.md`).

### `uptime-vendor` Uptime monitoring vendor (Better Stack or UptimeRobot, owner chooses)

- Role: processor. Phase: MVP. SR/ADR reference: Uptime monitoring (F08).
- Purpose: Uptime checks and job-tick heartbeat; alert delivery to owner contacts.
- Data categories: logs-metadata, email, phone.
- Region: Vendor default region; not confirmed. Cross-border: yes. Checks hit health endpoints only (no personal data). The vendor holds the owner's alert contact details (email/phone), which are personal data of staff. Legal position: unverified, for lawyer confirmation.
- Agreement: **not signed** (owner-blocked: yes). DPA link: TBD: link to the signed DPA copy (owner uploads to the evidence folder; never commit the PDF if it holds commercial terms).
- Sub-processors: Obtain the vendor's sub-processor list. Holds the heartbeat URL (HS_HEARTBEAT_URL), which is a secret-bearing URL.
- Secrets held for it (names only, `docs/ops/secrets-inventory.json`): `HS_HEARTBEAT_URL`.
- Exit and deletion procedure:
  1. Export the check list from ops/uptime.json (kept as code) and recreate in another vendor.
  2. Delete the account; revoke heartbeat URLs.
- If the provider refuses a DPA: Low risk: switch vendor (ops/uptime.json is vendor-neutral).
- Breach contact: TBD: vendor security/breach contact address from the signed DPA (breach duties: `breach-procedure.md`).

### `github` GitHub (source, CI, project board)

- Role: processor. Phase: MVP. SR/ADR reference: Source hosting and CI.
- Purpose: Source code, CI secrets store and issue tracking. Must hold no personal data of children or parents: only developer/owner identities and CI secrets.
- Data categories: source-code-only, credentials-tokens, teacher-data.
- Region: Global (GitHub/Microsoft). Cross-border: yes. By design no production personal data. Risk is an accidental commit of data or secrets, handled by docs/ops/secrets.md and the secret scan. Legal position: unverified, for lawyer confirmation.
- Agreement: **not signed** (owner-blocked: yes). DPA link: TBD: link to the signed DPA copy (owner uploads to the evidence folder; never commit the PDF if it holds commercial terms).
- Sub-processors: GitHub publishes a sub-processor list (Microsoft); the GitHub DPA is incorporated in its terms.
- Secrets held for it (names only, `docs/ops/secrets-inventory.json`): none.
- Exit and deletion procedure:
  1. Mirror the repository and CI to another host; delete the repository after confirming the mirror.
  2. Rotate every secret stored in GitHub environments (docs/ops/secrets-inventory.json).
- If the provider refuses a DPA: GitHub terms are standard; a refusal is not realistic. If the lawyer objects, self-host Git and CI.
- Breach contact: TBD: vendor security/breach contact address from the signed DPA (breach duties: `breach-procedure.md`).

### `codemagic` Codemagic (CI/CD for the Flutter apps)

- Role: processor. Phase: MVP. SR/ADR reference: Mobile build and release (F05).
- Purpose: Builds, signs and uploads the parent and teacher apps to the app stores (F05).
- Data categories: source-code-only, credentials-tokens.
- Region: Vendor-hosted build machines; region not confirmed. Cross-border: yes. Reasoning: builds run on its machines from our source with signing material held as secure variables. No production end-user personal data is sent: builds use public client keys and no production data, but build logs and artifacts must be kept free of personal data (a test fixture with real names would change this). Staff identities (owner email) are held. Legal position: unverified, for lawyer confirmation.
- Agreement: **not signed** (owner-blocked: yes). DPA link: TBD: link to the signed DPA copy (owner uploads to the evidence folder; never commit the PDF if it holds commercial terms).
- Sub-processors: Obtain Codemagic's sub-processor list and DPA terms.
- Secrets held for it (names only, `docs/ops/secrets-inventory.json`): `CODEMAGIC_SECURE_VARIABLES`.
- Exit and deletion procedure:
  1. Move builds to another CI (GitHub Actions macOS runners) using the same signing material.
  2. Rotate the keystore password, App Store Connect API key and delete the Codemagic team and its secure variables; confirm deletion.
- If the provider refuses a DPA: Replace with another CI; low data risk, so a recorded risk acceptance is also reasonable after lawyer advice.
- Breach contact: TBD: vendor security/breach contact address from the signed DPA (breach duties: `breach-procedure.md`).

### `apple-app-store` Apple App Store / App Store Connect

- Role: processor. Phase: MVP. SR/ADR reference: App Store distribution.
- Purpose: Distribution of the iOS apps; Apple collects its own end-user, analytics and crash data and holds the developer-account data.
- Data categories: device-identifiers, logs-metadata, teacher-data.
- Region: Apple, global. Cross-border: yes. Apple acts largely as an independent controller of store and analytics data about end users; we receive aggregated reports only. We give Apple the developer account holder's data. Apple's terms are fixed and not negotiable. Legal position: unverified, for lawyer confirmation.
- Agreement: **not signed** (owner-blocked: yes). DPA link: TBD: link to the signed DPA copy (owner uploads to the evidence folder; never commit the PDF if it holds commercial terms).
- Sub-processors: Apple terms; not negotiable.
- Secrets held for it (names only, `docs/ops/secrets-inventory.json`): `IOS_DISTRIBUTION_CERTIFICATE`, `IOS_PROVISIONING_PROFILES`, `APP_STORE_CONNECT_API_KEY`.
- Exit and deletion procedure:
  1. Remove the apps from sale and delete the app records; revoke the App Store Connect API key and the distribution certificate and provisioning profiles; close the developer account if leaving iOS.
- If the provider refuses a DPA: Not negotiable: record risk acceptance after lawyer advice (the alternative is no iOS app).
- Breach contact: TBD: vendor security/breach contact address from the signed DPA (breach duties: `breach-procedure.md`).

### `google-play` Google Play Console

- Role: processor. Phase: MVP. SR/ADR reference: Play Store distribution.
- Purpose: Distribution of the Android apps; Google collects store, vitals and crash data about end users and holds developer-account data.
- Data categories: device-identifiers, logs-metadata, teacher-data.
- Region: Google, global. Cross-border: yes. Google acts largely as an independent controller of Play store and vitals data; we receive aggregated reports. Developer-account data is held by Google. Play terms are fixed. Legal position: unverified, for lawyer confirmation.
- Agreement: **not signed** (owner-blocked: yes). DPA link: TBD: link to the signed DPA copy (owner uploads to the evidence folder; never commit the PDF if it holds commercial terms).
- Sub-processors: Google terms; not negotiable.
- Secrets held for it (names only, `docs/ops/secrets-inventory.json`): `PLAY_UPLOAD_KEYSTORE`, `PLAY_UPLOAD_KEYSTORE_PASSWORD`.
- Exit and deletion procedure:
  1. Unpublish and delete the apps; rotate to a new upload key via Play App Signing key reset; close the developer account if leaving Android.
- If the provider refuses a DPA: Not negotiable: record risk acceptance after lawyer advice.
- Breach contact: TBD: vendor security/breach contact address from the signed DPA (breach duties: `breach-procedure.md`).

## First-party secrets (no third party holds them)

Listed in `first_party_secrets` in `processors.json`. The validator refuses this marker for any secret with a vendor name prefix or a vendor consumer (app-stores, codemagic, firebase-console), so it cannot hide a third-party credential.

- `JOB_TICK_SECRET`: Shared secret we generate between the core service and the job-tick function; no third party chooses or holds it beyond hosting it as a variable (Appwrite and Fly already covered as processors).
- `ADMIN_COOKIE_SECRET`: Our own admin app cookie-signing secret; generated by us, held by the host environment only.

## Not processors (for the lawyer to confirm)

- Mobile network operators and app stores (Apple, Google Play) act on their own terms; they are not engaged by us as processors (unverified).
- Teachers: whether they are separate controllers for what they record in session logs is lawyer Q11.
