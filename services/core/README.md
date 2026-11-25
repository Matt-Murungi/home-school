# services/core

## Purpose

Core service (FastAPI on Fly.io, ADR 0003): payments, ledger, matching, job runner (ADR 0002). Talks to Appwrite and providers only through adapters with fakes for tests.

## Test command

`uv run pytest -q` (run from this directory), or `make test` from the repo root, which runs every package listed in `packages.json`.

## Status

Not scaffolded yet. Skipped by `make test` until F03 flips it in `packages.json`.
