#!/usr/bin/env python3
"""Path filter for CI (F07): which jobs must run for a change set. Stdlib only.

usage: ci_changes.py [--base SHA] [--head SHA] [--all] [--files-from FILE]
Prints `name=value` lines and appends them to $GITHUB_OUTPUT. Outputs are `true`/`false` per job
group, plus `flutter`, a JSON list of {id, path} for the Flutter packages to build.

Rules (each has a test in scripts/tests/test_ci_changes.py):
- a file under a group's prefixes turns that group on; nothing else builds;
- a change to a shared path (packages.json, Makefile, .github/, scripts/ci_*.py ...) turns everything on;
- the shared Flutter package turns on both apps; an unknown or unresolvable base turns everything on
  (fail safe: build too much rather than too little).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# group -> path prefixes that trigger it. Keep in sync with packages.json (a test enforces coverage).
GROUPS: dict[str, tuple[str, ...]] = {
    "core": (
        "services/core/",
        "appwrite/functions/",
    ),  # job-tick tests run in the core suite
    "appwrite": ("appwrite/",),
    "ops": ("ops/",),
    "scripts": ("scripts/", "docs/ops/", ".pre-commit-config.yaml", ".gitleaks.toml"),
    "admin": ("apps/admin/",),
    "docs": ("docs/",),
}
FLUTTER: dict[str, dict] = {
    "app-parent": {
        "path": "apps/parent",
        "triggers": ("apps/parent/", "packages/flutter_shared/"),
    },
    "app-teacher": {
        "path": "apps/teacher",
        "triggers": ("apps/teacher/", "packages/flutter_shared/"),
    },
    "flutter-shared": {
        "path": "packages/flutter_shared",
        "triggers": ("packages/flutter_shared/",),
    },
}
# Changing any of these can change how every job behaves: run everything.
SHARED = (
    "packages.json",
    "Makefile",
    ".github/",
    "scripts/ci_changes.py",
    "scripts/ci_lint.py",
    "scripts/ci_yaml.py",
    "scripts/run_packages.py",
    "scripts/check_layout.py",
    "scripts/secret_scan.py",
)


def _hit(files, prefixes) -> bool:
    return any(f == p.rstrip("/") or f.startswith(p) for f in files for p in prefixes)


def compute(files: list[str] | None) -> dict[str, str]:
    """files=None means 'unknown change set': everything runs."""
    everything = files is None or _hit(files, SHARED)
    out = {
        g: "true" if everything or _hit(files, p) else "false"
        for g, p in GROUPS.items()
    }
    flutter = [
        {"id": i, "path": spec["path"]}
        for i, spec in FLUTTER.items()
        if everything or _hit(files, spec["triggers"])
    ]
    out["flutter"] = json.dumps(flutter, separators=(",", ":"))
    return out


def git_changed(base: str, head: str) -> list[str] | None:
    if not base or set(base) == {"0"}:
        return None  # new branch / first push: no base to diff against
    try:
        res = subprocess.run(
            ["git", "diff", "--name-only", base, head],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None  # base commit not fetched: fail safe
    return [ln for ln in res.stdout.splitlines() if ln]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--base", default="")
    ap.add_argument("--head", default="HEAD")
    ap.add_argument("--all", action="store_true")
    ap.add_argument(
        "--files-from", help="file with one changed path per line (tests, local use)"
    )
    a = ap.parse_args(argv)
    if a.all:
        files = None
    elif a.files_from:
        files = [
            ln.strip()
            for ln in Path(a.files_from).read_text().splitlines()
            if ln.strip()
        ]
    else:
        files = git_changed(a.base, a.head)
    result = compute(files)
    lines = [f"{k}={v}" for k, v in result.items()]
    print("\n".join(lines))
    gh = os.environ.get("GITHUB_OUTPUT")
    if gh:
        with open(gh, "a", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
