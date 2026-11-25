"""M05 (#40): the two bookkeeping tables of the permission service (domain `permissions`).

Mapped to the issue in memory.md (M05 block). The permission rules themselves are tested in
services/core/tests/test_perm_*.py."""

import copy
import io
import unittest

from helpers import APPWRITE_DIR

import migrate
import schema_build as sb
from fake_client import FakeAppwriteClient

DOMAINS = sb.load_domains()
DOMAIN = next(d for d in DOMAINS if d["domain"] == "permissions")
TABLES = {t["$id"]: t for t in DOMAIN["tables"]}


def cols(table):
    return {c["key"]: c for c in TABLES[table]["columns"]}


class Tables(unittest.TestCase):
    def test_the_domain_has_exactly_the_two_tables_and_validates(self):
        self.assertEqual(set(TABLES), {"access_window", "teacher_session_history"})
        self.assertEqual(sb.validate(DOMAINS), [])

    def test_row_security_on_and_no_table_level_permissions(self):
        for t in TABLES.values():
            self.assertIs(t["rowSecurity"], True)
            self.assertEqual(t["$permissions"], [])

    def test_history_row_is_redacted_by_construction(self):
        """Edge case: a teacher whose assignment ended keeps child first name and class only."""
        self.assertEqual(
            set(cols("teacher_session_history")),
            {"teacher_id", "session_id", "session_log_id", "child_first_name", "class_level", "session_date", "duration_minutes"},
        )
        for banned in ("child_id", "name", "date_of_birth", "school", "note", "homework", "parent_id", "lat_e6"):
            self.assertNotIn(banned, cols("teacher_session_history"))
        self.assertEqual(cols("teacher_session_history")["child_first_name"]["size"], 60)

    def test_history_is_unique_per_session_and_indexed_by_teacher_and_date(self):
        idx = {i["key"]: i for i in TABLES["teacher_session_history"]["indexes"]}
        self.assertEqual(idx["unique_session"]["type"], "unique")
        self.assertEqual(idx["unique_session"]["columns"], ["session_id"])
        self.assertEqual(idx["by_teacher_date"]["columns"], ["teacher_id", "session_date"])

    def test_access_window_finds_due_rows_by_index_and_is_unique_per_teacher_and_child(self):
        idx = {i["key"]: i for i in TABLES["access_window"]["indexes"]}
        self.assertEqual(idx["by_due"]["columns"], ["state", "expires_at"])
        self.assertEqual(idx["unique_teacher_child"]["type"], "unique")
        self.assertEqual(cols("access_window")["state"]["elements"], ["open", "closed"])

    def test_foreign_keys_are_declared_and_instants_are_utc_datetimes(self):
        rel = {r["from"] for r in DOMAIN["relations"]}
        self.assertEqual(rel, {"access_window.teacher_id", "access_window.child_id", "teacher_session_history.teacher_id",
                               "teacher_session_history.session_id", "teacher_session_history.session_log_id"})
        self.assertEqual(cols("access_window")["expires_at"]["type"], "datetime")
        self.assertEqual(cols("teacher_session_history")["session_date"]["size"], 10)  # ISO calendar date

    def test_no_personal_data_defaults_and_notes_state_the_purpose(self):
        for t in TABLES.values():
            for c in t["columns"]:
                self.assertNotIn("default", c)
        for name in TABLES:
            self.assertIn("core", DOMAIN["meta"][name]["note"].lower())

    def test_validator_rejects_a_client_write_permission_on_these_tables(self):
        domains = copy.deepcopy(DOMAINS)
        d = next(x for x in domains if x["domain"] == "permissions")
        d["tables"][0]["$permissions"] = ['update("users")']
        self.assertTrue(sb.validate(domains))


class Migration(unittest.TestCase):
    MIGRATIONS = str(APPWRITE_DIR / "migrations")

    def version(self):
        names = sorted(p.name for p in (APPWRITE_DIR / "migrations").glob("*_permissions_schema.py"))
        self.assertEqual(len(names), 1)
        return int(names[0][:4])

    def run_migrations(self, client):
        out = io.StringIO()
        return migrate.run(client, "staging", self.MIGRATIONS, out=out, sleep=lambda s: None), out.getvalue()

    def test_version_is_unique(self):
        versions = [int(p.name[:4]) for p in (APPWRITE_DIR / "migrations").glob("[0-9]*.py")]
        self.assertEqual(versions.count(self.version()), 1)

    def test_applies_to_a_fresh_project_and_rerun_is_a_noop(self):
        client = FakeAppwriteClient()
        result, out = self.run_migrations(client)
        self.assertEqual(result.exit_code, 0, out)
        self.assertIn(self.version(), result.applied)
        for tid, t in TABLES.items():
            self.assertEqual(set(client.tables[tid]["columns"]), set(cols(tid)))
            self.assertEqual(set(client.tables[tid]["indexes"]), {i["key"] for i in t["indexes"]})
        before = (copy.deepcopy(client.tables), len(client.log))
        result, _ = self.run_migrations(client)
        self.assertEqual(result.applied, [])
        self.assertEqual((client.tables, len(client.log)), before)

    def test_inserts_no_rows(self):
        client = FakeAppwriteClient()
        self.run_migrations(client)
        self.assertFalse([entry for entry in client.log if "row" in str(entry).lower() and "create" in str(entry).lower()])


if __name__ == "__main__":
    unittest.main()
