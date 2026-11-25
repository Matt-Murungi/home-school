# Monitoring setup: owner-blocked items and exact steps

Nothing here is created by the build. No payment-bearing plan is chosen; every default is a free tier. Do the steps once, then record only the non-secret facts (project names) in `memory.md`.

| # | Item | Recommended default | Exact steps |
|---|---|---|---|
| 1 | Sentry account and projects | Sentry free (Developer) tier, one organisation, EU data region if offered | Sign up. Create projects `hs-core` (Python/FastAPI), `hs-admin` (Next.js), `hs-parent-app` and `hs-teacher-app` (Flutter), `hs-worker` (Python; the job runner once F04 lands). In each project settings turn on "Prevent Storing of IP Addresses" and "Data Scrubber" + "Use Default Scrubbers" (server-side backstop to our `before_send`). |
| 2 | DSNs | One DSN per project and per environment (use Sentry environments `staging` and `production`, same project) | Copy each DSN. Core/worker: Fly secret `SENTRY_DSN` (`fly secrets set SENTRY_DSN=... -a <app>`). Admin: env var `NEXT_PUBLIC_SENTRY_DSN` (browser) / `SENTRY_DSN` (server) in the host. Flutter: `--dart-define=SENTRY_DSN=...` in the Codemagic/CI build. Never commit a DSN. Leave unset in dev: reporting is off. |
| 3 | Alert routing (owner emails and phone) | Founder email + founder phone push (Sentry mobile app / Better Stack app); ops email group for tickets | In each Sentry project create issue alerts that match `ops/alerts.json` (tags `alert=...`, thresholds, action interval = `throttle_minutes`). Add owner email and install the mobile app for push. SMS/WhatsApp is a later paid add-on: decide only if push proves unreliable on Ugandan networks. |
| 4 | Uptime vendor account | Better Stack free tier (heartbeat + monitors); UptimeRobot free as the independent second check | Create account. Add monitors from `ops/uptime.json` (6 checks), 1 minute for `/health`, expected status as listed, confirm after 2 failures. Add a heartbeat monitor (`core-job-tick`, period 5 min, grace 10 min); store its URL as Fly secret `HS_HEARTBEAT_URL`. Add one UptimeRobot monitor on production `/health` as the second opinion. Route alerts to the founder email and push. Free-tier check intervals may be 3 minutes: if so, record it as a deviation, do not buy a plan without owner approval. |
| 5 | Hosts | Needed before 4 | Staging/production API and admin hostnames come from the Fly.io / admin deploy tickets; put them in the vendor monitors. |
| 6 | Log shipping (optional) | Fly.io logs (`fly logs`) are enough for the pilot | Structured JSON lines already carry `request_id`. A log drain (Better Stack Logs free) can be added later; the `api-5xx-rate` alert can run on Sentry or the vendor's log alerts. |

Alert routing is the item 3 row above; the uptime heartbeat is in row 4.

## Verification once set up

Run the "Monthly monitoring test" in `runbook.md` on staging: a deliberate exception shows in Sentry without personal data; stopping the staging API pages the founder.
