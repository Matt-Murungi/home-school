"""Thin real adapter for the migration runner: Appwrite REST (TablesDB) over urllib. Stdlib only.

Written but only exercised by tests with a mocked `opener` (no network). The API key is read from
`os.environ` by `from_environment`, never from a file, and never appears in errors or repr.
Caveat: REST paths follow the Appwrite 1.8 TablesDB API from memory of the docs and have NOT been run
against a live project (owner step: run once against the dev project, see memory.md).
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
import uuid

from migrate import AppwriteClient

MIGRATION_TABLE = "schema_migration"


class AppwriteError(Exception):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


class HttpAppwriteClient(AppwriteClient):
    def __init__(self, endpoint, project_id, database_id, api_key, opener=None, timeout=30):
        self.endpoint = endpoint.rstrip("/")
        self.project_id = project_id
        self.database_id = database_id
        self.api_key = api_key
        self._open = opener or urllib.request.urlopen
        self.timeout = timeout

    def __repr__(self):
        return f"HttpAppwriteClient(endpoint={self.endpoint!r}, project_id={self.project_id!r}, api_key=<hidden>)"

    # -- low level -------------------------------------------------------
    def _call(self, method, path, body=None, query=None):
        url = f"{self.endpoint}/tablesdb/{self.database_id}{path}"
        if query:
            url += "?" + urllib.parse.urlencode(query, doseq=True)
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("X-Appwrite-Project", self.project_id)
        req.add_header("X-Appwrite-Key", self.api_key)
        req.add_header("Content-Type", "application/json")
        try:
            with self._open(req, timeout=self.timeout) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = json.loads(exc.read() or b"{}").get("message", "")
            except (ValueError, AttributeError):
                pass
            raise AppwriteError(f"{method} {path} -> HTTP {exc.code} {detail}".strip(), exc.code) from None
        except urllib.error.URLError as exc:
            raise AppwriteError(f"{method} {path} failed: {exc.reason}") from None
        return json.loads(raw) if raw else {}

    # -- AppwriteClient --------------------------------------------------
    def ensure_migration_table(self):
        try:
            self._call("GET", f"/tables/{MIGRATION_TABLE}")
            return
        except AppwriteError as exc:
            if exc.status != 404:
                raise
        self._call("POST", "/tables", {"tableId": MIGRATION_TABLE, "name": MIGRATION_TABLE, "permissions": [], "rowSecurity": True})
        base = f"/tables/{MIGRATION_TABLE}"
        self._call("POST", f"{base}/columns/integer", {"key": "version", "required": True})
        self._call("POST", f"{base}/columns/string", {"key": "name", "size": 128, "required": True})
        self._call("POST", f"{base}/columns/string", {"key": "checksum", "size": 64, "required": True})
        self._call("POST", f"{base}/columns/datetime", {"key": "applied_at", "required": False})
        self._call("POST", f"{base}/indexes", {"key": "unique_version", "type": "unique", "columns": ["version"]})

    def applied_versions(self):
        q = {"queries[]": [json.dumps({"method": "limit", "values": [5000]})]}
        resp = self._call("GET", f"/tables/{MIGRATION_TABLE}/rows", query=q)
        rows = [{"version": r["version"], "name": r["name"], "checksum": r["checksum"]} for r in resp.get("rows", [])]
        return sorted(rows, key=lambda r: r["version"])

    def record_applied(self, version, name, checksum):
        self._call(
            "POST",
            f"/tables/{MIGRATION_TABLE}/rows",
            {"rowId": uuid.uuid4().hex, "data": {"version": version, "name": name, "checksum": checksum}, "permissions": []},
        )

    def list_tables(self):
        return [t["$id"] for t in self._call("GET", "/tables").get("tables", [])]

    def column_statuses(self, table):
        return {c["key"]: c["status"] for c in self._call("GET", f"/tables/{table}/columns").get("columns", [])}

    def index_statuses(self, table):
        return {i["key"]: i["status"] for i in self._call("GET", f"/tables/{table}/indexes").get("indexes", [])}

    def ensure_table(self, spec):
        """Create table, missing columns and indexes. Convergent, never alters. REST shapes written from the docs, not run
        live: verify once against dev (owner step in memory.md, M01)."""
        name = spec["$id"]
        created = False
        existing_cols, existing_idx = {}, set()
        try:
            self._call("GET", f"/tables/{name}")
            existing_cols = {c["key"]: c for c in self._call("GET", f"/tables/{name}/columns").get("columns", [])}
            existing_idx = {i["key"] for i in self._call("GET", f"/tables/{name}/indexes").get("indexes", [])}
        except AppwriteError as exc:
            if exc.status != 404:
                raise
            self._call("POST", "/tables", {"tableId": name, "name": spec.get("name", name), "permissions": spec["$permissions"], "rowSecurity": spec["rowSecurity"]})
            created = True
        columns_added, indexes_added = [], []
        for c in spec["columns"]:
            if c["key"] in existing_cols:
                have = existing_cols[c["key"]]
                if have.get("required") is not None and bool(have["required"]) != bool(c["required"]):
                    raise AppwriteError(f"{name}.{c['key']}: column exists with a different definition (drift)")
                continue
            kind = "enum" if c.get("format") == "enum" else c["type"]
            body = {"key": c["key"], "required": c["required"]}
            for k in ("size", "min", "max", "elements", "default", "array"):
                if k in c:
                    body[k] = c[k]
            self._call("POST", f"/tables/{name}/columns/{kind}", body)
            columns_added.append(c["key"])
        for i in spec["indexes"]:
            if i["key"] in existing_idx:
                continue
            self._call("POST", f"/tables/{name}/indexes", {"key": i["key"], "type": i["type"], "columns": i["columns"], "orders": i["orders"]})
            indexes_added.append(i["key"])
        return {"created": created, "columns_added": columns_added, "indexes_added": indexes_added}


def from_environment(cfg, environ, opener=None):
    """Build a client from one entry of environments.json; the key comes from `environ[cfg.keyEnvVar]`
    (default APPWRITE_API_KEY). Missing key is an error, never read from disk."""
    var = cfg.get("keyEnvVar", "APPWRITE_API_KEY")
    key = environ.get(var) or environ.get("APPWRITE_API_KEY")
    if not key:
        raise AppwriteError(f"environment variable {var} (or APPWRITE_API_KEY) is not set")
    return HttpAppwriteClient(cfg["endpoint"], cfg["projectId"], cfg.get("databaseId", "main"), key, opener=opener)
