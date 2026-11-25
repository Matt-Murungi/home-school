"""H05 (#134): the two settings tables (domain `settings`). The runtime behaviour is tested in
services/core/tests/test_settings_*.py; this file pins the schema decisions and the validator."""

import copy
import io
import unittest

from helpers import APPWRITE_DIR

import migrate
import schema_build as sb
from fake_client import FakeAppwriteClient

DOMAINS = sb.load_domains()
DOMAIN = next(d for d in DOMAINS if d["domain"] == "settings")
TABLES = {t["$id"]: t for t in DOMAIN["tables"]}


def cols(table):
    return {c["key"]: c for c in TABLES[table]["columns"]}


def idx(table):
    return {i["key"]: i for i in TABLES[table]["indexes"]}


def mutated(fn):
    domains = copy.deepcopy(DOMAINS)
    fn(next(d for d in domains if d["domain"] == "settings"))
    return sb.validate(domains)


class Tables(unittest.TestCase):
    def test_exactly_two_tables_and_the_domain_validates(self):
        self.assertEqual(set(TABLES), {"app_setting", "app_setting_change"})
        self.assertEqual(sb.validate(DOMAINS), [])

    def test_row_security_on_and_no_table_level_permissions(self):
        for t in TABLES.values():
            self.assertIs(t["rowSecurity"], True)
            self.assertEqual(t["$permissions"], [])

    def test_history_is_marked_append_only_and_immutable(self):
        m = DOMAIN["meta"]["app_setting_change"]
        self.assertIs(m["append_only"], True)
        self.assertEqual(m["deletion"], "immutable")
        for banned in ("deleted_at", "updated_at", "revoked_at"):
            self.assertNotIn(banned, cols("app_setting_change"))

    def test_history_note_states_the_limit_of_the_protection(self):
        note = DOMAIN["meta"]["app_setting_change"]["note"]
        for must in ("audited repository", "opt-in", "not enforcement by Appwrite", "appwrite/README.md"):
            self.assertIn(must, note)

    def test_history_revision_is_unique_per_setting_this_is_the_concurrency_claim(self):
        u = idx("app_setting_change")["unique_setting_revision"]
        self.assertEqual((u["type"], u["columns"]), ("unique", ["setting_key", "revision"]))

    def test_value_row_is_unique_per_setting(self):
        u = idx("app_setting")["unique_setting_key"]
        self.assertEqual((u["type"], u["columns"]), ("unique", ["setting_key"]))

    def test_actor_is_a_declared_foreign_key_with_an_index(self):
        self.assertIn("app_setting_change.actor_id", {r["from"] for r in DOMAIN["relations"]})
        self.assertEqual(idx("app_setting_change")["by_actor"]["columns"], ["actor_id"])

    def test_no_secret_looking_columns_and_values_are_bounded_text(self):
        for t in TABLES:
            for k in cols(t):
                for banned in ("secret", "password", "token", "api_key", "credential"):
                    self.assertNotIn(banned, k)
        for t, k in (("app_setting", "value"), ("app_setting_change", "value"),
                     ("app_setting_change", "previous_value"), ("app_setting", "pending_value")):
            self.assertEqual(cols(t)[k]["size"], 1000)
        self.assertEqual(cols("app_setting_change")["reason"]["size"], 500)

    def test_op_is_an_enum_of_the_three_operations(self):
        self.assertEqual(cols("app_setting_change")["op"]["elements"], ["set", "rollback", "cancel"])

    def test_notes_do_not_overclaim(self):
        text = " ".join(m["note"] for m in DOMAIN["meta"].values()).lower() + DOMAIN["description"].lower()
        for word in ("guarantee", "only way", "tamper-proof", "cannot be changed"):
            self.assertNotIn(word, text)

    def test_value_row_has_no_soft_delete_and_is_never_deleted(self):
        self.assertEqual(DOMAIN["meta"]["app_setting"]["deletion"], "hard")
        self.assertIn("never deleted", DOMAIN["meta"]["app_setting"]["note"])


class ValidatorMutations(unittest.TestCase):
    def test_a_client_write_permission_fails(self):
        self.assertTrue(mutated(lambda d: d["tables"][0].update({"$permissions": ['update("users")']})))

    def test_row_security_off_fails(self):
        self.assertTrue(mutated(lambda d: d["tables"][1].update({"rowSecurity": False})))

    def test_an_undeclared_actor_relation_fails(self):
        self.assertTrue(mutated(lambda d: d.update({"relations": []})))

    def test_a_missing_foreign_key_index_fails(self):
        def f(d):
            t = next(t for t in d["tables"] if t["$id"] == "app_setting_change")
            t["indexes"] = [i for i in t["indexes"] if i["key"] != "by_actor"]

        self.assertTrue(mutated(f))

    def test_an_updated_at_column_on_the_immutable_history_fails(self):
        def f(d):
            t = next(t for t in d["tables"] if t["$id"] == "app_setting_change")
            t["columns"].append({"key": "updated_at", "type": "datetime", "required": False})

        self.assertTrue(mutated(f))

    def test_a_float_column_fails(self):
        def f(d):
            d["tables"][0]["columns"].append({"key": "ratio", "type": "double", "required": False})

        self.assertTrue(mutated(f))


class Migration(unittest.TestCase):
    MIGRATIONS = str(APPWRITE_DIR / "migrations")

    def version(self):
        names = sorted(p.name for p in (APPWRITE_DIR / "migrations").glob("*_settings_schema.py"))
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
        self.assertFalse([e for e in client.log if "row" in str(e).lower() and "create" in str(e).lower()])


if __name__ == "__main__":
    unittest.main()
