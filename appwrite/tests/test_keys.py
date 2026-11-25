"""API-key scope manifest validation (appwrite/keys.md) (F02: scoped per consumer, fewest scopes)."""

import unittest
from pathlib import Path

from helpers import APPWRITE_DIR, make_dir  # noqa: F401

import check_keys

TABLE = """# keys
| Key | Consumer | Environment | Scopes | Expiry | Stored in |
|---|---|---|---|---|---|
{rows}
"""


def manifest(*rows):
    return TABLE.format(rows="\n".join(rows))


GOOD_CORE = "| hs-core-staging | core-service | staging | databases.read, tables.read, rows.read, rows.write, users.read, users.write, files.read, files.write, execution.write | 90d | Fly secret APPWRITE_API_KEY |"
GOOD_CI = "| hs-ci-staging | ci-deploy | staging | databases.read, databases.write, tables.read, tables.write, columns.read, columns.write, indexes.read, indexes.write, rows.read, rows.write, buckets.read, buckets.write, functions.read, functions.write | 90d | GitHub environment secret |"


class Keys(unittest.TestCase):
    def check(self, text):
        return check_keys.validate(text)

    def test_good_manifest_passes(self):
        self.assertEqual(self.check(manifest(GOOD_CORE, GOOD_CI)), [])

    def test_repo_manifest_passes(self):
        self.assertEqual(check_keys.validate((APPWRITE_DIR / "keys.md").read_text()), [])

    def test_repo_manifest_covers_every_environment_and_consumer(self):
        rows = check_keys.parse((APPWRITE_DIR / "keys.md").read_text())
        pairs = {(r["consumer"], r["environment"]) for r in rows}
        for env in ("development", "staging", "production"):
            for consumer in ("core-service", "ci-deploy"):
                self.assertIn((consumer, env), pairs)

    def test_unknown_scope_rejected(self):
        bad = GOOD_CORE.replace("rows.read", "rows.reed")
        self.assertTrue(any("unknown scope" in e for e in self.check(manifest(bad, GOOD_CI))))

    def test_wildcard_rejected(self):
        bad = GOOD_CORE.replace("users.read", "*")
        self.assertTrue(any("wildcard" in e for e in self.check(manifest(bad, GOOD_CI))))

    def test_core_service_may_not_hold_deploy_scopes(self):
        bad = GOOD_CORE.replace("execution.write", "columns.write")
        self.assertTrue(any("columns.write" in e and "core-service" in e for e in self.check(manifest(bad, GOOD_CI))))

    def test_ci_deploy_may_not_manage_users(self):
        bad = GOOD_CI.replace("rows.read", "users.write")
        self.assertTrue(any("users.write" in e and "ci-deploy" in e for e in self.check(manifest(GOOD_CORE, bad))))

    def test_unknown_consumer_rejected(self):
        bad = GOOD_CORE.replace("core-service", "admin-browser")
        self.assertTrue(any("consumer" in e for e in self.check(manifest(bad, GOOD_CI))))

    def test_unknown_environment_rejected(self):
        bad = GOOD_CORE.replace("| staging |", "| qa |")
        self.assertTrue(any("environment" in e for e in self.check(manifest(bad, GOOD_CI))))

    def test_expiry_required(self):
        bad = GOOD_CORE.replace("| 90d |", "| none |")
        self.assertTrue(any("expiry" in e for e in self.check(manifest(bad, GOOD_CI))))

    def test_key_value_in_manifest_rejected(self):
        bad = GOOD_CORE.replace("Fly secret APPWRITE_API_KEY", "standard_" + "a1" * 40)
        self.assertTrue(any("secret" in e.lower() for e in self.check(manifest(bad, GOOD_CI))))

    def test_stored_in_must_name_a_secrets_store(self):
        bad = GOOD_CORE.replace("Fly secret APPWRITE_API_KEY", "repo file")
        self.assertTrue(any("stored in" in e for e in self.check(manifest(bad, GOOD_CI))))

    def test_duplicate_consumer_env_rejected(self):
        self.assertTrue(any("duplicate" in e for e in self.check(manifest(GOOD_CORE, GOOD_CORE, GOOD_CI))))

    def test_empty_table_rejected(self):
        self.assertTrue(self.check("# nothing\n"))

    def test_cli_exit_codes(self):
        tmp, d = make_dir()
        self.addCleanup(tmp.cleanup)
        p = d / "keys.md"
        p.write_text(manifest(GOOD_CORE, GOOD_CI))
        self.assertEqual(check_keys.main([str(p)]), 0)
        p.write_text(manifest(GOOD_CORE.replace("rows.read", "nope")))
        self.assertEqual(check_keys.main([str(p)]), 1)


if __name__ == "__main__":
    unittest.main()
