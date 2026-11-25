"""Scheduled jobs (ADR 0002): an Appwrite Function ticks, the core service runs the jobs.

- `schedule.py`   the one list of scheduled jobs and their schedules
- `runner.py`     materialise due periods, claim, run, retry, recover, alert
- `api.py`        POST /internal/jobs/tick (HMAC-signed, never a user JWT)
- `idempotency.py`, `alerts.py`, `signing.py`, `model.py`
"""
