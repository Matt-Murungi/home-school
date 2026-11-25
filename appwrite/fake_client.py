"""In-memory stand-in for Appwrite, implementing `migrate.AppwriteClient` (tests only; no network)."""

import itertools
import threading

from migrate import AppwriteClient, SchemaDrift, column_drift, index_drift, table_drift

MIGRATION_TABLE = "schema_migration"


class UniqueViolation(ValueError):
    """What Appwrite answers (409) when a row would duplicate the values of a unique index."""


class FakeAppwriteClient(AppwriteClient):
    """`tables`: {name: {"columns": {key: status}, "indexes": {key: status}}}.

    Columns/indexes created as "processing" report it until `polls_until_available` poll cycles
    (one cycle = one `list_tables()` call) have passed, then become "available".
    """

    def __init__(self, tables=None, polls_until_available=0, failing_columns=()):
        self.tables = {k: {"columns": dict(v["columns"]), "indexes": dict(v["indexes"])} for k, v in (tables or {}).items()}
        self.polls_until_available = polls_until_available
        self._tick = 0
        self._born = {}  # (table, "columns"|"indexes", key) -> tick at creation (elements made by ensure_table)
        self.failing_columns = set(failing_columns)  # "table.column" that Appwrite would end up marking `failed`
        self.slept, self._clock = [], 0  # virtual clock: sleep() advances it, so timeouts are testable without waiting
        self._rows = []
        self.log = []
        self.rows = {}  # table -> {row id: row}; only created through create_row/update_row
        self.last_query_index = None
        self._row_lock = threading.RLock()  # Appwrite checks unique indexes atomically per write
        self._row_ids = itertools.count(1)
        self.specs = {}  # table -> {"rowSecurity", "$permissions", "columns": {key: spec}, "indexes": {key: spec}} for ensure_table

    def ensure_migration_table(self):
        self.tables.setdefault(MIGRATION_TABLE, {"columns": {"version": "available"}, "indexes": {"unique_version": "available"}})

    def applied_versions(self):
        return sorted(self._rows, key=lambda r: r["version"])

    applied_rows = applied_versions

    def record_applied(self, version, name, checksum):
        if any(r["version"] == version for r in self._rows):
            raise ValueError(f"version {version} already recorded (unique index)")
        self._rows.append({"version": version, "name": name, "checksum": checksum})

    def list_tables(self):
        self._tick += 1
        return list(self.tables)

    def sleep(self, seconds):
        self.slept.append(seconds)
        self._clock += seconds

    def now(self):
        return self._clock

    def _poll(self):
        self._tick += 1  # one status poll by ensure_table's wait = one processing cycle

    def _status(self, status, table="", kind="", key=""):
        if f"{table}.{key}" in self.failing_columns and kind == "columns" and status == "processing" and self._tick > self._born.get((table, kind, key), 0):
            return "failed"
        if status == "processing" and self._tick - self._born.get((table, kind, key), 0) > self.polls_until_available:
            return "available"
        return status

    def column_statuses(self, table):
        return {k: self._status(v, table, "columns", k) for k, v in self.tables[table]["columns"].items()}

    def index_statuses(self, table):
        return {k: self._status(v, table, "indexes", k) for k, v in self.tables[table]["indexes"].items()}

    def snapshot_statuses(self):
        """Set of all column/index statuses as a script would see them right now."""
        return {self._status(v, t, kind, k) for t in self.tables for kind in ("columns", "indexes") for k, v in self.tables[t][kind].items()}

    def _create_index(self, name, i):
        """What Appwrite checks when an index is created."""
        have = self.specs[name]
        statuses = self.column_statuses(name)
        missing = [k for k in i["columns"] if k not in have["columns"]]
        if missing:
            raise ValueError(f"{name}.{i['key']}: index on unknown column {missing}")
        busy = [k for k in i["columns"] if statuses.get(k) != "available"]
        if busy:
            raise ValueError(f"{name}.{i['key']}: column not available (still processing): {busy}")
        if any(have["columns"][k]["type"] == "text" for k in i["columns"]) and i["type"] != "fulltext":
            raise ValueError(f"{name}.{i['key']}: text column cannot be indexed")
        have["indexes"][i["key"]] = dict(i)
        self.tables[name]["indexes"][i["key"]] = "processing" if self.polls_until_available else "available"
        self._born[(name, "indexes", i["key"])] = self._tick

    def ensure_table(self, spec):
        """Mirror of what Appwrite enforces; convergent like the real adapter (see AppwriteClient.ensure_table)."""
        name = spec["$id"]
        status = "processing" if self.polls_until_available else "available"
        created = name not in self.tables
        if created:
            self.tables[name] = {"columns": {}, "indexes": {}}
            self.specs[name] = {"rowSecurity": spec["rowSecurity"], "$permissions": list(spec["$permissions"]), "columns": {}, "indexes": {}}
            self.log.append(f"create_table:{name}")
        have = self.specs.setdefault(name, {"rowSecurity": spec["rowSecurity"], "$permissions": [], "columns": {}, "indexes": {}})
        if not created and (diffs := table_drift(spec, have)):
            raise SchemaDrift(f"{name}: table exists with a different definition (drift): {'; '.join(diffs)}")
        columns_added, indexes_added = [], []
        for c in spec["columns"]:
            old = have["columns"].get(c["key"])
            if old is not None:
                if diffs := column_drift(c, old):
                    raise SchemaDrift(f"{name}.{c['key']}: column exists with a different definition (drift): {'; '.join(diffs)}")
                continue
            if c["type"] == "varchar" and not 1 <= c.get("size", 0) <= 16383:
                raise ValueError(f"{name}.{c['key']}: invalid varchar size")
            if c.get("required") and "default" in c:
                raise ValueError(f"{name}.{c['key']}: required column cannot have a default")
            have["columns"][c["key"]] = dict(c)
            self.tables[name]["columns"][c["key"]] = status
            self._born[(name, "columns", c["key"])] = self._tick
            columns_added.append(c["key"])
        new_indexes = [i for i in spec["indexes"] if i["key"] not in have["indexes"]]
        for i in spec["indexes"]:
            if i["key"] in have["indexes"] and (diffs := index_drift(i, have["indexes"][i["key"]])):
                raise SchemaDrift(f"{name}.{i['key']}: index exists with a different definition (drift): {'; '.join(diffs)}")
        if new_indexes:
            self.wait_columns_available(name, [c["key"] for c in spec["columns"]])
        for i in new_indexes:
            self._create_index(name, i)
            indexes_added.append(i["key"])
        if columns_added or indexes_added:
            self.log.append(f"ensure_table:{name}")
        return {"created": created, "columns_added": columns_added, "indexes_added": indexes_added}

    # --- rows (tests only): enough of Appwrite's row semantics to prove unique-index backstops ---------------------------
    def _check_row(self, table, data):
        have = self.specs[table]["columns"]
        for k, v in data.items():
            if k.startswith("$"):
                continue
            c = have.get(k)
            if c is None:
                raise ValueError(f"{table}.{k}: unknown column")
            if v is None:
                if c.get("required"):
                    raise ValueError(f"{table}.{k}: required column cannot be null")
                continue
            if c.get("format") == "enum" and v not in c["elements"]:
                raise ValueError(f"{table}.{k}: {v!r} is not one of {c['elements']}")
            if c["type"] == "integer" and (isinstance(v, bool) or not isinstance(v, int)):
                raise ValueError(f"{table}.{k}: integer expected")
            if c["type"] == "boolean" and not isinstance(v, bool):
                raise ValueError(f"{table}.{k}: boolean expected")
            if c["type"] == "varchar" and (not isinstance(v, str) or len(v) > c["size"]):
                raise ValueError(f"{table}.{k}: string of at most {c['size']} characters expected")

    def _check_unique(self, table, row, ignore_id=None):
        for i in self.specs[table]["indexes"].values():
            if i["type"] != "unique":
                continue
            key = tuple(row.get(c) for c in i["columns"])
            if any(v is None for v in key):  # MariaDB/Appwrite: NULLs never collide
                continue
            for other in self.rows[table].values():
                if other["$id"] != ignore_id and tuple(other.get(c) for c in i["columns"]) == key:
                    raise UniqueViolation(f"{table}.{i['key']}: a row with these values already exists")

    def create_row(self, table, data, row_id=None):
        with self._row_lock:
            self._check_row(table, data)
            missing = [k for k, c in self.specs[table]["columns"].items() if c.get("required") and data.get(k) is None]
            if missing:
                raise ValueError(f"{table}: required columns missing: {missing}")
            row = dict(data)
            row["$id"] = row_id or f"r{next(self._row_ids)}"
            rows = self.rows.setdefault(table, {})
            if row["$id"] in rows:
                raise UniqueViolation(f"{table}: row id {row['$id']} already exists")
            self._check_unique(table, row)
            rows[row["$id"]] = row
            return dict(row)

    def update_row(self, table, row_id, patch):
        with self._row_lock:
            self._check_row(table, patch)
            row = {**self.rows[table][row_id], **patch}
            self._check_unique(table, row, ignore_id=row_id)
            self.rows[table][row_id] = row
            return dict(row)

    def delete_row(self, table, row_id):
        """Hard delete (only for derived/rebuildable tables such as topic_progress, and the retention job)."""
        with self._row_lock:
            return self.rows[table].pop(row_id)

    def get_row(self, table, row_id):
        return dict(self.rows[table][row_id])

    def list_rows(self, table, **equals):
        """Equality filters; like Appwrite, a query needs an index whose first column is filtered."""
        with self._row_lock:
            idx = next((i for i in self.specs[table]["indexes"].values() if i["columns"][0] in equals), None)
            if equals and idx is None:
                raise ValueError(f"{table}: no index for query on {sorted(equals)}")
            self.last_query_index = idx["key"] if idx else None
            return [dict(r) for r in self.rows.get(table, {}).values() if all(r.get(k) == v for k, v in equals.items())]
