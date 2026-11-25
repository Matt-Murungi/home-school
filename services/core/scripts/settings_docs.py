#!/usr/bin/env python3
"""Regenerate (--write) or check (default) the settings table in docs/ops/settings.md (H05 #134).

cd services/core && uv run python scripts/settings_docs.py --write
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from hs_core.app_settings.docs import BEGIN, DOC, END, render_table, write_docs  # noqa: E402


def main(argv: list[str]) -> int:
    if "--write" in argv:
        print("settings docs rewritten" if write_docs() else "settings docs already current")
        return 0
    block = DOC.read_text().split(BEGIN, 1)[1].split(END, 1)[0].strip()
    if block != render_table().strip():
        print("docs/ops/settings.md is stale: run scripts/settings_docs.py --write")
        return 1
    print("settings docs ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
