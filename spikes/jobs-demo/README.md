# Job runner demo (D02 / ADR 0002)

This is a throwaway demo of the job design in [ADR 0002](../../docs/adr/0002-job-runner.md). An Appwrite Function ticks every minute, and the core service runs the due jobs from the `job_run` and `job_attempt` tables. The real version is built in F04 (#30) inside `services/core`.

- `src/jobs_demo/runner.py` creates runs per scheduled period, claims them atomically, retries with backoff, recovers crashed runs when their lease expires (including a crash between the two claim writes), and continues long jobs in chunks.
- `src/jobs_demo/store.py` is the storage interface, plus an in-memory store with Appwrite's "409 on duplicate row ID" behaviour.
- `src/jobs_demo/api.py` is the signed `POST /internal/jobs/tick` endpoint (FastAPI).
- `appwrite-function/src/main.py` is the `job-tick` function (runtime python-3.12, schedule `* * * * *`). It uses the standard library only.

## Run the tests

```sh
uv run pytest -q
```

Nothing here talks to a live Appwrite project.
