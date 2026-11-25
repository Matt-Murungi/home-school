#!/usr/bin/env python3
"""Compare the repo schema (appwrite/schema/*.json) with a read-only structure snapshot of the live project (M01, owner decision 2).

usage: diff_live.py <snapshot.json> [--only table,table] [--alias repo_table=live_table,...]

Offline: reads the snapshot file only (never calls Appwrite). Prints table, column, index and permission NAMES and
structure (types, sizes, required flags, enum members); it never prints defaults, row data or ids. The snapshot is
git-ignored and must not be committed.

Snapshot shape: {"databases": [{"id": "main", "tables": [{"id", "rowSecurity", "permissions", "columns": [...], "indexes": [...]}]}]}.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import schema_build


def _norm_col(c: dict) -> dict:
    fmt = c.get("format") or ""
    kind = "enum" if fmt == "enum" else (f"string/{fmt}" if c["type"] == "string" and fmt else c["type"])
    return {
        "type": kind,
        "size": c.get("size"),
        "required": bool(c.get("required")),
        "array": bool(c.get("array")),
        "elements": sorted(c.get("elements") or []),
    }


def _norm_idx(i: dict) -> tuple:
    return (i["type"], tuple(i["columns"]))


def _repo_tables(domains) -> dict[str, dict]:
    return {t["$id"]: t for d in domains for t in d["tables"]}


def _live_tables(snapshot: dict) -> dict[str, dict]:
    out = {}
    for db in snapshot.get("databases", []):
        for t in db.get("tables", []):
            out[t.get("id") or t.get("$id")] = t
    return out


def diff(domains, snapshot, only=None, aliases=None) -> dict:
    aliases = aliases or {}
    repo, live = _repo_tables(domains), _live_tables(snapshot)
    scope = set(only) if only else set(repo)
    report = {"tables_both": [], "tables_repo_only": [], "tables_live_only": [], "aliases": aliases, "details": {}}
    matched_live = set()
    for name in sorted(scope):
        if name not in repo:
            continue
        lname = aliases.get(name, name)
        if lname not in live:
            report["tables_repo_only"].append(name)
            continue
        matched_live.add(lname)
        report["tables_both"].append(name)
        r, l = repo[name], live[lname]
        d: dict = {"live_name": lname, "settings": [], "columns_repo_only": [], "columns_live_only": [], "columns_differ": [],
                   "indexes_repo_only": [], "indexes_live_only": [], "permissions": None}
        if bool(l.get("rowSecurity")) != bool(r["rowSecurity"]):
            d["settings"].append(f"rowSecurity repo={r['rowSecurity']} live={bool(l.get('rowSecurity'))}")
        rp, lp = sorted(r.get("$permissions", [])), sorted(l.get("permissions") or l.get("$permissions") or [])
        if rp != lp:
            d["permissions"] = {"repo": rp, "live": lp}
        rc = {c["key"]: _norm_col(c) for c in r["columns"]}
        lc = {c["key"]: _norm_col(c) for c in l.get("columns", [])}
        d["columns_repo_only"] = sorted(set(rc) - set(lc))
        d["columns_live_only"] = sorted(set(lc) - set(rc))
        for k in sorted(set(rc) & set(lc)):
            diffs = [f"{f} repo={rc[k][f]} live={lc[k][f]}" for f in ("type", "size", "required", "array") if rc[k][f] != lc[k][f]]
            if rc[k]["type"] == lc[k]["type"] == "enum" and rc[k]["elements"] != lc[k]["elements"]:
                diffs.append(f"enum members: repo-only {sorted(set(rc[k]['elements']) - set(lc[k]['elements']))} live-only {sorted(set(lc[k]['elements']) - set(rc[k]['elements']))}")
            if diffs:
                d["columns_differ"].append((k, diffs))
        ri = {_norm_idx(i) for i in r["indexes"]}
        li = {_norm_idx(i) for i in l.get("indexes", [])}
        d["indexes_repo_only"] = sorted(f"{t} {list(c)}" for t, c in ri - li)
        d["indexes_live_only"] = sorted(f"{t} {list(c)}" for t, c in li - ri)
        report["details"][name] = d
    if not only:
        report["tables_live_only"] = sorted(set(live) - matched_live)
    else:
        report["tables_live_only"] = sorted(n for n in live if n in {aliases.get(x, x) for x in scope} - matched_live)
    return report


def render(report: dict) -> str:
    out = ["# Repo schema vs live snapshot (structure only)", ""]
    out.append(f"Tables in both: {len(report['tables_both'])}; repo only: {len(report['tables_repo_only'])}; live only: {len(report['tables_live_only'])}")
    out += ["", "## Repo only (not in live): " + (", ".join(report["tables_repo_only"]) or "none")]
    out += ["", "## Live only (not in repo): " + (", ".join(report["tables_live_only"]) or "none")]
    for name in report["tables_both"]:
        d = report["details"][name]
        title = name if d["live_name"] == name else f"{name} (live: {d['live_name']})"
        lines = []
        lines += [f"  setting: {s}" for s in d["settings"]]
        if d["permissions"]:
            lines.append(f"  permissions repo={d['permissions']['repo']} live={d['permissions']['live']}")
        if d["columns_repo_only"]:
            lines.append("  columns in repo only: " + ", ".join(d["columns_repo_only"]))
        if d["columns_live_only"]:
            lines.append("  columns in live only: " + ", ".join(d["columns_live_only"]))
        for k, diffs in d["columns_differ"]:
            lines.append(f"  column {k}: " + "; ".join(diffs))
        if d["indexes_repo_only"]:
            lines.append("  indexes in repo only: " + "; ".join(d["indexes_repo_only"]))
        if d["indexes_live_only"]:
            lines.append("  indexes in live only: " + "; ".join(d["indexes_live_only"]))
        out += ["", f"## {title}: " + ("identical" if not lines else "differs")] + lines
    return "\n".join(out) + "\n"


def main(argv=None, out=sys.stdout) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("snapshot")
    ap.add_argument("--only", help="comma-separated repo table ids to compare")
    ap.add_argument("--alias", help="repo_table=live_table,... when live names a table differently")
    args = ap.parse_args(argv)
    snapshot = json.loads(Path(args.snapshot).read_text())
    aliases = dict(p.split("=", 1) for p in args.alias.split(",")) if args.alias else {}
    only = args.only.split(",") if args.only else None
    print(render(diff(schema_build.load_domains(), snapshot, only, aliases)), file=out, end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
