"""In-memory stand-in for Appwrite, implementing `migrate.AppwriteClient` (tests only; no network)."""

from migrate import AppwriteClient

MIGRATION_TABLE = "schema_migration"


class FakeAppwriteClient(AppwriteClient):
    """`tables`: {name: {"columns": {key: status}, "indexes": {key: status}}}.

    Columns/indexes created as "processing" report it until `polls_until_available` poll cycles
    (one cycle = one `list_tables()` call) have passed, then become "available".
    """

    def __init__(self, tables=None, polls_until_available=0):
        self.tables = {k: {"columns": dict(v["columns"]), "indexes": dict(v["indexes"])} for k, v in (tables or {}).items()}
        self.polls_until_available = polls_until_available
        self._tick = 0
        self._rows = []
        self.log = []
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

    def _status(self, status):
        if status == "processing" and self._tick > self.polls_until_available:
            return "available"
        return status

    def column_statuses(self, table):
        return {k: self._status(v) for k, v in self.tables[table]["columns"].items()}

    def index_statuses(self, table):
        return {k: self._status(v) for k, v in self.tables[table]["indexes"].items()}

    def snapshot_statuses(self):
        """Set of all column/index statuses as a script would see them right now."""
        return {s for t in self.tables for kind in ("columns", "indexes") for s in map(self._status, self.tables[t][kind].values())}

    def ensure_table(self, spec):
        """Mirror of what Appwrite enforces for table creation; convergent like the real adapter."""
        name = spec["$id"]
        status = "processing" if self.polls_until_available else "available"
        created = name not in self.tables
        if created:
            self.tables[name] = {"columns": {}, "indexes": {}}
            self.specs[name] = {"rowSecurity": spec["rowSecurity"], "$permissions": list(spec["$permissions"]), "columns": {}, "indexes": {}}
            self.log.append(f"create_table:{name}")
        have = self.specs.setdefault(name, {"rowSecurity": spec["rowSecurity"], "$permissions": [], "columns": {}, "indexes": {}})
        columns_added, indexes_added = [], []
        for c in spec["columns"]:
            old = have["columns"].get(c["key"])
            if old is not None:
                if any(old.get(k) != c.get(k) for k in ("type", "size", "required", "format", "array")):
                    raise ValueError(f"{name}.{c['key']}: column exists with a different definition (drift)")
                continue
            if c["type"] == "varchar" and not 1 <= c.get("size", 0) <= 16383:
                raise ValueError(f"{name}.{c['key']}: invalid varchar size")
            if c.get("required") and "default" in c:
                raise ValueError(f"{name}.{c['key']}: required column cannot have a default")
            have["columns"][c["key"]] = dict(c)
            self.tables[name]["columns"][c["key"]] = status
            columns_added.append(c["key"])
        for i in spec["indexes"]:
            if i["key"] in have["indexes"]:
                continue
            missing = [k for k in i["columns"] if k not in have["columns"]]
            if missing:
                raise ValueError(f"{name}.{i['key']}: index on unknown column {missing}")
            if any(have["columns"][k]["type"] == "text" for k in i["columns"]) and i["type"] != "fulltext":
                raise ValueError(f"{name}.{i['key']}: text column cannot be indexed")
            have["indexes"][i["key"]] = dict(i)
            self.tables[name]["indexes"][i["key"]] = status
            indexes_added.append(i["key"])
        if columns_added or indexes_added:
            self.log.append(f"ensure_table:{name}")
        return {"created": created, "columns_added": columns_added, "indexes_added": indexes_added}
