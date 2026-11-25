# ops

## Purpose

Monitoring as code (F08, NFR-17): `alerts.json` (alert rules), `uptime.json` (uptime checks per environment), `check_ops.py` (validator). No secrets and no contact details live here; see `docs/ops/` for the runbook, owner setup steps and app integration notes.

## Test command

`python3 -m unittest discover -s tests` and `python3 check_ops.py` (from this directory), or `make test` / `make lint` from the repo root.
