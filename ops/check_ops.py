#!/usr/bin/env python3
"""Validate ops/alerts.json and ops/uptime.json (stdlib only, offline).

usage: check_ops.py   (exit 1 with a list of problems)
"""

import json
import re
import sys
from pathlib import Path

REQUIRED_ALERTS = (
    "failed-payments",
    "failed-payouts",
    "job-failures",
    "unacknowledged-safeguarding",
    "uptime-down",
    "api-5xx-rate",
    "monitoring-heartbeat-missing",
)
ALERT_FIELDS = (
    "id",
    "nfr",
    "title",
    "source",
    "condition",
    "threshold",
    "severity",
    "routes",
    "throttle_minutes",
    "owner_role",
)
CHECK_FIELDS = (
    "id",
    "service",
    "environment",
    "base_url_var",
    "path",
    "interval_seconds",
    "expected_status",
    "confirm_after_failures",
)
MAX_INTERVAL = {"/health": 60, "/ready": 300}


def check_alerts(data: dict, runbook: str) -> list[str]:
    errs: list[str] = []
    alerts = data.get("alerts", [])
    ids = [a.get("id") for a in alerts]
    for need in REQUIRED_ALERTS:
        if need not in ids:
            errs.append(f"alerts.json: required alert '{need}' is missing")
    if len(ids) != len(set(ids)):
        errs.append("alerts.json: duplicate alert ids")
    routes = data.get("routes", {})
    for a in alerts:
        for f in ALERT_FIELDS:
            if f not in a:
                errs.append(f"alerts.json: {a.get('id')} lacks '{f}'")
        for r in a.get("routes", []):
            if r not in routes:
                errs.append(f"alerts.json: {a.get('id')} uses unknown route '{r}'")
        if a.get("throttle_minutes", 0) < 15:
            errs.append(f"alerts.json: {a.get('id')} throttle_minutes must be >= 15")
        if not re.search(rf"(?m)^### {re.escape(str(a.get('id')))}\b", runbook):
            errs.append(f"runbook: no '### {a.get('id')}' section")
    return errs


def check_uptime(data: dict) -> list[str]:
    errs: list[str] = []
    checks = data.get("checks", [])
    for c in checks:
        for f in CHECK_FIELDS:
            if f not in c:
                errs.append(f"uptime.json: {c.get('id')} lacks '{f}'")
        lim = MAX_INTERVAL.get(c.get("path"))
        if lim and c.get("interval_seconds", 10**9) > lim:
            errs.append(f"uptime.json: {c.get('id')} interval must be <= {lim}s")
        if c.get("path") in MAX_INTERVAL and c.get("expected_status") != [200]:
            errs.append(f"uptime.json: {c.get('id')} must expect exactly [200]")
        if c.get("confirm_after_failures", 0) < 2:
            errs.append(f"uptime.json: {c.get('id')} needs confirm_after_failures >= 2")
        if "http" in json.dumps(c):
            errs.append(f"uptime.json: {c.get('id')} must use base_url_var, not a literal URL")
    have = {(c.get("environment"), c.get("service"), c.get("path")) for c in checks}
    for env in ("staging", "production"):
        for svc, path in (("api", "/health"), ("api", "/ready")):
            if (env, svc, path) not in have:
                errs.append(f"uptime.json: missing {env} {svc} {path}")
        if not any(k[0] == env and k[1] == "admin" for k in have):
            errs.append(f"uptime.json: missing {env} admin check")
    return errs


def check(ops: Path, root: Path) -> list[str]:
    errs: list[str] = []
    try:
        alerts = json.loads((ops / "alerts.json").read_text())
        uptime = json.loads((ops / "uptime.json").read_text())
        runbook = (root / "docs/ops/runbook.md").read_text()
    except (OSError, ValueError) as e:
        return [f"cannot read ops files: {e}"]
    errs += check_alerts(alerts, runbook)
    errs += check_uptime(uptime)
    return errs


def main() -> int:
    ops = Path(__file__).resolve().parent
    errs = check(ops, ops.parent)
    for e in errs:
        print(e)
    print("ops: ok" if not errs else f"ops: {len(errs)} problem(s)")
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main())
