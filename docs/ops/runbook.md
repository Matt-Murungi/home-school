# Monitoring runbook (F08, NFR-17)

Rules live in `ops/alerts.json` and `ops/uptime.json`; `python3 ops/check_ops.py` fails if any alert lacks a section below. Contact destinations are configured in the vendor accounts, never in the repo (see `setup.md`).

## Severity and noise policy

- `page`: wakes a human now (founder push). Kept to six or fewer so a page always means act.
- `ticket`: email to ops; handled in working hours.
- Every alert has a threshold and a throttle (at least 15 minutes, most 30 to 120) so a flapping condition produces one message, not fifty. Uptime needs 2 consecutive failures. Sentry rules group by issue fingerprint.
- Review the alert log monthly; delete or retune any alert that fired without needing action twice.

## Tag convention (how code raises an alert)

Code reports through `ErrorReporter` (core service) or the Sentry SDK (apps) with tag `alert=<kind>` (`failed-payment`, `failed-payout`, `job-failure`, `safeguarding-unacked`) and logs a JSON line with `"alert": "<kind>"`. Sentry issue-alert rules match the tag. Never put names, phone numbers or emails in the message: use ids (the scrubber is a safety net, not permission).

## PII rules (what Sentry may receive)

Dropped: request bodies, cookies, `Authorization`, frame variables, user email/name/IP. Masked everywhere: keys containing phone, email, name fields (child, parent, guardian, teacher), address, token, password, session; free-text phone numbers (incl. `00256...`, `(0772) 123456`), emails, JWTs, bearer tokens and secret-looking `name=value`, `name: value`, `name%3Dvalue` and `"name": "value"` pairs (token, secret, password, passwd, pwd, pass, apikey, `*_key`, jwt, session, cookie, authorization, signature). Counters and settings are deliberately NOT masked: `session_count=3`, `tokens_used=5`, `password_policy=ok` (last part `count/used/policy/length/...`, or a plural). Only the last 64 characters of a name are judged and a value runs to the next whitespace or delimiter. Matching is linear in the input size (no keyword alternation in a regex); a scrubber drop is counted and logged once as `event dropped by scrubber: <ExceptionClass>`. Secret-name matching needs `name`, separator, value: a bare `the token abc` is not detectable. Kept: request id, environment, release, route, status, user id. Names and addresses inside free text cannot be detected, so never log them. Implementation: `services/core/src/hs_core/observability/scrub.py`; scrubbing failure drops the event.

## Alerts

### failed-payments
Collection failed or stuck pending. Who: ops admin. Check the payment in the admin dashboard, the provider status page and the callback logs (search by `request_id`). Retry or contact the parent. Escalate to founder if more than 3 distinct payments fail (provider outage).

### failed-payouts
A teacher payout failed. Who: ops admin, escalate to founder. Money is owed: do not retry blindly. Check the ledger entry state, confirm with the provider whether funds moved, then retry once through the admin tool (idempotent) or pay manually and record it. Tell the teacher.

### job-failures
A scheduled job raised or did not complete. Who: engineer on call. Open the Sentry issue, find the job name tag and `request_id`/run id, read the structured logs. Jobs are idempotent: re-run from the admin job panel after fixing. If the tick lock is stuck, see F04 notes.

### unacknowledged-safeguarding
A safeguarding case passed its acknowledgement SLA. Who: safeguarding lead, then backup, then founder. Open the case, acknowledge it, follow the safeguarding policy. This alert is never silenced or throttled below 30 minutes; if it fires, a human must act.

### uptime-down
`/health` or admin failing twice in a row. Who: engineer on call. Check Fly.io status and recent deploys; roll back the last release if it started right after a deploy; check `/ready` (a 503 there means Appwrite is unreachable, not our process). Post status to the founder.

### api-5xx-rate
More than 5% server errors over 5 minutes (at least 20 requests). Who: engineer on call. Group by path in the logs, open the top Sentry issue, roll back if recent deploy, check Appwrite status.

### monitoring-heartbeat-missing
The job tick stopped pinging the heartbeat, or the monitoring pipeline is broken. Who: engineer on call. This is the "who watches the watcher" alert: first check whether the core service and job tick are running; then check that the uptime vendor and Sentry are reachable and quota is not exhausted. Because a single vendor outage would silence everything, keep a second independent free check (UptimeRobot or Better Stack, whichever is not primary) on production `/health`, and do the monthly test below.

## Monthly monitoring test (checks monitoring itself)

1. Staging: raise a deliberate exception (`/v1/...` debug route or script) and confirm it appears in Sentry without personal data.
2. Stop the staging API; confirm the uptime alert reaches the founder within 3 minutes; restart.
3. Confirm the heartbeat is green and the alert recipients are still correct.
