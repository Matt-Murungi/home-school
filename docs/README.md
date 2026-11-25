# docs

## Purpose

Architecture decision records (`docs/adr`) and a placeholder for product docs (`docs/product`).

## Test command

`python3 docs/adr/check-adrs.py` (run from the repo root), or `make test` from the repo root, which runs every package listed in `packages.json`.

## Status

Implemented: the ADR lint runs in `make test` and `make lint`.
