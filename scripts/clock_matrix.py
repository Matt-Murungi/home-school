#!/usr/bin/env python3
"""Run test suites under several frozen wall-clock times and report clock-dependent tests.

A test that fails at some clocks but passes at others reads the wall clock (the bug that broke main
at night: N01 quiet hours deferred a push because the test used the real time). Tests must use an
injected clock, never the real one.

usage: clock_matrix.py [--targets core,py-momo,...] [--clocks all|quick|LABEL,LABEL] [--list]
                       [--select 'tests/test_notify*.py,...'] [--tick] [--timeout SEC]
  --select    pytest targets only: restrict to these paths/globs (relative to the package)
  --targets   default: core,py-momo,appwrite,legal-docs,ops (add `scripts`: it nests `make test`, slow)
  --clocks    `all` (default), `quick` (a night time and a month end), or labels from --list
  --tick      let the frozen clock advance (default: it stands still)
Exit 1 when a test fails at some clock but not at others (or a target fails everywhere).
Needs `uv` (time-machine comes from the core dev group or `uv run --with time-machine`).
The freezing is scripts/clock_freeze/sitecustomize.py (PYTHONPATH + HS_FROZEN_CLOCK).
No network is used by the tests; `uv` may resolve `time-machine` from its cache.
"""

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FREEZE_DIR = ROOT / "scripts" / "clock_freeze"

# label -> ISO instant with a Kampala (+03:00, no DST) offset. UTC in the comment where it matters.
CLOCKS = {
    "wed-03:00": "2027-03-10T03:00:00+03:00",
    "wed-09:00": "2027-03-10T09:00:00+03:00",
    "wed-12:00": "2027-03-10T12:00:00+03:00",
    "wed-20:59": "2027-03-10T20:59:00+03:00",
    "wed-21:00": "2027-03-10T21:00:00+03:00",
    "wed-23:30": "2027-03-10T23:30:00+03:00",
    "sun-12:00": "2027-03-14T12:00:00+03:00",
    "sun-23:30": "2027-03-14T23:30:00+03:00",
    "month-end-23:30": "2027-03-31T23:30:00+03:00",
    "month-start-00:30": "2027-04-01T00:30:00+03:00",  # 21:30Z on Mar 31: UTC date differs
    "new-year-eve-23:30": "2026-12-31T23:30:00+03:00",
    "new-year-00:30": "2027-01-01T00:30:00+03:00",  # 21:30Z on Dec 31: UTC date differs
    "feb28-23:30": "2027-02-28T23:30:00+03:00",
    "mar1-00:30": "2027-03-01T00:30:00+03:00",  # 21:30Z on Feb 28
    "leap-feb29-12:00": "2028-02-29T12:00:00+03:00",
    "leap-mar1-00:30": "2028-03-01T00:30:00+03:00",  # 21:30Z on Feb 29
}
QUICK = ["wed-23:30", "month-end-23:30"]

PYTEST = ["pytest", "-q", "--tb=no", "-rfE", "-p", "no:cacheprovider"]
UNITTEST = ["python", "-m", "unittest", "discover", "-s", "tests"]
# id -> (cwd relative to repo root, command argv, regex for a failing test id)
TARGETS = {
    "core": ("services/core", ["uv", "run", *PYTEST], "pytest"),
    "py-momo": (
        "packages/py_momo",
        ["uv", "run", "--with", "time-machine", *PYTEST],
        "pytest",
    ),
    "appwrite": (
        "appwrite",
        ["uv", "run", "--no-project", "--with", "time-machine", *UNITTEST],
        "unittest",
    ),
    "legal-docs": (
        "docs/legal",
        ["uv", "run", "--no-project", "--with", "time-machine", *UNITTEST],
        "unittest",
    ),
    "ops": (
        "ops",
        ["uv", "run", "--no-project", "--with", "time-machine", *UNITTEST],
        "unittest",
    ),
    "scripts": (
        "scripts",
        ["uv", "run", "--no-project", "--with", "time-machine", *UNITTEST],
        "unittest",
    ),
}
DEFAULT_TARGETS = ["core", "py-momo", "appwrite", "legal-docs", "ops"]

_PYTEST_FAIL = re.compile(r"^(?:FAILED|ERROR) (\S+)", re.MULTILINE)
_UNITTEST_FAIL = re.compile(r"^(?:FAIL|ERROR): (\S+ \([^)]*\))", re.MULTILINE)


def parse_failures(kind: str, output: str) -> set[str]:
    rx = _PYTEST_FAIL if kind == "pytest" else _UNITTEST_FAIL
    return set(rx.findall(output))


def classify(
    results: dict[str, set[str] | None],
) -> tuple[dict[str, list[str]], list[str]]:
    """results: clock label -> failing test ids (None = the run itself broke: timeout, no tests).
    Returns ({test: [clocks it fails at]} for tests that fail at SOME clocks only,
    [tests failing at every clock])."""
    ran = {c: f for c, f in results.items() if f is not None}
    broke = [c for c, f in results.items() if f is None]
    every = set.intersection(*ran.values()) if ran else set()
    flaky: dict[str, list[str]] = {}
    for clock, fails in ran.items():
        for t in fails - every:
            flaky.setdefault(t, []).append(clock)
    for clocks in flaky.values():
        clocks.sort()
    if broke:
        flaky["<run broke: timeout or crash>"] = sorted(broke)
    return flaky, sorted(every)


def run_one(
    target: str,
    label: str,
    iso: str,
    tick: bool,
    timeout: int,
    select: list[str] | None = None,
) -> set[str] | None:
    cwd, cmd, kind = TARGETS[target]
    if select and kind == "pytest":
        found = sorted(
            str(f.relative_to(ROOT / cwd)) for g in select for f in (ROOT / cwd).glob(g)
        )
        if not found:
            raise SystemExit(f"--select {select} matches nothing in {cwd}")
        cmd = [*cmd, *found]
    env = dict(os.environ)
    env["HS_FROZEN_CLOCK"] = iso
    env["HS_FROZEN_TICK"] = "1" if tick else "0"
    env["PYTHONPATH"] = os.pathsep.join(
        filter(None, [str(FREEZE_DIR), env.get("PYTHONPATH")])
    )
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    try:
        p = subprocess.run(
            cmd,
            check=False,
            cwd=ROOT / cwd,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return None
    out = p.stdout + p.stderr
    fails = parse_failures(kind, out)
    if p.returncode != 0 and not fails:
        sys.stderr.write(
            f"--- {target} @ {label}: exit {p.returncode}, no test ids parsed ---\n"
        )
        sys.stderr.write(out[-2000:] + "\n")
        return None
    return fails


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    ap.add_argument("--targets", default=",".join(DEFAULT_TARGETS))
    ap.add_argument("--clocks", default="all")
    ap.add_argument("--tick", action="store_true")
    ap.add_argument("--timeout", type=int, default=900)
    ap.add_argument("--select", default="")
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args(argv)
    if a.list:
        for k, v in CLOCKS.items():
            print(f"{k:20} {v}")
        print("targets:", ", ".join(TARGETS))
        return 0
    labels = (
        list(CLOCKS)
        if a.clocks == "all"
        else QUICK
        if a.clocks == "quick"
        else a.clocks.split(",")
    )
    unknown = [x for x in labels if x not in CLOCKS] + [
        t for t in a.targets.split(",") if t not in TARGETS
    ]
    if unknown:
        print("unknown:", unknown)
        return 2
    bad = False
    select = [x for x in a.select.split(",") if x]
    for target in a.targets.split(","):
        results = {}
        for label in labels:
            results[label] = run_one(
                target, label, CLOCKS[label], a.tick, a.timeout, select
            )
            n = results[label]
            print(
                f"[{target}] {label}: "
                + ("RUN BROKE" if n is None else f"{len(n)} failing"),
                flush=True,
            )
        flaky, every = classify(results)
        for t in every:
            print(f"[{target}] FAILS AT EVERY CLOCK (not clock dependent): {t}")
        for t, clocks in sorted(flaky.items()):
            print(f"[{target}] CLOCK DEPENDENT {t}: fails at {', '.join(clocks)}")
        bad = bad or bool(flaky) or bool(every)
        if not flaky and not every:
            print(f"[{target}] OK: same result at all {len(labels)} clocks")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
