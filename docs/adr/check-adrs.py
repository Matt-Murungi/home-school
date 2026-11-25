#!/usr/bin/env python3
"""ADR lint. Fails (exit 1) if any ADR file lacks the required structure.

Usage: python3 docs/adr/check-adrs.py [DIR_OR_FILE ...]   (default: the directory of this script)

Checks per file named NNNN-slug.md:
  * first line is "# ADR NNNN - title" and NNNN matches the file name
  * bold metadata fields: Status, Date, Ticket (e.g. "- **Status:** Proposed")
  * Status starts with Proposed, Accepted, Superseded or Deprecated
  * level-2 headings: Context, Decision, Consequences (a heading may continue,
    e.g. "## Decision drivers" does NOT count as "## Decision")
Standard library only; runs offline.
"""
import re
import sys
from pathlib import Path

FIELDS = ["Status", "Date", "Ticket"]
HEADINGS = ["Context", "Decision", "Consequences"]
STATUSES = ("Proposed", "Accepted", "Superseded", "Deprecated")
NAME = re.compile(r"^(\d{4})-[a-z0-9][a-z0-9-]*\.md$")


def check(path: Path) -> list[str]:
    m = NAME.match(path.name)
    if not m:
        return [f"file name must look like 0000-slug.md"]
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    errs = []
    first = lines[0] if lines else ""
    t = re.match(r"^# ADR (\d{4}) [-—–:] \S", first)
    if not t:
        errs.append('first line must be "# ADR NNNN — title"')
    elif t.group(1) != m.group(1):
        errs.append(f"title number {t.group(1)} does not match file name {m.group(1)}")
    for f in FIELDS:
        fm = re.search(rf"^\s*[-*]\s+\*\*{f}:\*\*\s*(\S.*)$", text, re.M)
        if not fm:
            errs.append(f"missing field **{f}:**")
        elif f == "Status" and not fm.group(1).startswith(STATUSES):
            errs.append(f"Status must start with one of {STATUSES}, got: {fm.group(1)[:40]}")
    in_fence = False
    h2 = set()
    for ln in lines:
        if ln.startswith("```"):
            in_fence = not in_fence
        elif not in_fence:
            hm = re.match(r"^## (\S+)\s*$", ln)  # exactly one word
            if hm:
                h2.add(hm.group(1))
    for h in HEADINGS:
        if h not in h2:
            errs.append(f"missing heading '## {h}'")
    return errs


def main(argv: list[str]) -> int:
    targets = [Path(a) for a in argv] or [Path(__file__).resolve().parent]
    files = []
    for t in targets:
        files += sorted(t.glob("*.md")) if t.is_dir() else [t]
    files = [f for f in files if f.name.lower() != "readme.md"]
    if not files:
        print("no ADR files found")
        return 1
    bad = 0
    for f in files:
        errs = check(f)
        print(("FAIL " if errs else "ok   ") + str(f))
        for e in errs:
            print(f"     - {e}")
        bad += bool(errs)
    print(f"{len(files) - bad}/{len(files)} ADRs pass")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
