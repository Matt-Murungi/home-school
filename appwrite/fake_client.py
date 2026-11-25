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
