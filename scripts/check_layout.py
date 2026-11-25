#!/usr/bin/env python3
"""Fail if the repo layout is violated (clear message for agents).

- top-level directories must be one of ALLOWED_TOP (hidden dirs ignored)
- every directory under apps/, packages/, services/ must be registered in packages.json
- every registered package directory must have a README.md
usage: check_layout.py [--root DIR]
"""

import json
import sys
from pathlib import Path

ALLOWED_TOP = {"apps", "packages", "services", "appwrite", "docs", "scripts", "spikes"}
GROUPS = ("apps", "packages", "services")
IGNORE = {"node_modules", "__pycache__"}


def check(root: Path):
    errors = []
    manifest = json.loads((root / "packages.json").read_text())
    registered = {p["path"] for p in manifest["packages"]}
    for d in sorted(root.iterdir()):
        if (
            d.is_dir()
            and not d.name.startswith(".")
            and d.name not in IGNORE
            and d.name not in ALLOWED_TOP
        ):
            errors.append(
                f"{d.name}/ is outside the layout. Allowed top-level dirs: {', '.join(sorted(ALLOWED_TOP))}. "
                "Put new code in apps/, packages/ or services/ and register it in packages.json."
            )
    for g in GROUPS:
        gd = root / g
        if not gd.is_dir():
            continue
        for d in sorted(gd.iterdir()):
            if d.is_dir() and not d.name.startswith(".") and d.name not in IGNORE:
                rel = f"{g}/{d.name}"
                if rel not in registered:
                    errors.append(
                        f"{rel} is not registered in packages.json. Add an entry (path, status, test command) "
                        "and a README.md with '## Purpose' and '## Test command' (see CONTRIBUTING.md)."
                    )
    for rel in sorted(registered):
        d = root / rel
        if not d.is_dir():
            errors.append(f"{rel} is in packages.json but the directory is missing.")
        elif not (d / "README.md").is_file():
            errors.append(
                f"{rel}/README.md is missing (purpose and test command required)."
            )
    return errors


def main(argv):
    root = Path(__file__).resolve().parents[1]
    if "--root" in argv:
        root = Path(argv[argv.index("--root") + 1])
    errors = check(root)
    for e in errors:
        print(f"layout error: {e}")
    if not errors:
        print("layout ok")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
