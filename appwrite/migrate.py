#!/usr/bin/env python3
"""Migration runner for the Appwrite projects (F02, #28). Stdlib only.

Runs numbered scripts from `migrations/` (`NNNN_name.py`, each defining `up(ctx)`) in order and
records each applied version in the `schema_migration` table.

Guarantees (each has a test in tests/test_migrate.py):
- re-running is a no-op; applied scripts are never run again, and an edited applied script is an error;
- the first failure stops the run (no later script runs) and prints PROMOTE=false so CI does not promote;
- before each script, waits until every column and index is `available` (fails after a timeout);
- seed scripts (`NNNN_seed_*.py`) are refused when the environment is production, before anything runs.

Talks to Appwrite only through the `AppwriteClient` interface: `fake_client.FakeAppwriteClient` in tests,
`appwrite_client.HttpAppwriteClient` for real. The live project is never touched by agents (ADR 0012).

usage: migrate.py --env development|staging|production [--dir migrations] [--environments environments.json]
Exit codes: 0 ok, 1 failed (do not promote), 2 invalid migrations or environment, 3 seed refused in production.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import os
import re
import sys
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
ENVIRONMENTS = ("development", "staging", "production")
ALIASES = {"dev": "development", "stage": "staging", "prod": "production"}
NAME_RE = re.compile(r"^(\d{4})_([a-z0-9_]+)\.py$")
WAIT_TIMEOUT_S = 300
POLL_S = 2


class AppwriteClient(ABC):
    """What the runner needs from Appwrite. Implemented by the fake (tests) and the HTTP adapter."""

    @abstractmethod
    def ensure_migration_table(self) -> None:
        """Create `schema_migration` if missing."""

    @abstractmethod
    def applied_versions(self) -> list[dict]:
        """Rows of schema_migration: version (int), name, checksum; sorted by version."""

    @abstractmethod
    def record_applied(self, version: int, name: str, checksum: str) -> None: ...

    @abstractmethod
    def list_tables(self) -> list[str]: ...

    @abstractmethod
    def column_statuses(self, table: str) -> dict[str, str]:
        """column key -> available | processing | deleting | stuck | failed."""

    @abstractmethod
    def index_statuses(self, table: str) -> dict[str, str]: ...

    # Injectable clock for waits (tests replace them; the real adapter sleeps for real).
    sleep = staticmethod(time.sleep)
    now = staticmethod(time.monotonic)
    wait_timeout = WAIT_TIMEOUT_S
    wait_poll = POLL_S

    def _poll(self) -> None:
        """Hook called once per status poll by `wait_columns_available` (the fake advances its clock here)."""

    def wait_columns_available(self, table: str, keys) -> None:
        """Block until every named column of `table` is `available`. Appwrite processes columns asynchronously and rejects
        an index over a column that is still `processing`. Fails fast on `failed`/`stuck`, and after `wait_timeout`."""
        start, keys = self.now(), list(keys)
        while True:
            self._poll()
            statuses = self.column_statuses(table)
            failed = [f"{table}.{k} ({statuses[k]})" for k in keys if statuses.get(k) in ("failed", "stuck")]
            if failed:
                raise MigrationError(f"column failed: {', '.join(failed)}", 1)
            pending = [f"{table}.{k} ({statuses.get(k, 'missing')})" for k in keys if statuses.get(k) != "available"]
            if not pending:
                return
            if self.now() - start >= self.wait_timeout:
                raise MigrationError(f"timed out after {self.wait_timeout}s waiting for: {', '.join(pending)}", 1)
            self.sleep(self.wait_poll)

    def ensure_table(self, spec: dict) -> dict:
        """Create the table described by `spec` (Appwrite CLI table format, see schema_build.py) if missing, and any
        missing columns and indexes. Convergent: never alters or drops. Waits for every column to be `available` before
        creating indexes (timeout; fails fast on a failed column). Drift raises `SchemaDrift`: a column whose type, size,
        required, array, enum elements, default (and min/max/format when the spec sets them) differ; an index whose type,
        columns or orders differ; a table whose rowSecurity or table-level permissions differ. Returns
        {"created": bool, "columns_added": [...], "indexes_added": [...]}. Used by schema migrations (M01+). Not abstract
        so existing test doubles keep working."""
        raise NotImplementedError(f"{type(self).__name__} does not implement ensure_table")


class SchemaDrift(ValueError):
    """An existing table, column or index differs from the spec; `ensure_table` never alters, so a human decides."""


def _norm(value):
    return None if value in (None, "") else value


def column_drift(spec: dict, have: dict) -> list[str]:
    """Differences between a spec column and an existing one (REST responses normalised by the caller). Empty = same."""
    diffs = []
    for k in ("type", "required", "array", "default"):
        a, b = _norm(spec.get(k)), _norm(have.get(k))
        if k in ("required", "array"):
            a, b = bool(spec.get(k)), bool(have.get(k))
        if a != b:
            diffs.append(f"{k}: {b!r} -> {a!r}")
    for k in ("size", "elements", "format", "min", "max"):  # only when the spec states them (Appwrite fills defaults)
        if k in spec and _norm(spec[k]) != _norm(have.get(k)):
            diffs.append(f"{k}: {have.get(k)!r} -> {spec[k]!r}")
    return diffs


def index_drift(spec: dict, have: dict) -> list[str]:
    diffs = []
    if spec["type"] != have.get("type"):
        diffs.append(f"type: {have.get('type')!r} -> {spec['type']!r}")
    if list(spec["columns"]) != list(have.get("columns", [])):
        diffs.append(f"columns: {have.get('columns')!r} -> {spec['columns']!r}")
    if have.get("orders") and [o.upper() for o in spec["orders"]] != [o.upper() for o in have["orders"]]:
        diffs.append(f"orders: {have['orders']!r} -> {spec['orders']!r}")
    return diffs


def table_drift(spec: dict, have: dict) -> list[str]:
    diffs = []
    if bool(spec["rowSecurity"]) != bool(have.get("rowSecurity")):
        diffs.append(f"rowSecurity: {have.get('rowSecurity')!r} -> {spec['rowSecurity']!r}")
    if sorted(spec["$permissions"]) != sorted(have.get("$permissions", [])):
        diffs.append("table permissions differ")
    return diffs


@dataclass
class Migration:
    version: int
    name: str
    path: Path
    checksum: str
    is_seed: bool


@dataclass
class RunResult:
    exit_code: int = 0
    applied: list[int] = field(default_factory=list)
    skipped: list[int] = field(default_factory=list)
    failed: int | None = None

    @property
    def promote(self) -> bool:
        return self.exit_code == 0


@dataclass
class Context:
    client: AppwriteClient
    environment: str


class MigrationError(Exception):
    def __init__(self, message: str, code: int = 2):
        super().__init__(message)
        self.code = code


def normalize_env(env: str) -> str:
    e = ALIASES.get(env.strip().lower(), env.strip().lower())
    if e not in ENVIRONMENTS:
        raise MigrationError(f"unknown environment {env!r}; expected one of {', '.join(ENVIRONMENTS)}")
    return e


def discover(directory) -> list[Migration]:
    found, seen = [], {}
    for p in sorted(Path(directory).glob("*.py")):
        if p.name.startswith("__"):
            continue
        m = NAME_RE.match(p.name)
        if not m:
            raise MigrationError(f"bad migration file name {p.name!r}; expected NNNN_name.py (lower case)")
        version, name = int(m.group(1)), m.group(2)
        if version in seen:
            raise MigrationError(f"duplicate migration version {version:04d}: {seen[version]} and {p.name}")
        seen[version] = p.name
        checksum = hashlib.sha256(p.read_bytes()).hexdigest()
        found.append(Migration(version, name, p, checksum, name.startswith("seed")))
    return sorted(found, key=lambda x: x.version)


def wait_until_available(client, out, sleep, now, timeout=WAIT_TIMEOUT_S, poll=POLL_S) -> None:
    """Block until all columns and indexes of all tables are `available`."""
    start = now()
    while True:
        pending, failed = [], []
        for table in client.list_tables():
            for kind, statuses in (("column", client.column_statuses(table)), ("index", client.index_statuses(table))):
                for key, status in statuses.items():
                    if status == "available":
                        continue
                    (failed if status in ("failed", "stuck") else pending).append(f"{table}.{key} ({kind}: {status})")
        if failed:
            raise MigrationError(f"schema element failed: {', '.join(failed)}", 1)
        if not pending:
            return
        if now() - start >= timeout:
            raise MigrationError(f"timed out after {timeout}s waiting for: {', '.join(pending)}", 1)
        print(f"waiting for {len(pending)} column/index to become available: {', '.join(pending[:5])}", file=out)
        sleep(poll)


def _load_up(path: Path):
    spec = importlib.util.spec_from_file_location(f"migration_{path.stem}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not callable(getattr(module, "up", None)):
        raise MigrationError(f"{path.name} defines no up(ctx)", 1)
    return module.up


def run(client, environment, directory, *, out=sys.stdout, sleep=time.sleep, now=time.monotonic, timeout=WAIT_TIMEOUT_S):
    result = RunResult()
    try:
        env = normalize_env(environment)
        migrations = discover(directory)
        seeds = [m for m in migrations if m.is_seed]
        if env == "production" and seeds:
            names = ", ".join(m.path.name for m in seeds)
            raise MigrationError(f"refusing to run seed scripts against production: {names}. Nothing was run.", 3)

        client.ensure_migration_table()
        applied = {r["version"]: r for r in client.applied_versions()}
        on_disk = {m.version: m for m in migrations}
        for version, row in applied.items():
            if version not in on_disk:
                raise MigrationError(f"applied version {version:04d} is missing from {directory}")
            if row["checksum"] != on_disk[version].checksum:
                raise MigrationError(f"migration {on_disk[version].path.name} changed after it was applied; add a new script instead")

        pending = [m for m in migrations if m.version not in applied]
        result.skipped = [m.version for m in migrations if m.version in applied]
        if not pending:
            print(f"[{env}] nothing to do ({len(applied)} migrations already applied)", file=out)
        for m in pending:
            result.failed = m.version
            wait_until_available(client, out, sleep, now, timeout)
            print(f"[{env}] applying {m.path.name}", file=out)
            try:
                _load_up(m.path)(Context(client, env))
            except MigrationError:
                raise
            except Exception as exc:  # noqa: BLE001 - any script failure must stop the run
                raise MigrationError(f"{m.path.name} failed: {type(exc).__name__}: {exc}", 1) from exc
            wait_until_available(client, out, sleep, now, timeout)
            client.record_applied(m.version, m.name, m.checksum)
            result.applied.append(m.version)
            result.failed = None
    except MigrationError as exc:
        result.exit_code = exc.code
        print(f"ERROR: {exc}", file=out)
        if result.failed is not None:
            print(f"stopped at version {result.failed:04d}; later migrations were not run", file=out)
    print(f"PROMOTE={'true' if result.promote else 'false'}", file=out)
    return result


def _default_client_factory(environments_file):
    def factory(env):
        import json

        import appwrite_client

        cfg = json.loads(Path(environments_file).read_text())["environments"][env]
        return appwrite_client.from_environment(cfg, os.environ)

    return factory


def main(argv=None, client_factory=None, out=sys.stdout, sleep=time.sleep):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--env", required=True)
    ap.add_argument("--dir", default=str(HERE / "migrations"))
    ap.add_argument("--environments", default=str(HERE / "environments.json"))
    ap.add_argument("--timeout", type=int, default=WAIT_TIMEOUT_S)
    args = ap.parse_args(argv)
    try:
        env = normalize_env(args.env)
    except MigrationError as exc:
        print(f"ERROR: {exc}\nPROMOTE=false", file=out)
        return exc.code
    factory = client_factory or _default_client_factory(args.environments)
    result = run(factory(env), env, args.dir, out=out, sleep=sleep, timeout=args.timeout)
    gh = os.environ.get("GITHUB_OUTPUT")
    if gh:
        with open(gh, "a") as fh:
            fh.write(f"promote={'true' if result.promote else 'false'}\n")
    return result.exit_code


if __name__ == "__main__":
    sys.exit(main())
