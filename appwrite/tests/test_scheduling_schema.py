"""S01 (#93): schema additions for session generation (sessions.json columns, scheduling.json tables).

Mapped to acceptance criteria in memory.md (S01 block).
"""

import copy
import io
import unittest

from helpers import APPWRITE_DIR

import migrate
import schema_build as sb
import session_rules as rules
from fake_client import FakeAppwriteClient, UniqueViolation

DOMAINS = sb.load_domains()
TABLES = {t["$id"]: t for d in DOMAINS for t in d["tables"]}
SCHED = next(d for d in DOMAINS if d["domain"] == "scheduling")

T0 = "2026-12-01T06:00:00.000+00:00"
T1 = "2026-12-01T07:00:00.000+00:00"


def cols(table):
    return {c["key"]: c for c in TABLES[table]["columns"]}


def idx(table):
    return {i["key"]: i for i in TABLES[table]["indexes"]}


def fresh():
    client = FakeAppwriteClient()
    for t in TABLES.values():
        client.ensure_table(t)
    return client


def session(**extra):
    row = {"child_id": "c1", "teacher_id": "t1", "subscription_id": "s1", "home_location_id": "h1",
           "scheduled_start_at": T0, "scheduled_end_at": T1, "status": "scheduled", "status_changed_at": T0,
           "assignment_id": "a1", "slot_ref": "2-480:2026-12-01"}
    row.update(extra)
    return rules.with_session_keys(row)


class SessionColumns(unittest.TestCase):
    def test_generation_identity_columns_exist(self):
        c = cols("session")
        self.assertEqual(c["slot_ref"]["type"], "varchar")
        self.assertFalse(c["slot_ref"]["required"])
        self.assertEqual(c["generation_key"]["type"], "varchar")
        self.assertEqual(c["is_trial"]["type"], "boolean")
        self.assertIs(c["is_trial"]["default"], False)
        self.assertEqual(c["overridden"]["type"], "boolean")
        self.assertIs(c["overridden"]["default"], False)

    def test_generation_key_is_unique_and_derived(self):
        i = idx("session")["unique_generation_key"]
        self.assertEqual((i["type"], i["columns"]), ("unique", ["generation_key"]))
        self.assertEqual(sb.soft_delete_patch(TABLES["session"], "x")["generation_key"], None)

    def test_same_generation_key_is_rejected_by_the_backstop(self):
        client = fresh()
        client.create_row("session", session(generation_key="a1:2-480:2026-12-01"))
        with self.assertRaises(UniqueViolation):
            client.create_row("session", session(generation_key="a1:2-480:2026-12-01", teacher_id="t2"))

    def test_non_generated_sessions_do_not_collide(self):  # substitutes and manual rows have no generation_key
        client = fresh()
        client.create_row("session", session(teacher_id="t1", generation_key=None))
        client.create_row("session", session(teacher_id="t2", generation_key=None))

    def test_soft_delete_releases_the_key_and_restore_must_recompute_it(self):
        client = fresh()
        a = client.create_row("session", session(generation_key="a1:k"))
        client.update_row("session", a["$id"], sb.soft_delete_patch(TABLES["session"], T1))
        b = client.create_row("session", session(generation_key="a1:k", teacher_id="t2"))
        self.assertTrue(b["$id"])

    def test_substitute_and_cancelled_rows_keep_their_generation_key(self):
        # the key is an identity, not a slot holder: only soft delete releases it (so a cancelled session is never regenerated)
        row = rules.with_session_keys(session(status="cancelled_by_parent_late", generation_key="k"))
        self.assertIsNone(row["teacher_slot_key"])
        self.assertEqual(row["generation_key"], "k")


class NewTables(unittest.TestCase):
    def test_domain_is_generated_and_validates(self):
        self.assertEqual(sb.validate(DOMAINS), [])
        self.assertEqual({t["$id"] for t in SCHED["tables"]}, {"teacher_schedule_lease", "scheduling_issue"})
        self.assertTrue((APPWRITE_DIR.parent / "docs" / "erd" / "scheduling.md").exists())

    def test_lease_table_has_fencing_columns(self):
        c = cols("teacher_schedule_lease")
        for k in ("teacher_id", "generation", "holder", "acquired_at", "expires_at", "released_at", "lease_key"):
            self.assertIn(k, c)
        self.assertTrue(c["generation"]["required"])
        self.assertEqual(idx("teacher_schedule_lease")["unique_lease_key"]["type"], "unique")
        self.assertEqual(idx("teacher_schedule_lease")["by_teacher_generation"]["columns"], ["teacher_id", "generation"])
        self.assertEqual(SCHED["meta"]["teacher_schedule_lease"]["deletion"], "hard")

    def test_lease_generation_is_claimed_once(self):
        client = fresh()
        row = {"teacher_id": "t1", "generation": 1, "holder": "w1", "acquired_at": T0, "expires_at": T1,
               "lease_key": "t1:1"}
        client.create_row("teacher_schedule_lease", row)
        with self.assertRaises(UniqueViolation):
            client.create_row("teacher_schedule_lease", dict(row, holder="w2"))

    def test_scheduling_issue_columns_and_open_key(self):
        c = cols("scheduling_issue")
        for k in ("assignment_id", "teacher_id", "session_id", "kind", "slot_ref", "detail", "status", "issue_key",
                  "resolved_at", "resolved_by_id", "deleted_at"):
            self.assertIn(k, c)
        self.assertEqual(set(c["kind"]["elements"]), {
            "teacher_conflict", "outside_availability", "holiday", "no_home_location", "no_slots", "slot_shortfall",
            "invalid_slot", "generation_error", "teacher_inactive"})
        self.assertEqual(set(c["status"]["elements"]), {"open", "resolved"})
        self.assertEqual(idx("scheduling_issue")["unique_issue_key"]["columns"], ["issue_key"])
        self.assertEqual(sb.soft_delete_patch(TABLES["scheduling_issue"], "x")["issue_key"], None)

    def test_open_issue_is_not_duplicated(self):
        client = fresh()
        row = {"assignment_id": "a1", "kind": "teacher_conflict", "status": "open", "issue_key": "teacher_conflict:a1:k"}
        client.create_row("scheduling_issue", row)
        with self.assertRaises(UniqueViolation):
            client.create_row("scheduling_issue", row)

    def test_every_foreign_key_is_declared(self):
        rel = {r["from"] for r in SCHED["relations"]}
        self.assertTrue({"teacher_schedule_lease.teacher_id", "scheduling_issue.assignment_id",
                         "scheduling_issue.teacher_id", "scheduling_issue.session_id"} <= rel)

    def test_no_client_permissions_and_row_security(self):
        for t in SCHED["tables"]:
            self.assertEqual(t["$permissions"], [])
            self.assertTrue(t["rowSecurity"])

    def test_validator_rejects_a_raw_unique_on_the_soft_issue_table(self):
        domains = copy.deepcopy(DOMAINS)
        d = next(x for x in domains if x["domain"] == "scheduling")
        t = next(x for x in d["tables"] if x["$id"] == "scheduling_issue")
        t["indexes"].append({"key": "bad", "type": "unique", "columns": ["assignment_id"], "orders": ["ASC"]})
        self.assertTrue(sb.validate(domains))


class Migration(unittest.TestCase):
    def run_all(self, client):
        out = io.StringIO()
        return migrate.run(client, "staging", str(APPWRITE_DIR / "migrations"), out=out, sleep=lambda s: None)

    def test_migration_exists_with_unique_version(self):
        names = sorted(p.name for p in (APPWRITE_DIR / "migrations").glob("*_session_generation.py"))
        self.assertEqual(len(names), 1)
        versions = [int(p.name[:4]) for p in (APPWRITE_DIR / "migrations").glob("[0-9]*.py")]
        self.assertEqual(versions.count(int(names[0][:4])), 1)

    def test_upgrade_adds_columns_to_an_existing_sessions_table_and_creates_tables(self):
        # a project that already ran the M02 migration with the OLD session spec
        old = copy.deepcopy(TABLES["session"])
        new_cols = {"slot_ref", "generation_key", "is_trial", "overridden"}
        old["columns"] = [c for c in old["columns"] if c["key"] not in new_cols]
        old["indexes"] = [i for i in old["indexes"] if i["key"] != "unique_generation_key"]
        client = FakeAppwriteClient()
        client.ensure_table(old)
        result = self.run_all(client)
        self.assertEqual(result.exit_code, 0)
        self.assertTrue(new_cols <= set(client.tables["session"]["columns"]))
        self.assertIn("unique_generation_key", client.tables["session"]["indexes"])
        self.assertTrue({"teacher_schedule_lease", "scheduling_issue"} <= set(client.tables))

    def test_rerun_is_a_noop(self):
        client = FakeAppwriteClient()
        self.run_all(client)
        before = (copy.deepcopy(client.tables), len(client.log))
        self.assertEqual(self.run_all(client).applied, [])
        self.assertEqual((client.tables, len(client.log)), before)


if __name__ == "__main__":
    unittest.main()
