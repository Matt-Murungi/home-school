#!/usr/bin/env python3
"""Permission drift check for CI (M05 #40, ADR 0012 "Drift check").

    cd services/core
    uv run python scripts/permissions_check.py              # seeded fake dataset
    uv run python scripts/permissions_check.py --write-docs
    uv run python scripts/permissions_check.py --live       # a real project, read only

Default mode builds the seeded dataset (every table), lets the PermissionService compute every
row's permissions, then runs the same audit the daily `permission-audit` job runs and expects ZERO
differences (exit 0). It also fails when docs/ops/permissions.md is stale. `--live` runs the audit
against the configured Appwrite project with the API key from the environment (needs staging or
dev settings; owner-blocked, nothing here has run it). The audit never writes.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from hs_core.permissions import matrixdoc  # noqa: E402
from hs_core.permissions.audit import audit_all  # noqa: E402
from hs_core.permissions.sample import SampleWorld  # noqa: E402


class LiveCheckRefused(RuntimeError):
    """`--live` needs a real AppwriteRepository (an in-memory one audits nothing and goes green)."""


class NothingAudited(RuntimeError):
    """The audit saw no rows at all: that is not "zero differences"."""


def schema_tables() -> list[str]:
    root = Path(__file__).resolve().parents[3] / "appwrite" / "schema"
    return sorted(
        t["$id"] for f in root.glob("*.json") for t in json.loads(f.read_text())["tables"]
    )


def undecided_tables() -> list[str]:
    from hs_core.permissions.rules import SPECS

    return sorted(set(schema_tables()) - set(SPECS))


def require_real_repository(repo) -> None:
    from hs_core.repository import AppwriteRepository

    if not isinstance(repo, AppwriteRepository):
        raise LiveCheckRefused(
            f"LiveCheckRefused: --live needs a configured Appwrite project (got "
            f"{type(repo).__name__}); "
            "set APPWRITE_ENDPOINT, APPWRITE_PROJECT_ID, APPWRITE_API_KEY and APPWRITE_DATABASE_ID"
        )


def verdict(summary: dict, expect_rows: bool = True) -> None:
    if expect_rows and summary["rows"] == 0:
        raise NothingAudited("NothingAudited: the audit saw 0 rows, so it proves nothing")


def empty_table_notes(summary: dict) -> list[str]:
    return [
        f"NOTE: no rows in `{t}` (expected for a new project, not for the seeded dataset)"
        for t in summary.get("empty_tables", [])
    ]


def check_seeded() -> dict:
    w = SampleWorld().seed()
    return audit_all(w.repo, w.files, w.clock).to_dict()


def check_live() -> dict:  # pragma: no cover - needs a real project
    from hs_core.permissions.files import AppwriteFileStore
    from hs_core.permissions.service import PermissionService
    from hs_core.repository import AppwriteRepository, build_repository
    from hs_core.settings import load_settings

    settings = load_settings()
    repo = build_repository(settings)
    require_real_repository(repo)
    files = AppwriteFileStore(repo._client) if isinstance(repo, AppwriteRepository) else None
    svc = PermissionService(repo, files=files)
    return audit_all(repo, files, svc.clock).to_dict()


def main(argv: list[str] | None = None, out=sys.stdout) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument(
        "--write-docs", action="store_true", help="regenerate the matrix in docs/ops/permissions.md"
    )
    ap.add_argument(
        "--live", action="store_true", help="audit the configured Appwrite project (read only)"
    )
    args = ap.parse_args(argv)
    if args.write_docs:
        changed = matrixdoc.write_doc()
        print("docs/ops/permissions.md " + ("updated" if changed else "already current"), file=out)
        return 0
    status = 0
    missing = undecided_tables()
    if missing:
        print(
            f"ERROR: tables with no permission decision (add a Spec in rules.SPECS): {missing}",
            file=out,
        )
        status = 1
    if not matrixdoc.doc_is_current():
        print("ERROR: docs/ops/permissions.md matrix is stale (run with --write-docs)", file=out)
        status = 1
    try:
        summary = check_live() if args.live else check_seeded()
        verdict(summary)
    except (LiveCheckRefused, NothingAudited) as exc:
        print(f"ERROR: {exc}", file=out)
        return 2
    print(json.dumps(summary, sort_keys=True, indent=2), file=out)
    for note in empty_table_notes(summary):
        print(note, file=out)
    if not args.live and summary["empty_tables"]:
        print("ERROR: the seeded dataset must have rows in every client-readable table", file=out)
        status = 1
    drift = (
        summary["over_grants"]
        + summary["under_grants"]
        + summary["forbidden"]
        + summary["malformed"]
    )
    if drift:
        print(f"ERROR: {drift} rows differ from the expected permissions", file=out)
        status = 1
    else:
        print("permissions ok: zero differences", file=out)
    return status


if __name__ == "__main__":
    sys.exit(main())
