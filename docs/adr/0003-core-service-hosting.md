# ADR 0003 — Core service hosting: Fly.io, Frankfurt (`fra`)

- **Status:** Proposed — awaiting owner sign-off
- **Date:** 25 November 2026
- **Ticket:** D03 (#23)
- **Related:** System Requirements v0.2 §2, §3, §4, §11 (DP-7), §12 (NFR-2, NFR-5, NFR-9); ADR 0002 (job runner); ADR 0012 (Appwrite Cloud Frankfurt and permissions)

## Context

The core service is the trusted half of the backend (SR §3, §6.3). It is a Python 3.12 / FastAPI application that:

- holds the Appwrite server API key, which never ships in a client
- verifies each caller's Appwrite JWT
- owns matching, scheduling, payments, payouts, the ledger, tiers, assessments and notifications
- grants and revokes row permissions (ADR 0012)
- executes the scheduled jobs that an Appwrite Function ticks (ADR 0002)

It must be reachable from phones in Kampala, from the Next.js admin, from MoMo and Airtel callbacks and from Appwrite Functions. It must be close to Appwrite Cloud, which is in Frankfurt, because one client request may cause several Appwrite calls.

Targets that bear on hosting:

| ID | Requirement |
|---|---|
| NFR-2 | Check-in and log submission respond within 3 s when online |
| NFR-5 | 99.5% monthly availability; no planned maintenance weekdays 15:00–20:00 or weekends 08:00–18:00 |
| NFR-9 | Year one: 5,000 active children, 1,000 teachers, 20,000 sessions a month |
| SR §4 | Separate staging and production, each with its own keys and secrets |
| SEC-3 | Secrets in a secrets manager, never in apps or repositories |
| DP-7 | Hosting region and cross-border transfer reviewed (see the DP-7 brief below) |

## Decision drivers

| Driver | Weight |
|---|---|
| Few moving parts for a small team; container-based so the host can change later | High |
| Same region as Appwrite Cloud (Frankfurt), to keep core-to-Appwrite calls short | High |
| Separate staging and production apps with separate secrets | High |
| At least two instances in production for NFR-5; scale out without re-architecture | High |
| Predictable low cost at year-one scale | Medium |
| Reasonable latency from Kampala | Medium |
| Legal position on data location (DP-7) | Medium, open |

## Options considered

### A. Fly.io, region `fra` — chosen

Containers ("Machines") built from a Dockerfile, deployed with `fly deploy`.

- **Pros:**
  - Container image is portable: nothing in the service is Fly-specific beyond `fly.toml`.
  - Per-app secrets, per-app configuration, rolling deploys and health checks.
  - Staging and production are separate apps, so credentials and scaling are independent.
  - Frankfurt region, the same city as Appwrite Cloud.
  - Pay-per-use, no platform fee. Small machines cost a few dollars a month.
- **Cons:**
  - A smaller company than the hyperscalers, with a history of occasional platform incidents. Two machines on separate hosts reduce, but do not remove, this risk.
  - Built-in autoscaling is limited (see the capability checklist).
  - No Africa-hosted option short of the Johannesburg region, which is not measured here.

### B. A PaaS such as Render or Railway

- **Pros:** very simple, Git-driven deploys.
- **Cons:** fewer region and networking controls, and the same cost class. No advantage over A for this workload. Not rejected on principle: because the service is a container, this stays a fallback.

### C. AWS (ECS Fargate or App Runner) in `eu-central-1`, or `af-south-1` (Cape Town)

- **Pros:**
  - Strongest SLAs and compliance paperwork.
  - `af-south-1` would keep the service on the African continent.
- **Cons:**
  - Far more setup and a higher fixed cost (load balancer, NAT) than a small team needs at pilot scale.
  - Cape Town to Appwrite Frankfurt would add a long hop to every Appwrite call.

### D. A self-managed VPS (for example Hetzner) with Docker

- **Pros:** cheapest at steady load.
- **Cons:** the team patches, monitors and fails over the host itself. That conflicts with NFR-5 for a small team.

### E. Run the logic in Appwrite Functions

Rejected in ADR 0002 (option C): splits money and matching rules from the tested core service, and function timeouts limit long jobs.

## Decision

Host the core service on **Fly.io in `fra`**, as two apps:

| App | Purpose | Machines | Appwrite project |
|---|---|---|---|
| `homeschool-core-staging` | End-to-end tests, demos, pilot teachers, MoMo sandbox | 1 (may stop when idle) | `hs-staging` |
| `homeschool-core-prod` | Live | 2, on separate hosts, always running | `hs-prod` |

App names are proposals. They must be globally unique on Fly, so check availability when creating them.

Both apps use `primary_region = "fra"`. Each app has its own secrets; production secrets are never set on staging, and the reverse (SR §4).

### Fly.io capability checklist

The ticket requires containers, secrets, autoscaling and separate staging/production apps. The right-hand column says how each is met. Items marked **verify** are from Fly's public documentation and the author's knowledge, and are confirmed only by the first staging deploy.

| Capability needed | How Fly.io meets it | Status |
|---|---|---|
| Containers | Dockerfile (or prebuilt image) deployed with `fly deploy`; Fly runs it as a Firecracker microVM ("Machine") | Standard feature; confirm in staging |
| Secrets | `fly secrets set NAME=value --app <app>`; stored encrypted, exposed to the app as environment variables, never shown by `fly secrets list` (names only). Staged secrets trigger a rolling restart | Standard feature; confirm in staging |
| Separate staging and production | One Fly app per environment, each with its own `fly.toml`, secrets, certificates and scaling. Optionally one Fly organisation per environment for access control | Standard feature |
| Autoscaling | Fly Proxy can auto-stop and auto-start machines on traffic (`auto_stop_machines`, `auto_start_machines`, `min_machines_running` in `fly.toml`). Scaling the count is `fly scale count N`. A metrics-driven autoscaler exists as a separate add-on. **Year-one decision:** fixed 2 machines in production (NFR-5), manual scale-up, because 20,000 sessions a month is a low request rate. Revisit if p95 latency or CPU alarms fire | **verify** the autoscaler option before relying on it |
| High availability | Two or more machines in the same region; Fly places them on different hosts. HTTP health checks in `fly.toml` take unhealthy machines out of rotation | **verify** placement on first deploy |
| Region | `primary_region = "fra"` | Standard feature |
| Custom domain and TLS | `fly certs add api.<domain>`; certificate issued and renewed by Fly. The mobile apps pin the certificate (SEC-9): pin the public key or the issuing CA, not a leaf that renews every 90 days | Needs the domain (owner). See follow-ups |
| Logs and metrics | `fly logs`; ship structured logs and Sentry events from the app (NFR-17) | Standard feature |
| Deploy from CI | `fly deploy` with a deploy-scoped token (`fly tokens create deploy`) held as a GitHub Actions secret | Standard feature |
| Inbound reachability | Public HTTPS endpoint for MoMo/Airtel callbacks and the signed `/internal/jobs/tick` call | Standard feature |
| Internal-only endpoints | `/internal/*` is on the public URL, protected by HMAC signature and timestamp (ADR 0002 rule 8). A private network path from Appwrite is not available because Appwrite Cloud is outside Fly | Accepted |

### Latency

All numbers were measured on 25 November 2026 at about 09:30 UTC from the builder's workstation. The outbound IP geolocated to Kampala, Uganda (AS36924), so this is a Kampala network. The link type was not verified and is probably a fixed (fibre) line, **not** a mobile network. The measured URL was the public, unauthenticated `GET https://fra.cloud.appwrite.io/v1/health/version`, with no credentials. 20 requests with `curl -w`, each on a fresh connection, 0.3 s apart. All returned HTTP 200.

| Stage (milliseconds) | min | median | p95 (19th of 20) | max |
|---|---|---|---|---|
| TCP connect (about one round trip) | 70 | 83 | 97 | 162 |
| TLS finished | 144 | 167 | 199 | 255 |
| Response complete | 393 | 420 | 518 | 526 |

Reading it:

- One round trip from this Kampala network to Frankfurt is about 70–100 ms.
- A brand-new HTTPS request costs about 0.4 s. The difference between "TLS finished" and "response complete", about 250 ms at the median, is one round trip plus Appwrite's own processing.
- A connection that is kept alive pays only that last part.

What this means for NFR-2 (check-in within 3 s):

- A check-in goes phone → core service (Fly `fra`) → Appwrite (Frankfurt) and back. The phone-to-Frankfurt leg is what was measured. The core-to-Appwrite leg should be a few milliseconds if both sit in Frankfurt, but that is **not measured**.
- Estimate: 0.4 s for a cold connection, about 0.25 s for a warm one, plus a handful of Appwrite calls inside Frankfurt. This should fit the 3 s budget on a fibre-class link with room to spare.
- A mobile network will be slower and more variable. That is unmeasured and could change the conclusion.
- The mitigation is already in the design: the apps queue check-ins and logs in Drift (SQLite) and send them when a request succeeds, with clear status (NFR-2, SR §3). If measured mobile latency or loss is bad, the offline queue is the fallback, not a different region.

**Not measured (blocked on the owner):**

1. Kampala to Fly.io `fra`. This needs a deployed app. Throwaway recipe is in the runbook below.
2. Mobile network (MTN and Airtel) in Kampala, both to Appwrite and to Fly. This needs a phone or a hotspot on each carrier.
3. Core service (Fly `fra`) to Appwrite Frankfurt.

To repeat the Appwrite measurement:

```
for i in $(seq 1 20); do
  curl -s -o /dev/null -w "%{time_connect} %{time_appconnect} %{time_total} %{http_code}\n" \
    https://fra.cloud.appwrite.io/v1/health/version; sleep 0.3
done
```

### Year-one monthly cost estimate

**Estimate only.** Prices were read on 25 November 2026 from Fly.io's pricing page (`docs.fly.io/about/pricing`). Those list prices are for the `iad` and `ewr` regions. The `fra` price may differ slightly and **was not verified**. Fly bills by the second; figures here assume full-month runtime.

| Item | Basis | USD a month |
|---|---|---|
| Production: 2 × `shared-cpu-1x`, 1 GB | 2 × $6.70 | 13.40 |
| Staging: 1 × `shared-cpu-1x`, 512 MB | $3.69 (less if stopped when idle) | 3.69 |
| Dedicated IPv4 for production (optional; shared IPv4 and IPv6 are free) | $2 | 2.00 |
| Outbound data | Assumed under 10 GB at $0.02 per GB: 20,000 sessions × about 30 API calls × about 5 KB is about 3 GB | 0.20 |
| **Total, no support plan** | | **about 19–20** |
| Optional Fly Standard support | $29 | +29 |

The sizing is an assumption: a FastAPI service doing I/O-bound calls to Appwrite should fit in 1 GB at this volume. Load-test it in F03 and resize. The year-one cost range for the whole stack, including Appwrite, is in ADR 0012.

## Consequences

- **Positive:**
  - Same city as Appwrite; staging and production isolated; a standard container, so moving host later is a Dockerfile-and-secrets exercise.
  - Cost is small relative to the rest of the stack.
- **Negative:**
  - The service is outside Uganda. Every request crosses the Africa–Europe link (about 80 ms a round trip as measured), and children's personal data is processed in the EU, which feeds the DP-7 review below.
  - Fly's autoscaling is not a managed horizontal autoscaler. Capacity is planned by hand at this stage.
  - A single region has no regional failover. A Fly `fra` incident takes the core service down (NFR-5 risk). Accepted for the pilot; revisit with a second region if the 99.5% target is missed.
- **Follow-ups:**
  - F03 (#29): Dockerfile, `fly.toml` for each environment, health endpoint, CI deploy, load test and resize. Run the Kampala to Fly measurement and update this ADR.
  - Choose the API domain (owner) before pinning certificates (SEC-9).
  - Re-check the `fra` machine price when the staging app exists (`fly platform` or the billing page).

## Staging runbook (not yet executed)

**Nothing below has been run.** Appwrite is read-only to agents and there is no Fly account. A developer with both accounts should be able to build a staging environment from this and ADR 0012. Placeholders in angle brackets are for the person to fill in. Never paste real secret values into the repository, a ticket or a shell history you share.

```
# 0. One-time: install flyctl and sign in (opens a browser)
fly auth login

# 1. Create the app in the Fly organisation (check the name is free)
fly apps create homeschool-core-staging --org <org-slug>

# 2. Config: services/core/fly.staging.toml (written in F03), containing at least
#    app = "homeschool-core-staging"
#    primary_region = "fra"
#    [http_service] internal_port = 8080, force_https = true,
#                   auto_stop_machines = "stop", auto_start_machines = true,
#                   min_machines_running = 0
#    [[http_service.checks]] path = "/healthz", interval = "15s", timeout = "5s"

# 3. Secrets (names only here; values come from your password manager and from
#    the Appwrite console in the next section)
fly secrets set --stage --app homeschool-core-staging \
  APPWRITE_ENDPOINT=https://fra.cloud.appwrite.io/v1 \
  APPWRITE_PROJECT_ID=<staging-project-id> \
  APPWRITE_API_KEY=<staging-server-key> \
  JOB_TICK_SECRET=<random-32-bytes-hex> \
  MOMO_ENV=sandbox
# (provider credentials are added by the payments tickets, sandbox values only)

# 4. Deploy and check
fly deploy --config services/core/fly.staging.toml --app homeschool-core-staging
fly status --app homeschool-core-staging
curl -fsS https://homeschool-core-staging.fly.dev/healthz

# 5. Latency check from a Kampala machine or phone hotspot (measurement still owed)
for i in $(seq 1 20); do curl -s -o /dev/null \
  -w "%{time_connect} %{time_appconnect} %{time_total}\n" \
  https://homeschool-core-staging.fly.dev/healthz; done

# 6. Production later: same steps with homeschool-core-prod, min_machines_running = 2,
#    auto_stop_machines = "off", and `fly scale count 2 --region fra`.
```

The Appwrite half of the runbook is in ADR 0012.

## Open items and blockers

All of these need the owner.

| Item | Needed from | Recommended default |
|---|---|---|
| Fly.io account, organisation and billing | Owner | One organisation, two apps (staging, prod) |
| Throwaway Fly app to measure Kampala to `fra`, and mobile-network measurements | Owner (or a Kampala-based person with a phone on each carrier) | Do it in the F03 staging deploy |
| Support plan (Standard, $29 a month) | Owner | Skip for the pilot; revisit before public launch |
| Sign-off on this ADR | Owner | Accept |

## Sign-off

- [ ] Owner approves this ADR (change Status to *Accepted*)
