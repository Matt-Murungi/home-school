# Travel-time service (X01, #85)

Answers "minutes of travel between two zones at a given time" for matching (SR 7.2: travel-time filter and 30% score, MAT-*) using Google Routes, cached per zone pair. Code: `services/core/src/hs_core/travel/`. Tables: `appwrite/schema/geo.json` (`travel_time_cache`, `travel_usage`), migration `0007_geo_schema.py`. Job: `travel-cache-refresh` in `jobs/schedule.py`.

## How it behaves

| Situation | Answer | `estimated` |
|---|---|---|
| Same zone | `TRAVEL_WITHIN_ZONE_MINUTES` (10), no provider call | false (`same_zone`) |
| Zone without a valid centroid | 30 minutes (`default`), warning log with the zone id | true |
| Fresh cache row for (origin, destination, band) | cached minutes | false (`cache`) |
| Miss or expired | one provider call, batched per destination, single-flight per pair | false (`provider`) |
| Failure, over budget, no key | last known value (`stale_cache`), else straight line x 1.4 / speed (`haversine`) | true |

Never raises into matching. Keys: origin zone id, destination zone id, band (`morning_peak` 06:30-09:30, `off_peak` 09:30-15:30, `evening_peak` 15:30-20:00, `night` 20:00-06:30; weekends have no peaks; Africa/Kampala wall clock). Direction matters (A to B is cached separately from B to A). TTL 7 days with +-10% jitter per key (no refresh stampede); the daily job refreshes entries within one day of expiry.

Failures are cached negatively (5 minutes; 30 minutes for quota; 1 day for "no route"; 1 hour for implausible answers) without erasing the last good value. A provider answer is rejected (never cached) when it is not positive, over 6 hours, or implies more than 130 km/h for zones more than 500 m apart. Retries: up to 3 attempts, backoff 0.5 s then 1 s (cap 4 s); a `Retry-After` is honoured only up to 5 s, otherwise the lookup falls back immediately.

Per-session lookups (exact home points) exist as `session_minutes(..., close_call=True)`; use `needs_session_lookup(estimate, limit, margin)` to decide. They count against the budget, are never cached and never logged.

## Cost guard and alerts

Elements (origin x destination pairs) are counted per Africa/Kampala day and month in `travel_usage` (defaults: 1,000 a day, 15,000 a month; warming may use at most half of the daily budget). Over the daily cap: cache and fallbacks only, one `daily_budget_exhausted` alert per day. Monthly: `monthly_budget_threshold` alert at 80% and 100%, hard stop at 100%. Alerts go to the `TravelAlertSink` (default: ERROR log `travel alert`, picked up by Sentry's logging integration; a real sink is a follow-up). Counters are read-modify-write without atomic increment, so several instances can overshoot a cap by about one batch; the Google Cloud budget and quota cap below are the hard stop.

Metrics (`TravelMetrics.log_snapshot`, structured JSON, zone ids only, never coordinates): lookups, cache_hits, cache_misses, negative_hits, stale_served, fallbacks, provider_calls, provider_elements, provider_failures, rate_limited, rejected_responses, budget_denied, close_call_lookups, cost_micro_usd.

## Fallback assumptions (Kampala, unverified)

Road distance = straight line x 1.4 (hills, swamps and a sparse arterial network make roads much longer than the crow flies). Speed 20 km/h average, x0.7 in the peaks, x1.4 at night. Example: Kololo to Entebbe, 34 km straight, off-peak: about 144 minutes (real driving is roughly 60 to 90 minutes: this fallback is deliberately pessimistic for far zones and should be calibrated with the owner's real-route check below). Both numbers are settings.

## Google adapter status

`GoogleRoutesProvider` is written from Google's public Routes API documentation (`POST https://routes.googleapis.com/distanceMatrix/v2:computeRouteMatrix`, `X-Goog-Api-Key`, `X-Goog-FieldMask: originIndex,destinationIndex,duration,condition,status`, `travelMode DRIVE`, `routingPreference TRAFFIC_AWARE`, future `departureTime`) and has only run against a mocked transport. UNVERIFIED against the live service: element limit (100 used), response streaming shape, error bodies for quota. No key exists, so no real call has ever been made.

## Owner steps (blocked: Google account, billing)

1. Create a Google Cloud project per environment (staging, production) with billing enabled (card or invoicing).
2. Enable the Routes API (the later Places work for the address pin is separate, with its own restricted client keys in the F09 inventory).
3. Create a server API key, restrict it to the Routes API only; restrict by IP if you allocate a static Fly egress IP (`fly ips allocate-egress -a <fly-app>`), otherwise rely on API restriction plus the quota cap. Store it as `GOOGLE_MAPS_SERVER_KEY` with `fly secrets import -a <fly-app>` (inventory row exists; rotation procedure `provider-key`). Never in the repo.
4. Quotas: set a per-day cap on Compute Route Matrix elements below the daily default (recommended 1,500 a day).
5. Billing budget with email alerts to the founder at 50%, 90% and 100% of a monthly amount (recommended USD 50 to start). Budget alerts notify but do not stop spend: the quota cap in step 4 does.
6. Set the non-secret `TRAVEL_*` variables per environment only if the defaults need changing (inventory lists them).
7. Real-route sanity check (needs the key): for about 10 Kampala zone pairs (near, medium, across town, Entebbe) call the adapter once for `evening_peak` and compare with local knowledge or a manual Google Maps check at 17:30; adjust `TRAVEL_FALLBACK_SPEED_KMH` and `TRAVEL_FALLBACK_CIRCUITY`; confirm the element limit and error shapes in the adapter; confirm the price per element and set `TRAVEL_ELEMENT_COST_MICRO_USD`.
8. Apply `0007_geo_schema` to a non-live project (F02 runner), run one `travel-cache-refresh` on staging and check the `travel provider call` log lines and the `travel_time_cache` rows.

Rough cost: 30 zones is 870 ordered pairs; two warm bands about 1,740 elements a week, about 7,500 a month, so a monthly cap of 15,000 leaves room for on-demand, other bands and close-call lookups. At an assumed USD 0.01 per element the expected spend is about USD 75 a month before any free tier (unverified: check the current Routes price list).
