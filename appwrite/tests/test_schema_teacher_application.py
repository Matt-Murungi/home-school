"""V01 (#56): appwrite/schema/teacher_application.json follows the M01 convention."""

import json
import unittest

from helpers import APPWRITE_DIR

import schema_build as sb
from fake_client import FakeAppwriteClient

DOMAINS = sb.load_domains()
DOM = next((d for d in DOMAINS if d["domain"] == "teacher_application"), None)
TABLES = {t["$id"]: t for t in DOM["tables"]} if DOM else {}
PII_FREE_CLAIM = {"phone", "email", "name", "ip", "code"}


class TeacherApplicationSchema(unittest.TestCase):
    def test_domain_validates(self):
        self.assertIsNotNone(DOM)
        self.assertEqual(sb.validate(DOMAINS), [])

    def test_tables_exist_with_row_security_and_no_client_permissions(self):
        self.assertEqual(
            set(TABLES),
            {"teacher_application", "teacher_referee", "teacher_application_subject", "teacher_application_claim"},
        )
        for t in TABLES.values():
            self.assertTrue(t["rowSecurity"])
            self.assertEqual(t["$permissions"], [])

    def test_application_has_the_vers_1_fields_and_a_submit_state(self):
        cols = {c["key"]: c for c in TABLES["teacher_application"]["columns"]}
        for k in ("teacher_type", "registration_number", "highest_qualification", "institution",
                  "years_teaching", "current_school", "travel_radius_km", "content_json",
                  "submit_key", "submitted_at", "revision", "attempt"):
            self.assertIn(k, cols)
        self.assertIn("submitted", cols["status"]["elements"])
        self.assertFalse(cols["registration_number"]["required"])  # graduates have none
        self.assertEqual(cols["submitted_at"]["type"], "datetime")  # UTC instant

    def test_one_application_per_teacher_is_a_unique_index(self):
        idx = TABLES["teacher_application"]["indexes"]
        self.assertTrue(any(i["type"] == "unique" and i["columns"] == ["teacher_id"] for i in idx))

    def test_application_is_never_hard_deleted(self):
        self.assertEqual(DOM["meta"]["teacher_application"]["deletion"], "history")

    def test_no_national_id_number_column_anywhere(self):
        names = {c["key"] for t in TABLES.values() for c in t["columns"]}
        self.assertFalse({n for n in names if "national" in n or n in {"nin", "id_number"}})

    def test_referee_has_two_positions_and_consent_timestamp(self):
        cols = {c["key"]: c for c in TABLES["teacher_referee"]["columns"]}
        self.assertEqual((cols["position"]["min"], cols["position"]["max"]), (1, 2))
        self.assertTrue(cols["consent_at"]["required"])
        self.assertLessEqual(cols["phone"]["size"], 16)  # E.164

    def test_claims_hold_no_personal_data(self):
        names = {c["key"] for c in TABLES["teacher_application_claim"]["columns"]}
        self.assertFalse(names & PII_FREE_CLAIM)
        self.assertEqual(DOM["meta"]["teacher_application_claim"]["deletion"], "hard")

    def test_migration_applies_and_is_idempotent(self):
        client = FakeAppwriteClient()
        sb.apply_domain(client, "teacher_application")
        sb.apply_domain(client, "teacher_application")
        for name in TABLES:
            self.assertIn(name, client.tables)

    def test_migration_file_and_generated_files(self):
        files = sorted(p.name for p in (APPWRITE_DIR / "migrations").glob("*.py"))
        self.assertEqual(len([f for f in files if "teacher_application" in f]), 1)
        self.assertEqual(sb.check(), [], "run: python3 appwrite/schema_build.py --write")
        config = json.loads((APPWRITE_DIR / "appwrite.config.json").read_text())
        self.assertIn("teacher_application", [t["$id"] for t in config["tables"]])
        self.assertTrue((APPWRITE_DIR.parent / "docs/erd/teacher_application.md").exists())


if __name__ == "__main__":
    unittest.main()
