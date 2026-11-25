"""A01 (#43): appwrite/schema/auth.json (otp_challenge) follows the M01 convention."""

import json
import unittest

from helpers import APPWRITE_DIR

import migrate
import schema_build as sb
from fake_client import FakeAppwriteClient

DOMAINS = sb.load_domains()
AUTH = next((d for d in DOMAINS if d["domain"] == "auth"), None)


class AuthSchema(unittest.TestCase):
    def test_domain_file_exists_and_validates(self):
        self.assertIsNotNone(AUTH)
        self.assertEqual(sb.validate(DOMAINS), [])

    def test_otp_challenge_table_shape(self):
        t = next(t for t in AUTH["tables"] if t["$id"] == "otp_challenge")
        cols = {c["key"]: c for c in t["columns"]}
        for k in ("kind", "challenge_id", "bucket", "phone_hash", "code_hash", "salt", "app", "created_at", "expires_at"):
            self.assertIn(k, cols)
        self.assertEqual(cols["expires_at"]["type"], "datetime")
        self.assertTrue(cols["expires_at"]["required"])
        self.assertTrue(t["rowSecurity"])
        self.assertEqual(t["$permissions"], [])  # server-only: no client can read hashes

    def test_no_plaintext_phone_or_code_or_ip_columns(self):
        t = next(t for t in AUTH["tables"] if t["$id"] == "otp_challenge")
        names = {c["key"] for c in t["columns"]}
        self.assertFalse(names & {"phone", "code", "ip", "ip_address", "otp", "msisdn"})

    def test_indexes_support_the_core_queries_and_cleanup(self):
        t = next(t for t in AUTH["tables"] if t["$id"] == "otp_challenge")
        idx = {i["key"]: i["columns"] for i in t["indexes"]}
        self.assertIn(["bucket"], idx.values())
        self.assertIn(["phone_hash", "created_at"], idx.values())
        self.assertIn(["expires_at"], idx.values())

    def test_hard_delete_is_documented_because_rows_are_short_lived(self):
        self.assertEqual(AUTH["meta"]["otp_challenge"]["deletion"], "hard")

    def test_generated_files_are_fresh(self):
        self.assertEqual(sb.check(), [], "run: python3 appwrite/schema_build.py --write")
        self.assertTrue((APPWRITE_DIR.parent / "docs/erd/auth.md").exists())

    def test_migration_creates_the_table_and_is_idempotent(self):
        client = FakeAppwriteClient()
        sb.apply_domain(client, "auth")
        sb.apply_domain(client, "auth")
        self.assertIn("otp_challenge", client.tables)
        self.assertEqual(set(client.tables["otp_challenge"]["columns"]), {c["key"] for c in AUTH["tables"][0]["columns"]})

    def test_migration_file_exists_and_is_numbered_after_core(self):
        files = sorted(p.name for p in (APPWRITE_DIR / "migrations").glob("*.py"))
        mig = [f for f in files if "auth" in f]
        self.assertEqual(len(mig), 1)
        self.assertGreater(int(mig[0][:4]), 2)

    def test_config_json_has_the_table(self):
        config = json.loads((APPWRITE_DIR / "appwrite.config.json").read_text())
        self.assertIn("otp_challenge", [t["$id"] for t in config["tables"]])


if __name__ == "__main__":
    unittest.main()
