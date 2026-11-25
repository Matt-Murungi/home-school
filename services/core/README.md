# services/core

## Purpose

Core service (FastAPI on Fly.io, ADR 0003): payments, ledger, matching, job runner (ADR 0002). Talks to Appwrite only through the repository layer (`hs_core.repository`), with an in-memory fake for tests.

## Run

```
cd services/core
cp .env.example .env            # dev needs no values: falls back to the in-memory repository
uv run uvicorn --factory hs_core.main:app_factory --reload
```

Endpoints: `GET /health` (also `/healthz`, liveness: version + git SHA), `GET /ready` (also `/readyz`, readiness: repository ping, 503 when Appwrite is unreachable; the process keeps running and the next probe retries), `GET /v1/me` (any valid Appwrite JWT), `GET /v1/admin/ping` (label `opsadmin`). Callers send `Authorization: Bearer <Appwrite JWT>`. Errors always look like `{"error": {"code", "message", "request_id"}}`. `X-Request-ID` is accepted (`[A-Za-z0-9._-]{1,64}`) or generated, returned, and put on every JSON log line.

## Configuration (environment variables only)

`HS_ENV` = `dev` (default) | `staging` | `production`. In staging and production the service refuses to start unless `APPWRITE_ENDPOINT` (https, not localhost), `APPWRITE_PROJECT_ID`, `APPWRITE_API_KEY` (not a placeholder, at least 32 chars) and `APPWRITE_DATABASE_ID` are set, and `HS_DEBUG` is off in production. Errors name the variable, never the value. Also required in staging/production: `JOB_TICK_SECRET` (at least 32 chars, shared with the `job-tick` function; never logged). Optional: `JOB_ALERT_AFTER_FAILURES` (default 3), `APPWRITE_CONNECT_TIMEOUT_S` (3.05), `APPWRITE_READ_TIMEOUT_S` (10), `LOG_LEVEL`, `APP_VERSION`, `GIT_SHA`. Every Appwrite SDK call carries these timeouts (`install_request_timeout`). Outside dev the service refuses to fall back to the in-memory repository. Error reporting (F08): `SENTRY_DSN` (https; empty disables reporting; never set in tests). Events are scrubbed of personal data and tagged with `request_id`, environment and release; see `docs/ops/`. See `.env.example` (empty values only; never commit a real `.env`).

## Test command

`uv run pytest -q` and `uv run ruff check . && uv run ruff format --check .` (from this directory), or `make test` / `make lint` from the repo root. Tests are offline: fake repository, fake token verifier, mocked Appwrite SDK.

## Docker

`docker build -t hs-core --build-arg GIT_SHA=$(git rev-parse --short HEAD) .` from this directory. Multi-stage, non-root user, healthcheck on `/health`, no secrets baked in (inject `APPWRITE_*` at runtime). The Dockerfile is checked by `tests/test_dockerfile.py`; the image build itself has not been run on a machine with Docker yet.

## Scheduled jobs (F04, ADR 0002)

`src/hs_core/jobs/`: `schedule.py` is the **one** list of scheduled jobs and their schedules (Kampala-anchored); `runner.py` materialises due periods from a persisted watermark, claims runs by creating `job_attempt` rows with deterministic IDs, runs handlers with a timeout, retries with capped backoff, recovers from crashes and stalled claimers, and alerts (`alerts.py`: `AlertSink`, `LogAlertSink` default, `FakeAlertSink`; the real Sentry/email sink is wired later). `POST /internal/jobs/tick` (`api.py`, `signing.py`) accepts only an HMAC-signed request (`X-Job-Timestamp`, `X-Job-Signature` = HMAC-SHA256 of `"{timestamp}.{body}"` with `JOB_TICK_SECRET`, 5 minute skew, constant-time compare); a user JWT is never read. The sample job `heartbeat` (every 5 minutes) logs one line. To add a job: write the handler (idempotent by `ctx.effect_key`), add one `JobSpec` to `schedule.py`. The Appwrite Function that ticks is in `appwrite/functions/job-tick/`.

## Not done here

Fly.io app and deployment (owner-blocked, ADR 0003); seeing `heartbeat` run on staging (owner-blocked: needs F02 projects, Fly.io, the deployed function).
