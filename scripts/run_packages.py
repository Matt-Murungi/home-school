#!/usr/bin/env python3
"""Run every package's `test` or `lint` command from packages.json.

usage: run_packages.py test|lint [--manifest FILE]
env:   HS_SKIP=id,id   skip these package ids
       HS_STRICT=1     a missing tool is a failure (use in CI) instead of a SKIP
Planned (not yet scaffolded) packages and packages whose tools are missing are
skipped with an explicit message; they are never reported as passing.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main(argv):
    if not argv or argv[0] not in ("test", "lint"):
        print(__doc__)
        return 2
    kind = argv[0]
    manifest = ROOT / "packages.json"
    if "--manifest" in argv:
        manifest = Path(argv[argv.index("--manifest") + 1])
    base = manifest.parent if "--manifest" not in argv else ROOT
    skip = {s for s in os.environ.get("HS_SKIP", "").split(",") if s}
    strict = os.environ.get("HS_STRICT") == "1"
    pkgs = json.loads(manifest.read_text())["packages"]
    ran, skipped, failed = [], [], []
    for p in pkgs:
        name = p["path"]
        cmd = p.get(kind)
        if p["id"] in skip:
            print(f"SKIP {name}: skipped via HS_SKIP")
            skipped.append(name)
            continue
        if p["status"] != "implemented":
            print(
                f"SKIP {name}: not implemented yet (planned for {p.get('ticket', '?')})"
            )
            skipped.append(name)
            continue
        if not cmd:
            print(f"SKIP {name}: no {kind} command defined")
            skipped.append(name)
            continue
        missing = [t for t in p.get("requires", []) if shutil.which(t) is None]
        if missing:
            print(
                f"SKIP {name}: tool missing: {', '.join(missing)} (install it to run {kind} here)"
            )
            skipped.append(name)
            if strict:
                failed.append(name)
            continue
        print(f"==> {kind} {name}: {cmd}", flush=True)
        cwd = (base / name) if (base / name).is_dir() else ROOT / name
        r = subprocess.run(cmd, shell=True, cwd=cwd, check=False)
        (ran if r.returncode == 0 else failed).append(name)
        if r.returncode != 0:
            print(f"FAIL {name} (exit {r.returncode})")
    print(f"\n{kind}: {len(ran)} ran ok, {len(skipped)} skipped, {len(failed)} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
