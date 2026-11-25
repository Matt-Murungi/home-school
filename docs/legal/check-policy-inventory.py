#!/usr/bin/env python3
"""Fail (exit 1) if a policy-inventory row lacks purpose/consumer ticket/owner/status,
or references a ticket number not in the embedded backlog list.

Usage: python3 docs/legal/check-policy-inventory.py [inventory.md]
"""
import re
import sys
from pathlib import Path

# From `gh issue list -R Matt-Murungi/home-school -s all` on 25 Nov 2026: issues #1-#155
# (epics #1-#20, tickets #21-#155).
KNOWN_TICKETS = set(range(1, 156))
VALID_STATUS = {"draft needed", "draft in review", "approved"}


def check(text):
    m = re.search(r"<!-- INVENTORY:START -->(.*?)<!-- INVENTORY:END -->", text, re.S)
    if not m:
        return ["inventory markers not found"]
    rows = [l for l in m.group(1).splitlines() if l.strip().startswith("|")]
    if len(rows) < 3:
        return ["inventory table has no data rows"]
    errors = []
    for line in rows[2:]:
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) != 5:
            errors.append(f"row has {len(cells)} cells, expected 5: {line[:60]}")
            continue
        name, purpose, tickets, owner, status = cells
        for label, val in (("policy", name), ("purpose", purpose), ("consumer ticket", tickets), ("owner", owner), ("status", status)):
            if not val:
                errors.append(f"{name or '?'}: missing {label}")
        nums = [int(n) for n in re.findall(r"#(\d+)", tickets)]
        if tickets and not nums:
            errors.append(f"{name}: consumer ticket has no #NN reference")
        for n in nums:
            if n not in KNOWN_TICKETS:
                errors.append(f"{name}: ticket #{n} does not exist")
        if status and status.lower() not in VALID_STATUS:
            errors.append(f"{name}: status '{status}' not in {sorted(VALID_STATUS)}")
    return errors


def main():
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).with_name("policy-inventory.md")
    errors = check(path.read_text(encoding="utf-8"))
    for e in errors:
        print("FAIL:", e)
    if errors:
        return 1
    print(f"ok: {path.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
