"""Config skeleton, environments file and secret hygiene (F02)."""

import json
import re
import subprocess
import sys
import unittest

from helpers import APPWRITE_DIR  # noqa: F401

REPO = APPWRITE_DIR.parent
CONFIG = json.loads((APPWRITE_DIR / "appwrite.config.json").read_text())
ENVS = json.loads((APPWRITE_DIR / "environments.example.json").read_text())


class Config(unittest.TestCase):
    def test_tables_follow_adr_0012(self):
        self.assertTrue(CONFIG["tablesDB"])
        for t in CONFIG["tables"]:
            self.assertTrue(t["rowSecurity"], t["$id"])
            self.assertEqual(t["$permissions"], [], f"{t['$id']}: table-level client permissions must be empty")
            self.assertTrue(t["columns"], t["$id"])
            for c in t["columns"]:
                self.assertTrue({"key", "type", "required"} <= set(c), c)
            keys = {c["key"] for c in t["columns"]}
            for i in t.get("indexes", []):
                self.assertTrue(set(i["columns"]) <= keys, i)

    def test_schema_migration_table_declared(self):
        t = next(t for t in CONFIG["tables"] if t["$id"] == "schema_migration")
        self.assertTrue({"version", "name", "checksum", "applied_at"} <= {c["key"] for c in t["columns"]})
        self.assertTrue(any(i["type"] == "unique" and i["columns"] == ["version"] for i in t["indexes"]))

    def test_sample_table_present_for_ci_sample_change(self):
        self.assertIn("sample_item", {t["$id"] for t in CONFIG["tables"]})

    def test_buckets_have_file_security_and_no_client_permissions(self):
        self.assertTrue(CONFIG["buckets"])
        for b in CONFIG["buckets"]:
            self.assertTrue(b["fileSecurity"], b["$id"])
            self.assertEqual(b["$permissions"], [], b["$id"])

    def test_no_any_or_users_write_permissions_anywhere(self):
        text = json.dumps(CONFIG)
        self.assertNotRegex(text, r'(create|update|delete)\(\\?"(any|users)')
        self.assertNotIn('read(\\"any\\")', text)

    def test_config_has_no_project_ids(self):
        # Project ids differ per environment and come from environments.json at deploy time.
        self.assertIn(CONFIG.get("projectId", ""), ("", "<set-by-ci>"))


class Environments(unittest.TestCase):
    def test_three_environments_frankfurt(self):
        self.assertEqual(set(ENVS["environments"]), {"development", "staging", "production"})
        for name, e in ENVS["environments"].items():
            self.assertEqual(e["endpoint"], "https://fra.cloud.appwrite.io/v1", name)
            self.assertRegex(e["projectId"], r"^<.*>$", f"{name}: placeholder only")
            self.assertEqual(e["databaseId"], "main")
            self.assertRegex(e["keyEnvVar"], r"^[A-Z_]+$")
            self.assertEqual(e["seedAllowed"], name != "production")

    def test_separate_key_variable_per_environment_and_no_values(self):
        names = [e["keyEnvVar"] for e in ENVS["environments"].values()]
        self.assertEqual(len(set(names)), 3)
        self.assertNotRegex(json.dumps(ENVS), r"standard_[0-9a-f]{20,}")


class NoSecretsCommitted(unittest.TestCase):
    KEYLIKE = re.compile(r"standard_[0-9a-f]{32,}|[A-Za-z0-9]{40,}")

    def test_secret_scan_clean_on_appwrite_dir(self):
        r = subprocess.run(
            [sys.executable, str(REPO / "scripts" / "secret_scan.py"), "--path", str(APPWRITE_DIR)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_no_key_like_value_in_appwrite_files(self):
        offenders = []
        for p in APPWRITE_DIR.rglob("*"):
            if not p.is_file() or "__pycache__" in p.parts or p.suffix == ".pyc" or p.name == "test_config.py":
                continue
            for n, line in enumerate(p.read_text(errors="ignore").splitlines(), 1):
                if self.KEYLIKE.search(line) and "tests" not in p.parts:
                    offenders.append(f"{p.relative_to(APPWRITE_DIR)}:{n}")
        self.assertEqual(offenders, [])

    def test_no_env_files_in_appwrite_dir(self):
        names = [p.name for p in APPWRITE_DIR.rglob(".env*") if p.name != ".env.example"]
        self.assertEqual(names, [])

    def test_real_adapter_never_reads_files_for_key(self):
        src = (APPWRITE_DIR / "appwrite_client.py").read_text()
        self.assertNotIn("workspace_env", src)
        self.assertNotRegex(src, r"open\(.*env")


if __name__ == "__main__":
    unittest.main()
