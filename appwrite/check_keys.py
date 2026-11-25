#!/usr/bin/env python3
"""Validate the API-key scope manifest `appwrite/keys.md` (F02, #28). Stdlib only.

Rules: known consumer and environment; known scopes only, no wildcard; each consumer's scopes are a
subset of its allowlist (fewest needed; deploy scopes never on the core service, user management never
on CI); expiry set; "Stored in" names a secrets store; no key value in the file; one row per
consumer and environment. usage: check_keys.py [path]   exit 1 on any error. Prints errors, never values.
"""

import re
import sys
from pathlib import Path

ENVIRONMENTS = {"development", "staging", "production"}
KNOWN_SCOPES = {
    "databases.read", "databases.write", "tables.read", "tables.write", "columns.read", "columns.write",
    "indexes.read", "indexes.write", "rows.read", "rows.write", "users.read", "users.write",
    "buckets.read", "buckets.write", "files.read", "files.write", "functions.read", "functions.write",
    "execution.read", "execution.write", "sessions.write", "teams.read", "teams.write",
    "messages.read", "messages.write", "targets.read", "targets.write", "health.read",
}  # fmt: skip
# Allowlist per consumer: the most a key for this consumer may ever hold.
ALLOWED = {
    # Runtime: data, labels (users), files, function execution. Never schema or function deployment.
    "core-service": {
        "databases.read", "tables.read", "rows.read", "rows.write", "users.read", "users.write",
        "files.read", "files.write", "buckets.read", "execution.read", "execution.write", "health.read",
    },
    # CI deploy: schema, buckets, functions and the migration/seed rows. Never users, sessions or messages.
    "ci-deploy": {
        "databases.read", "databases.write", "tables.read", "tables.write", "columns.read", "columns.write",
        "indexes.read", "indexes.write", "rows.read", "rows.write", "buckets.read", "buckets.write",
        "functions.read", "functions.write", "health.read",
    },
}  # fmt: skip
SECRET_STORES = re.compile(r"(?i)(fly secret|github (environment )?secret|secrets? manager)")
KEY_VALUE = re.compile(r"standard_[0-9a-f]{20,}|\b[A-Za-z0-9_-]{40,}\b")
EXPIRY = re.compile(r"^\d+\s*(d|days|w|weeks|m|months)$", re.I)
COLUMNS = ["key", "consumer", "environment", "scopes", "expiry", "stored in"]


def parse(text):
    rows, header = [], None
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if header is None:
            if [c.lower() for c in cells] == COLUMNS:
                header = cells
            continue
        if set("".join(cells)) <= set("-: "):
            continue
        rows.append(dict(zip(COLUMNS, cells, strict=False)) | {"_raw": line})
    return rows


def validate(text):
    errors = []
    if KEY_VALUE.search(text):
        errors.append("manifest contains something that looks like a secret value (names only, never values)")
    rows = parse(text)
    if not rows:
        errors.append("no key table found (columns: Key | Consumer | Environment | Scopes | Expiry | Stored in)")
    seen = set()
    for r in rows:
        who = f"{r['key']}"
        consumer, env = r["consumer"], r["environment"]
        if consumer not in ALLOWED:
            errors.append(f"{who}: unknown consumer {consumer!r} (expected {', '.join(sorted(ALLOWED))})")
        if env not in ENVIRONMENTS:
            errors.append(f"{who}: unknown environment {env!r}")
        if (consumer, env) in seen:
            errors.append(f"{who}: duplicate row for {consumer} in {env}")
        seen.add((consumer, env))
        scopes = [s.strip() for s in r["scopes"].split(",") if s.strip()]
        if not scopes:
            errors.append(f"{who}: no scopes listed")
        for s in scopes:
            if "*" in s:
                errors.append(f"{who}: wildcard scope {s!r} not allowed")
            elif s not in KNOWN_SCOPES:
                errors.append(f"{who}: unknown scope {s!r}")
            elif consumer in ALLOWED and s not in ALLOWED[consumer]:
                errors.append(f"{who}: scope {s} is not allowed for {consumer} (fewest scopes needed)")
        if not EXPIRY.match(r["expiry"]):
            errors.append(f"{who}: expiry must be set, e.g. 90d (got {r['expiry']!r})")
        if not SECRET_STORES.search(r["stored in"]):
            errors.append(f"{who}: stored in must name a secrets store (Fly secret, GitHub environment secret, secrets manager)")
    return errors


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    path = Path(argv[0]) if argv else Path(__file__).with_name("keys.md")
    errors = validate(path.read_text())
    for e in errors:
        print(f"keys: {e}")
    if not errors:
        print(f"keys: {path.name} ok")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
