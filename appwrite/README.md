# appwrite

## Purpose

Appwrite project definition: `appwrite.config.json` (tables, buckets, functions), migrations and seed data. Build from the tickets and System Requirements, never from the live project (read-only, ADR 0012).

## Test command

`npm test` (run from this directory), or `make test` from the repo root, which runs every package listed in `packages.json`.

## Status

Not scaffolded yet. Skipped by `make test` until F02 flips it in `packages.json`. Nothing here is ever applied to the live project by an agent.
