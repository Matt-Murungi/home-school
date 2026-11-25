# ADR 0002 — Job runner: Appwrite Functions tick, core service runs the jobs

- **Status:** Proposed — awaiting owner sign-off
- **Date:** 25 November 2026
- **Ticket:** D02 (#22)
- **Related:** System Requirements v0.2 §3, §7, §14 and NFR-7, NFR-8, NFR-17; ADR 0003 (Fly.io core service); ADR 0012 (Appwrite Cloud)

## Context

The platform depends on scheduled and background work. Examples:

| Job | Cadence | Notes |
|---|---|---|
| Auto-confirm sessions past 48 h | Hourly | SR §7.4 |
| Session reminders, renewal reminders, grace-period expiry | Every few minutes / daily | Notifications, subscriptions |
| Matching offer expiry and next offer | Every minute | NFR-3: first offer within 1 minute |
| Weekly payout batch preparation | Weekly | Money — must apply exactly once (NFR-8) |
| Monthly tier evaluation | Monthly | Long-running over all teachers |
| Retention enforcement | Daily | DP-6 |
| Address-access revocation 24 h after last session | Every few minutes | ADR 0012 permissions |

NFR-7 requires jobs to be idempotent and safe to re-run. Business rules belong in the core service (Python/FastAPI on Fly.io), not in scattered scripts.

**What already exists.** The Home School Appwrite project already has `job_run` and `job_attempt` tables (no rows, no functions deployed). Their columns describe a database-backed queue:

- `job_run`: `name`, `dedupe_key`, a `trigger` of schedule, enqueue or manual, and a `status` of queued, running, completed, failed or skipped.
- Also on `job_run`: `attempts` and `max_attempts`, `run_at`, `scheduled_for`, and the lease field `locked_until`.
- Also on `job_run`: `last_error`, plus `actor_id` and `reason` for manual runs.

This ADR adopts that design rather than replacing it.

## Decision drivers

| Driver | Weight |
|---|---|
| Exactly-once effect for money jobs; safe re-runs (NFR-7, NFR-8) | High |
| No lost work when something is down (missed ticks catch up) | High |
| Little extra infrastructure to run and pay for | High |
| Business logic stays in the core service with its tests | High |
| Delayed jobs (run at a time), retries with backoff, manual re-runs with audit | Medium |
| Visibility: every run recorded and failures alerted (NFR-17) | Medium |

## Options considered

### A. Appwrite Function cron tick + job queue in Appwrite, executed by the core service — chosen

An Appwrite Function `job-tick` runs every minute. It sends one signed HTTP request to `POST /internal/jobs/tick` on the core service. The core service then:

1. creates `job_run` rows for every scheduled period that has become due
2. claims due runs
3. runs them within a time budget
4. records the outcome

Other code enqueues work (for example "send renewal reminder at 09:00") by inserting a `job_run` with a future `run_at`.

- **Pros:**
  - Nothing new to host. Appwrite already runs the cron, and Fly.io already runs the core service.
  - All logic is Python in the core service, with unit tests.
  - Runs are visible as rows that the admin dashboard can list.
- **Cons:**
  - Cron granularity is one minute.
  - Throughput is bounded by what one tick can do, which is ample at year-one scale (NFR-9).
  - The pattern depends on careful claim logic, because Appwrite has no conditional update. See *Claiming without a conditional update*.

### B. Celery or Dramatiq workers with Redis on Fly.io

- **Pros:** a mature retry and scheduling ecosystem, sub-second latency, and high throughput.
- **Cons:**
  - Redis and worker machines to run, monitor and pay for.
  - Beat or scheduler high availability needs care.
  - Job state lives in Redis, which needs separate visibility and backups.
  - Exactly-once still needs application idempotency.
  - It is more than the scale requires.

### C. Business logic inside Appwrite Functions

- **Pros:** no core-service endpoint needed.
- **Cons:**
  - Splits money, matching and session rules across function code and the core service.
  - Harder to test together.
  - Function timeouts limit long jobs.
  - Rejected: it breaks the "trusted state changes in one service" principle (SR §3).

### D. Postgres-backed queue (e.g. pg-boss, procrastinate)

Not applicable: there is no Postgres since ADR 0012. Revisit only if the ledger moves to Postgres (SR §14, open item 2).

## Decision

Adopt option A.

### How it works

```
Appwrite cron (every minute)
  └─ Function job-tick ── signed POST /internal/jobs/tick ──► Core service (Fly.io)
                                                                ├─ materialise due scheduled runs
                                                                ├─ claim due runs (job_attempt insert)
                                                                ├─ run handlers within a 20 s budget
                                                                └─ update job_run; alert on final failure
```

### Rules

1. **Dedupe by row ID.** A run's row ID is a hash of its `dedupe_key`, for example `auto-confirm:2027-01-18T09:00Z` or `payout:2027-W03`. Appwrite refuses a second row with the same ID, so a period or an enqueued task exists once however many ticks overlap. The existing `job_run` has no unique index on `dedupe_key`; the deterministic ID makes one unnecessary.
2. **Claiming without a conditional update.** Appwrite cannot update a row only if it is unchanged. Instead, a worker claims an attempt by creating the `job_attempt` row with a deterministic ID derived from (run, chunk, attempt). Only one creator succeeds; the others get 409 Conflict and skip. The winner then sets `status=running`, increments `attempts` and sets the lease `locked_until = now + 5 min`.
3. **Catch-up, never lost.** Appwrite does not retry a failed scheduled execution. That is fine, because each tick creates runs for every period due within the job's catch-up window (at least one interval; default two days), not only the current minute. After an outage, the next tick creates and runs everything missed.
4. **Retries with backoff.** A handler exception is a failed attempt. The run returns to `queued` with `run_at = now + base × 2^(attempt−1)`. At `max_attempts` it becomes `failed`, and the alert hook fires (NFR-17).
5. **Crash recovery.** A run still `running` after its lease expires (worker crashed or timed out) counts as a failed attempt and is claimable again. `last_error` records "lease expired".
6. **Long jobs in chunks.** Handlers do one bounded chunk and return `Continue(cursor)`. The run is re-queued at once with the cursor in `payload`, so payout batches and tier evaluation never exceed the tick budget or the function timeout.
7. **Effects are idempotent too.** Each chunk gets an idempotency key (`dedupe_key#chunkN`). Side effects such as MoMo disbursements, emails and ledger postings use it, so a chunk that runs again after a crash cannot pay or post twice (NFR-8).
8. **Authentication.**
   - The function signs `"{timestamp}.{body}"` with HMAC-SHA256 using `JOB_TICK_SECRET`. The secret is held only by the function's variables and the core service's secrets (F09).
   - The core service rejects missing or wrong signatures and timestamps more than 5 minutes off.
   - User JWTs are never accepted on internal job endpoints.
9. **Manual runs.** Admin re-runs insert a run with `trigger=manual`, `actor_id` and `reason`, and are audit-logged (M06).
10. **One list of schedules.** All schedules are declared in one module in the core service. The Appwrite Function has no schedule knowledge beyond "every minute".

## Consequences

- **Positive:**
  - No Redis or worker fleet.
  - Jobs are plain, tested Python in the core service.
  - Every run is a row the admin can see.
  - Re-runs and catch-up are safe by construction.
- **Negative:**
  - One-minute granularity: matching offers can be up to about 60 s late. This is acceptable against NFR-3, but matching should also trigger directly from the request where possible.
  - Throughput is limited to one tick per minute, each with a 20 s budget. Revisit with option B if queues back up.
- **Cost:** Appwrite counts one function execution per minute, about 43,000 a month. Check that against the plan's execution allowance.
- **Follow-ups for F04 (#30):**
  - Add `outcome`, `finished_at` and `error` columns to `job_attempt`, so attempts can be read without `job_run`.
  - Query claimable runs with the existing `by_status_run_at` index (`status = queued AND run_at <= now`, ordered by `run_at`, limited) instead of scanning.
  - Confirm the function timeout and execution limits on the current Appwrite plan, and set the function timeout above the 25 s HTTP timeout.
  - Alert after N consecutive failed ticks, as well as on final run failure.

## Evidence

The demo is in [`spikes/jobs-demo/`](../../spikes/jobs-demo/). It contains:

- the runner, implementing the rules above against the existing `job_run` and `job_attempt` columns
- the signed FastAPI tick endpoint
- the `job-tick` Appwrite Function, written with the standard library only

It runs on a store with Appwrite's 409-on-duplicate-ID behaviour. All 11 tests pass (25 Nov 2026):

| Requirement | Test |
|---|---|
| Runs once per period despite repeated ticks | `test_scheduled_job_runs_once_per_period_even_if_ticked_repeatedly` |
| Missed hour caught up, nothing lost | `test_missed_ticks_are_caught_up_not_lost` |
| 8 concurrent ticks → each run applied exactly once | `test_concurrent_ticks_apply_each_run_exactly_once` |
| Retry with backoff → failed → alert, never re-run | `test_failures_retry_with_backoff_then_fail_and_alert` |
| Crashed worker: lease expires, reclaimed once | `test_crashed_worker_lease_expires_and_run_is_reclaimed_once` |
| Long job in chunks, every item once | `test_long_job_continues_in_chunks_without_redoing_work` |
| Idempotent enqueue, delayed and manual runs | `test_enqueue_is_idempotent_and_supports_delay_and_manual_runs` |
| Daily schedules in Kampala time | `test_daily_schedule_uses_kampala_time` |
| Signature, wrong secret, replay and tampering rejected | `test_rejects_unsigned_wrong_secret_and_stale_requests` |
| The function's requests are accepted by the endpoint | `test_appwrite_function_signs_requests_the_api_accepts` |

**Not yet done:** the ticket's "hello-world job running on staging". It needs:

- the core service deployed on Fly.io (F03 #29)
- the function deployed to an Appwrite project that is not holding live configuration (F02 #28)

Today the only Home School Appwrite project contains real settings and policies, so nothing was deployed. Run the staging demo when F02 and F03 are in place.

## Sign-off

- [ ] Owner approves this ADR (change Status to *Accepted*)
