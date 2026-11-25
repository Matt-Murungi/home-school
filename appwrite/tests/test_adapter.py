"""Real adapter tests with a mocked HTTP opener: no network (F02)."""

import io
import json
import unittest

from helpers import APPWRITE_DIR  # noqa: F401

import appwrite_client as ac
from migrate import AppwriteClient


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class Recorder:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def __call__(self, req, timeout=None):
        self.requests.append(req)
        body = self.responses.pop(0)
        if isinstance(body, Exception):
            raise body
        return FakeResponse(json.dumps(body).encode())


def make(responses, key="test-key-value"):
    rec = Recorder(responses)
    c = ac.HttpAppwriteClient(
        endpoint="https://fra.cloud.appwrite.io/v1",
        project_id="proj1",
        database_id="main",
        api_key=key,
        opener=rec,
    )
    return c, rec


class Adapter(unittest.TestCase):
    def test_implements_interface(self):
        c, _ = make([])
        self.assertIsInstance(c, AppwriteClient)

    def test_headers_carry_project_and_key(self):
        c, rec = make([{"total": 0, "rows": []}])
        c.applied_versions()
        h = {k.lower(): v for k, v in rec.requests[0].header_items()}
        self.assertEqual(h["x-appwrite-project"], "proj1")
        self.assertEqual(h["x-appwrite-key"], "test-key-value")
        self.assertTrue(rec.requests[0].full_url.startswith("https://fra.cloud.appwrite.io/v1/tablesdb/main/tables/schema_migration/rows"))

    def test_column_statuses(self):
        c, rec = make([{"total": 2, "columns": [{"key": "a", "status": "available"}, {"key": "b", "status": "processing"}]}])
        self.assertEqual(c.column_statuses("t"), {"a": "available", "b": "processing"})
        self.assertIn("/tablesdb/main/tables/t/columns", rec.requests[0].full_url)

    def test_index_statuses(self):
        c, _ = make([{"total": 1, "indexes": [{"key": "i", "status": "available"}]}])
        self.assertEqual(c.index_statuses("t"), {"i": "available"})

    def test_record_applied_posts_row(self):
        c, rec = make([{"$id": "x"}])
        c.record_applied(3, "three", "abc")
        req = rec.requests[0]
        self.assertEqual(req.get_method(), "POST")
        body = json.loads(req.data)
        self.assertEqual(body["data"]["version"], 3)
        self.assertEqual(body["data"]["checksum"], "abc")

    def test_applied_versions_sorted(self):
        rows = [{"version": 2, "name": "b", "checksum": "y"}, {"version": 1, "name": "a", "checksum": "x"}]
        c, _ = make([{"total": 2, "rows": rows}])
        self.assertEqual([r["version"] for r in c.applied_versions()], [1, 2])

    def test_list_tables(self):
        c, _ = make([{"total": 1, "tables": [{"$id": "t1"}]}])
        self.assertEqual(c.list_tables(), ["t1"])

    def test_http_error_becomes_appwrite_error_without_key(self):
        import urllib.error

        err = urllib.error.HTTPError("u", 401, "no", {}, io.BytesIO(b'{"message": "bad key"}'))
        c, _ = make([err])
        with self.assertRaises(ac.AppwriteError) as cm:
            c.list_tables()
        self.assertIn("401", str(cm.exception))
        self.assertNotIn("test-key-value", str(cm.exception))

    def test_missing_table_creates_it_on_ensure(self):
        import urllib.error

        nf = urllib.error.HTTPError("u", 404, "nf", {}, io.BytesIO(b'{"type": "table_not_found"}'))
        c, rec = make([nf] + [{}] * 10)
        c.ensure_migration_table()
        methods = [(r.get_method(), r.full_url.rsplit("/v1", 1)[1]) for r in rec.requests]
        self.assertEqual(methods[0][0], "GET")
        self.assertEqual(methods[1][0], "POST")  # create table
        self.assertTrue(any("/columns/integer" in u for _, u in methods))

    def test_from_environment_reads_key_from_os_environ_only(self):
        env = {"APPWRITE_API_KEY": "k-from-env"}
        cfg = {"endpoint": "https://e/v1", "projectId": "p", "databaseId": "main"}
        c = ac.from_environment(cfg, env, opener=Recorder([]))
        self.assertEqual(c.api_key, "k-from-env")
        with self.assertRaises(ac.AppwriteError):
            ac.from_environment(cfg, {}, opener=Recorder([]))

    def test_repr_hides_key(self):
        c, _ = make([])
        self.assertNotIn("test-key-value", repr(c))


if __name__ == "__main__":
    unittest.main()
