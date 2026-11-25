"""X01 (#85): geo domain (travel-time cache + usage tables) follows the schema convention."""

import copy
import json
import unittest

from helpers import APPWRITE_DIR

import migrate
import schema_build as sb
from fake_client import FakeAppwriteClient

GEO = json.loads((APPWRITE_DIR / "schema" / "geo.json").read_text())
TABLES = {t["$id"]: t for t in GEO["tables"]}


class GeoSchema(unittest.TestCase):
    def test_domain_validates_with_the_shared_validator(self):
        self.assertEqual(sb.validate(sb.load_domains()), [])

    def test_tables_and_hard_deletion_policy(self):
        self.assertEqual(set(TABLES), {"travel_time_cache", "travel_usage"})
        for tid in TABLES:
            self.assertEqual(GEO["meta"][tid]["deletion"], "hard")  # derived data, no soft-delete keys

    def test_cache_row_has_last_good_value_and_negative_cache_columns_and_no_coordinates(self):
        cols = {c["key"] for c in TABLES["travel_time_cache"]["columns"]}
        self.assertTrue(
            {"origin_zone_id", "dest_zone_id", "band", "minutes", "fetched_at", "expires_at", "retry_at", "failure_reason", "fail_count"} <= cols
        )
        self.assertFalse([c for c in cols if "lat" in c or "lng" in c])

    def test_zone_foreign_keys_are_declared_and_indexed(self):
        rels = {r["from"] for r in GEO["relations"]}
        self.assertEqual(rels, {"travel_time_cache.origin_zone_id", "travel_time_cache.dest_zone_id"})
        first = {i["columns"][0] for i in TABLES["travel_time_cache"]["indexes"]}
        self.assertTrue({"origin_zone_id", "dest_zone_id"} <= first)

    def test_unique_pair_and_period_keys(self):
        uniq = lambda t: {tuple(i["columns"]) for i in TABLES[t]["indexes"] if i["type"] == "unique"}  # noqa: E731
        self.assertEqual(uniq("travel_time_cache"), {("pair_key",)})
        self.assertEqual(uniq("travel_usage"), {("period_key",)})

    def test_migration_applies_to_a_fresh_project_and_rerun_is_a_noop(self):
        client = FakeAppwriteClient()
        sb.apply_domain(client, "core")
        sb.apply_domain(client, "geo")
        for tid, t in TABLES.items():
            self.assertEqual(set(client.tables[tid]["columns"]), {c["key"] for c in t["columns"]})
        before = (copy.deepcopy(client.tables), copy.deepcopy(client.specs))
        sb.apply_domain(client, "geo")
        self.assertEqual((client.tables, client.specs), before)

    def test_a_geo_migration_script_exists_and_only_applies_the_geo_domain(self):
        files = sorted((APPWRITE_DIR / "migrations").glob("*_geo_schema.py"))
        self.assertEqual(len(files), 1)
        self.assertIn('apply_domain(ctx.client, "geo")', files[0].read_text())
        self.assertIsNotNone(migrate)


if __name__ == "__main__":
    unittest.main()
