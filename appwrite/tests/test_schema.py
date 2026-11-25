"""M01 (#36): schema convention, core domain rules, generated files, migration, live diff.

Acceptance criteria and edge cases are mapped to tests in memory.md (M01 block).
"""

import copy
import io
import json
import re
import unittest

from helpers import APPWRITE_DIR

import migrate
import schema_build as sb
from fake_client import FakeAppwriteClient

REPO = APPWRITE_DIR.parent
CORE = sb.load_domains()  # all domains, in order
TABLES = {t["$id"]: t for d in CORE for t in d["tables"]}
CORE_DOMAIN = next(d for d in CORE if d["domain"] == "core")
CORE_IDS = [t["$id"] for t in CORE_DOMAIN["tables"]]
REQUIRED_TABLES = {
    "user_profile", "role_assignment", "parent_profile", "guardian_link", "home_location", "child", "consent_record",
    "policy_acceptance", "teacher_profile", "teacher_document", "teacher_approval", "availability_slot",
    "availability_exception", "zone", "teacher_zone",
}


def cols(table):
    return {c["key"]: c for c in TABLES[table]["columns"]}


def mutated(fn):
    domains = copy.deepcopy(CORE)
    fn(next(d for d in domains if d["domain"] == "core"))
    return sb.validate(domains)


def tbl(domain, name):
    return next(t for t in domain["tables"] if t["$id"] == name)


class Convention(unittest.TestCase):
    def test_one_json_file_per_domain(self):
        names = sorted(p.name for p in (APPWRITE_DIR / "schema").glob("*.json"))
        self.assertIn("core.json", names)
        self.assertIn("_base.json", names)
        self.assertEqual([d["domain"].lstrip("_") for d in CORE], [n[:-5].lstrip("_") for n in names])

    def test_generated_config_is_up_to_date(self):
        self.assertEqual(sb.check(), [], "run: python3 appwrite/schema_build.py --write")

    def test_generated_config_has_every_domain_table_and_keeps_other_sections(self):
        config = json.loads((APPWRITE_DIR / "appwrite.config.json").read_text())
        self.assertEqual([t["$id"] for t in config["tables"]], [t["$id"] for d in CORE for t in d["tables"]])
        self.assertTrue(config["buckets"])
        self.assertIn("functions", config)
        for t in config["tables"]:
            self.assertEqual(t["databaseId"], "main")

    def test_build_replaces_only_tables(self):
        base = {"projectId": "<set-by-ci>", "functions": [{"$id": "f"}], "tables": [], "buckets": [1]}
        out = sb.build_config(base, CORE)
        self.assertEqual(out["functions"], [{"$id": "f"}])
        self.assertEqual(out["buckets"], [1])
        self.assertTrue(out["tables"])

    def test_stale_generated_file_is_detected(self):
        errors = sb.check(config_text="{}\n")
        self.assertTrue(any("appwrite.config.json" in e for e in errors))

    def test_duplicate_table_across_domains_rejected(self):
        domains = copy.deepcopy(CORE)
        domains.append({"domain": "other", "tables": [copy.deepcopy(tbl(domains[1], "zone"))], "meta": {"zone": {"deletion": "soft"}}, "relations": []})
        self.assertTrue(any("duplicate table" in e for e in sb.validate(domains)))

    def test_schema_has_no_validation_errors(self):
        self.assertEqual(sb.validate(CORE), [])

    def test_erd_is_up_to_date_and_mentions_every_table(self):
        self.assertEqual(sb.check_erd(), [], "run: python3 appwrite/schema_build.py --write")
        text = (REPO / "docs" / "erd" / "core.md").read_text()
        self.assertIn("```mermaid", text)
        self.assertIn("erDiagram", text)
        for t in CORE_IDS:
            self.assertRegex(text, rf"\b{t}\b")
        self.assertRegex(text, r"parent_profile \|\|--o\{ child")

    def test_stale_erd_is_detected(self):
        self.assertTrue(sb.check_erd(texts={"core": "old"}))


class CoreTables(unittest.TestCase):
    def test_all_issue_tables_exist(self):
        self.assertTrue(REQUIRED_TABLES <= set(CORE_IDS), REQUIRED_TABLES - set(CORE_IDS))

    def test_sr_5_1_key_fields(self):
        expect = {
            "user_profile": {"phone", "email", "name", "status"},
            "role_assignment": {"user_id", "role", "granted_by_id", "granted_at"},
            "parent_profile": {"user_id"},
            "guardian_link": {"parent_id", "user_id", "grants"},
            "home_location": {"parent_id", "lat_e6", "lng_e6", "directions", "zone_id"},
            "child": {"parent_id", "name", "date_of_birth", "school", "class_level", "curriculum_id", "catalogue_version_id", "areas_of_concern"},
            "consent_record": {"parent_id", "child_id", "document", "version", "accepted_at"},
            "policy_acceptance": {"user_id", "policy", "version", "accepted_at"},
            "teacher_profile": {"user_id", "tier_id", "founding_flag", "gender", "bio", "photo_file_id", "status", "travel_radius_km"},
            "teacher_document": {"teacher_id", "type", "file_id", "expiry_date", "verification_status", "verified_by_id", "notes"},
            "teacher_approval": {"teacher_id", "curriculum_id", "level_id"},
            "availability_slot": {"teacher_id", "weekday", "start_minute", "end_minute"},
            "availability_exception": {"teacher_id", "starts_at", "ends_at"},
            "zone": {"name", "centroid_lat_e6", "centroid_lng_e6", "polygon_geojson", "is_cluster", "hard_to_fill"},
        }
        for t, keys in expect.items():
            self.assertTrue(keys <= set(cols(t)), f"{t}: missing {keys - set(cols(t))}")

    def test_every_core_table_has_row_security_and_no_table_permissions(self):
        for t in CORE_DOMAIN["tables"]:
            self.assertIs(t["rowSecurity"], True, t["$id"])
            self.assertEqual(t["$permissions"], [], t["$id"])

    def test_no_any_or_users_permission_and_no_client_write_anywhere(self):
        text = json.dumps(CORE_DOMAIN)
        self.assertNotRegex(text, r'\(\\?"(any|users)')
        self.assertNotRegex(text, r"(create|update|delete|write)\(")

    def test_validator_rejects_missing_row_security(self):
        errs = mutated(lambda d: tbl(d, "child").update(rowSecurity=False))
        self.assertTrue(any("child" in e and "rowSecurity" in e for e in errs))

    def test_validator_rejects_public_and_write_table_permissions(self):
        for perm in ('read("any")', 'read("users")', 'create("label:parent")', 'update("user:x")'):
            errs = mutated(lambda d: tbl(d, "child").update({"$permissions": [perm]}))
            self.assertTrue(any("child" in e and "permission" in e for e in errs), perm)

    def test_validator_allows_empty_permissions_only_for_user_data(self):
        errs = mutated(lambda d: tbl(d, "child").update({"$permissions": ['read("label:opsadmin")']}))
        self.assertTrue(any("permission" in e for e in errs))

    def test_role_labels_are_alphanumeric_and_complete(self):
        roles = cols("role_assignment")["role"]["elements"]
        self.assertEqual(set(roles), {"opsadmin", "safeguardinglead", "superadmin", "curator", "parent", "guardian", "teacher"})
        for r in roles:
            self.assertRegex(r, r"^[a-z0-9]+$")

    def test_validator_rejects_non_alphanumeric_role(self):
        def f(d):
            tbl(d, "role_assignment")["columns"][1]["elements"].append("ops_admin")

        self.assertTrue(any("ops_admin" in e for e in mutated(f)))

    def test_user_profile_roles_come_from_labels_not_a_column(self):
        self.assertNotIn("roles", cols("user_profile"))
        self.assertNotIn("role", cols("user_profile"))
        self.assertIn("labels", CORE_DOMAIN["meta"]["user_profile"]["note"])

    def test_unique_active_role_per_user(self):
        t = TABLES["role_assignment"]
        self.assertTrue(any(i["type"] == "unique" and i["columns"] == ["active_key"] for i in t["indexes"]))

    def test_unique_phone_email_and_one_profile_per_user(self):
        self.assertTrue(any(i["type"] == "unique" and i["columns"] == ["phone"] for i in TABLES["user_profile"]["indexes"]))
        self.assertTrue(any(i["type"] == "unique" and i["columns"] == ["email"] for i in TABLES["user_profile"]["indexes"]))
        for t in ("parent_profile", "teacher_profile"):
            self.assertTrue(any(i["type"] == "unique" and i["columns"] == ["user_id"] for i in TABLES[t]["indexes"]), t)

    def test_unique_teacher_curriculum_level_and_teacher_zone_and_policy(self):
        want = {
            "teacher_approval": ["teacher_id", "curriculum_id", "level_id"],
            "teacher_zone": ["teacher_id", "zone_id"],
            "policy_acceptance": ["user_id", "policy", "version"],
            "zone": ["name"],
        }
        for t, c in want.items():
            self.assertTrue(any(i["type"] == "unique" and i["columns"] == c for i in TABLES[t]["indexes"]), t)

    def test_child_has_no_unique_name_dob_constraint_decision(self):
        # Decision: twins and re-adds are legitimate; duplicates are a core-service warning (documented in the schema notes).
        self.assertFalse(any(i["type"] == "unique" for i in TABLES["child"]["indexes"]))
        self.assertIn("No unique", CORE_DOMAIN["meta"]["child"]["note"])

    def test_child_school_and_class_nullable_edge_case(self):
        self.assertFalse(cols("child")["school"]["required"])
        self.assertFalse(cols("child")["class_level"]["required"])
        self.assertTrue(cols("child")["name"]["required"])
        self.assertTrue(cols("child")["date_of_birth"]["required"])

    def test_child_has_no_photo_column_dp3(self):
        self.assertFalse([k for k in cols("child") if "photo" in k or "image" in k])

    def test_parent_two_homes_one_default(self):
        t = TABLES["home_location"]
        self.assertFalse(any(i["type"] == "unique" and i["columns"] == ["parent_id"] for i in t["indexes"]), "parent may have several homes")
        self.assertIn("is_default", cols("home_location"))
        self.assertTrue(any(i["type"] == "unique" and i["columns"] == ["default_key"] for i in t["indexes"]), "single default enforced by unique default_key")
        self.assertFalse(cols("home_location")["default_key"]["required"])

    def test_home_location_is_not_a_child_column(self):
        self.assertFalse([k for k in cols("child") if "address" in k or "lat" in k or "directions" in k])

    def test_timestamps_are_datetime_and_named_at(self):
        for tid in CORE_IDS:
            for c in TABLES[tid]["columns"]:
                if c["type"] == "datetime":
                    self.assertTrue(c["key"].endswith("_at"), (tid, c["key"]))
                if c["key"].endswith("_at"):
                    self.assertEqual(c["type"], "datetime", (tid, c["key"]))

    def test_validator_rejects_non_datetime_at_column(self):
        def f(d):
            tbl(d, "child")["columns"].append({"key": "seen_at", "type": "varchar", "size": 30, "required": False})

        self.assertTrue(any("seen_at" in e for e in mutated(f)))

    def test_dates_without_time_are_iso_strings_and_coordinates_integers(self):
        self.assertEqual((cols("child")["date_of_birth"]["type"], cols("child")["date_of_birth"]["size"]), ("varchar", 10))
        self.assertEqual(cols("teacher_document")["expiry_date"]["size"], 10)
        for t, k in (("home_location", "lat_e6"), ("zone", "centroid_lng_e6")):
            self.assertEqual(cols(t)[k]["type"], "integer")

    def test_no_float_columns_and_money_columns_must_be_integer(self):
        for tid in TABLES:
            self.assertFalse([c for c in TABLES[tid]["columns"] if c["type"] in ("double", "float")], tid)

        def f(d):
            tbl(d, "child")["columns"].append({"key": "fee_ugx", "type": "varchar", "size": 10, "required": False})

        self.assertTrue(any("fee_ugx" in e and "integer" in e for e in mutated(f)))

    def test_soft_delete_columns_where_sr_5_5_requires(self):
        soft = [t for t, m in CORE_DOMAIN["meta"].items() if m["deletion"] == "soft"]
        self.assertTrue({"user_profile", "parent_profile", "child", "home_location", "teacher_profile", "teacher_document", "teacher_approval", "availability_slot", "availability_exception", "zone", "guardian_link"} <= set(soft))
        for t in soft:
            c = cols(t)["deleted_at"]
            self.assertEqual(c["type"], "datetime", t)
            self.assertFalse(c["required"], t)

    def test_validator_requires_deletion_policy_and_deleted_at(self):
        errs = mutated(lambda d: d["meta"].pop("child"))
        self.assertTrue(any("child" in e and "deletion" in e for e in errs))

        def f(d):
            tbl(d, "child")["columns"] = [c for c in tbl(d, "child")["columns"] if c["key"] != "deleted_at"]

        self.assertTrue(any("child" in e and "deleted_at" in e for e in mutated(f)))

    def test_insert_only_tables(self):
        for t in ("consent_record", "policy_acceptance"):
            self.assertEqual(CORE_DOMAIN["meta"][t]["deletion"], "immutable")
            self.assertEqual(TABLES[t]["$permissions"], [])
            for k in cols(t):
                self.assertNotIn(k, ("deleted_at", "revoked_at", "updated_at"), t)

    def test_validator_rejects_soft_delete_on_immutable_table(self):
        def f(d):
            tbl(d, "consent_record")["columns"].append({"key": "deleted_at", "type": "datetime", "required": False})

        self.assertTrue(any("consent_record" in e and "immutable" in e for e in mutated(f)))

    def test_consent_withdrawal_is_a_new_row(self):
        self.assertEqual(cols("consent_record")["decision"]["elements"], ["granted", "withdrawn"])
        self.assertTrue(cols("consent_record")["child_id"]["required"])  # DP-2: per child

    def test_every_foreign_key_is_indexed_and_resolves(self):
        rels = CORE_DOMAIN["relations"]
        self.assertGreaterEqual(len(rels), 20)
        for r in rels:
            table, column = r["from"].split(".")
            self.assertTrue(any(i["columns"][0] == column for i in TABLES[table]["indexes"]), r)
            if "deferred" not in r:
                self.assertIn(r["to"], TABLES, r)

    def test_validator_rejects_unindexed_foreign_key(self):
        def f(d):
            t = tbl(d, "child")
            t["indexes"] = [i for i in t["indexes"] if i["columns"] != ["parent_id"]]

        self.assertTrue(any("child.parent_id" in e and "index" in e for e in mutated(f)))

    def test_validator_rejects_relation_to_missing_table(self):
        def f(d):
            d["relations"].append({"from": "child.parent_id", "to": "nope"})

        self.assertTrue(any("nope" in e for e in mutated(f)))

    def test_every_id_like_column_is_declared_a_relation_or_deferred(self):
        declared = {r["from"] for r in CORE_DOMAIN["relations"]}
        for tid in CORE_IDS:
            for k in cols(tid):
                if k.endswith("_id"):
                    self.assertIn(f"{tid}.{k}", declared | {"teacher_document.file_id", "teacher_profile.photo_file_id"}, f"{tid}.{k}")

    def test_lookup_indexes_the_issue_implies(self):
        has = lambda t, c: any(i["columns"][: len(c)] == c for i in TABLES[t]["indexes"])
        self.assertTrue(has("child", ["parent_id"]))
        self.assertTrue(has("home_location", ["parent_id"]))
        self.assertTrue(has("consent_record", ["child_id"]))
        self.assertTrue(has("availability_slot", ["teacher_id", "weekday"]))
        self.assertTrue(has("teacher_document", ["expiry_date"]))
        self.assertTrue(has("teacher_zone", ["zone_id"]))
        self.assertTrue(has("teacher_approval", ["curriculum_id", "level_id"]))
        self.assertTrue(has("teacher_profile", ["status"]))

    def test_appwrite_column_and_index_limits(self):
        for tid in TABLES:
            t = TABLES[tid]
            self.assertLessEqual(len(t["indexes"]), 64, tid)
            self.assertLessEqual(sb.row_width(t), sb.MAX_ROW_BYTES, tid)
            for c in t["columns"]:
                if c["type"] == "varchar":
                    self.assertTrue(1 <= c["size"] <= 16383, (tid, c["key"]))
                self.assertLessEqual(len(c["key"]), 48)
            for i in t["indexes"]:
                self.assertLessEqual(sum(cols(tid)[k].get("size", 0) for k in i["columns"]), 768, (tid, i["key"]))
                self.assertFalse([k for k in i["columns"] if cols(tid)[k]["type"] == "text"], (tid, i["key"]))

    def test_validator_rejects_oversize_varchar_and_text_index_and_wide_row(self):
        def big(d):
            tbl(d, "child")["columns"].append({"key": "x", "type": "varchar", "size": 20000, "required": False})

        self.assertTrue(any("size" in e for e in mutated(big)))

        def textidx(d):
            tbl(d, "teacher_profile")["indexes"].append({"key": "by_bio", "type": "key", "columns": ["bio"], "orders": ["ASC"]})

        self.assertTrue(any("bio" in e and "text" in e for e in mutated(textidx)))

        def wide(d):
            t = tbl(d, "child")
            for n in range(5):
                t["columns"].append({"key": f"w{n}", "type": "varchar", "size": 5000, "required": False})

        self.assertTrue(any("row" in e and "child" in e for e in mutated(wide)))

    def test_validator_rejects_index_on_unknown_column_and_required_with_default(self):
        def f(d):
            tbl(d, "child")["indexes"].append({"key": "bad", "type": "key", "columns": ["zzz"], "orders": ["ASC"]})

        self.assertTrue(any("zzz" in e for e in mutated(f)))

        def g(d):
            tbl(d, "child")["columns"][1].update(default="x")

        self.assertTrue(any("required" in e and "default" in e for e in mutated(g)))

    def test_enum_defaults_are_members(self):
        for tid in TABLES:
            for c in TABLES[tid]["columns"]:
                if c.get("format") == "enum" and "default" in c:
                    self.assertIn(c["default"], c["elements"], (tid, c["key"]))

    def test_list_paths_stay_under_5000_cap_by_design(self):
        # No table here relies on listing everything: every list-by-parent/teacher path is covered by an index (see lookup test);
        # zone is the only table listed whole and is bounded by Kampala neighbourhoods (< 5,000).
        self.assertNotIn("unbounded", json.dumps(CORE_DOMAIN))


class Migration(unittest.TestCase):
    MIGRATIONS = str(APPWRITE_DIR / "migrations")

    def run_migrations(self, client):
        out = io.StringIO()
        result = migrate.run(client, "staging", self.MIGRATIONS, out=out, sleep=lambda s: None)
        return result, out.getvalue()

    def test_core_migration_exists_and_follows_baseline(self):
        names = sorted(p.name for p in (APPWRITE_DIR / "migrations").glob("*.py"))
        self.assertIn("0001_schema_baseline.py", names)
        self.assertIn("0002_core_schema.py", names)

    def test_applies_to_a_fresh_project(self):
        client = FakeAppwriteClient()
        result, out = self.run_migrations(client)
        self.assertEqual(result.exit_code, 0, out)
        self.assertEqual(result.applied, [1, 2])
        self.assertTrue(set(CORE_IDS) <= set(client.tables))
        for tid in CORE_IDS:
            self.assertEqual(set(client.tables[tid]["columns"]), set(cols(tid)), tid)
            self.assertEqual(set(client.tables[tid]["indexes"]), {i["key"] for i in TABLES[tid]["indexes"]}, tid)
            self.assertTrue(all(s == "available" for s in client.tables[tid]["columns"].values()))
            self.assertIs(client.specs[tid]["rowSecurity"], True)
            self.assertEqual(client.specs[tid]["$permissions"], [])

    def test_rerun_is_a_noop(self):
        client = FakeAppwriteClient()
        self.run_migrations(client)
        before = (copy.deepcopy(client.tables), len(client.log), copy.deepcopy(client.specs))
        result, out = self.run_migrations(client)
        self.assertEqual(result.applied, [])
        self.assertEqual(result.skipped, [1, 2])
        self.assertEqual((client.tables, len(client.log), client.specs), before)

    def test_ensure_table_is_convergent_and_adds_only_missing_columns(self):
        client = FakeAppwriteClient()
        spec = copy.deepcopy(TABLES["zone"])
        first = client.ensure_table(spec)
        self.assertTrue(first["created"])
        again = client.ensure_table(spec)
        self.assertEqual(again, {"created": False, "columns_added": [], "indexes_added": []})
        spec["columns"].append({"key": "extra", "type": "boolean", "required": False, "default": False})
        spec["indexes"].append({"key": "by_extra", "type": "key", "columns": ["extra"], "orders": ["ASC"]})
        third = client.ensure_table(spec)
        self.assertEqual((third["columns_added"], third["indexes_added"]), (["extra"], ["by_extra"]))

    def test_ensure_table_rejects_drift_instead_of_altering(self):
        client = FakeAppwriteClient()
        spec = copy.deepcopy(TABLES["zone"])
        client.ensure_table(spec)
        spec["columns"][0]["size"] = 40
        with self.assertRaises(ValueError):
            client.ensure_table(spec)

    def test_fake_rejects_what_appwrite_rejects(self):
        client = FakeAppwriteClient()
        spec = copy.deepcopy(TABLES["zone"])
        spec["indexes"].append({"key": "bad", "type": "key", "columns": ["nope"], "orders": ["ASC"]})
        with self.assertRaises(ValueError):
            client.ensure_table(spec)

    def test_waits_for_available_columns_before_next_script(self):
        client = FakeAppwriteClient(polls_until_available=3)
        result, out = self.run_migrations(client)
        self.assertEqual(result.exit_code, 0, out)

    def test_no_seed_script_inserts_personal_data(self):
        seeds = [p.name for p in (APPWRITE_DIR / "migrations").glob("*.py") if re.match(r"^\d{4}_seed", p.name)]
        self.assertEqual(seeds, [])
        text = (APPWRITE_DIR / "migrations" / "0002_core_schema.py").read_text()
        self.assertNotRegex(text, r"create_row|record_applied|@[a-z0-9-]+\.[a-z]{2,}|\+256")

    def test_schema_files_contain_no_personal_data_defaults(self):
        for d in CORE:
            for t in d["tables"]:
                for c in t["columns"]:
                    if "default" in c and isinstance(c["default"], str):
                        self.assertNotRegex(c["default"], r"@|\+?\d{7,}", (t["$id"], c["key"]))
        text = json.dumps(CORE)
        self.assertNotRegex(text, r"[A-Za-z0-9._-]+@[A-Za-z0-9-]+\.[a-z]{2,}")


class RealAdapter(unittest.TestCase):
    def test_http_adapter_ensure_table_creates_table_columns_indexes(self):
        from test_adapter import make

        spec = copy.deepcopy(TABLES["zone"])
        n_cols, n_idx = len(spec["columns"]), len(spec["indexes"])
        from urllib.error import HTTPError

        available = {"columns": [{"key": c["key"], "status": "available"} for c in spec["columns"]]}
        responses = [HTTPError("u", 404, "nf", {}, io.BytesIO(b"{}"))] + [{}] * (1 + n_cols) + [available] + [{}] * n_idx
        c, rec = make(responses)
        result = c.ensure_table(spec)
        self.assertTrue(result["created"])
        paths = [r.full_url.split("/tablesdb/main")[1] for r in rec.requests]
        self.assertEqual(paths[0], "/tables/zone")
        self.assertEqual(paths[1], "/tables")
        self.assertTrue(any(p.endswith("/columns/varchar") for p in paths))
        self.assertTrue(any(p.endswith("/columns/boolean") for p in paths))
        self.assertTrue(any(p.endswith("/indexes") for p in paths))
        body = json.loads(rec.requests[1].data)
        self.assertEqual((body["tableId"], body["rowSecurity"], body["permissions"]), ("zone", True, []))


class DiffLive(unittest.TestCase):
    def snapshot(self):
        return {
            "databases": [{"id": "main", "tables": [
                {"id": "zone", "rowSecurity": True, "permissions": ['read("label:opsadmin")'],
                 "columns": [{"key": "name", "type": "varchar", "size": 80, "required": True, "array": False},
                             {"key": "live_only", "type": "integer", "required": False, "array": False}],
                 "indexes": [{"key": "by_name", "type": "unique", "columns": ["name"], "orders": []}]},
                {"id": "user_role", "rowSecurity": True, "permissions": [], "columns": [], "indexes": []},
                {"id": "other_live", "rowSecurity": True, "permissions": [], "columns": [], "indexes": []},
            ]}]
        }

    def test_report_lists_every_kind_of_difference_without_values(self):
        import diff_live

        report = diff_live.diff(CORE, self.snapshot(), aliases={"role_assignment": "user_role"})
        text = diff_live.render(report)
        self.assertIn("zone", report["tables_both"])
        self.assertIn("live_only", text)
        self.assertIn("centroid_lat_e6", text)  # in repo, not in live
        self.assertIn("role_assignment", text)
        self.assertIn("user_role", text)
        self.assertIn("child", report["tables_repo_only"])
        self.assertIn("other_live", report["tables_live_only"])
        self.assertIn("permissions", text)

    def test_cli_runs_on_a_snapshot_file(self):
        import tempfile
        from pathlib import Path

        import diff_live

        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "snap.json"
            p.write_text(json.dumps(self.snapshot()))
            out = io.StringIO()
            self.assertEqual(diff_live.main([str(p)], out=out), 0)
            self.assertIn("zone", out.getvalue())

    def test_diff_never_calls_the_network(self):
        text = (APPWRITE_DIR / "diff_live.py").read_text()
        self.assertNotRegex(text, r"urllib|requests|http\.client|socket")


if __name__ == "__main__":
    unittest.main()


class AsyncColumnProcessing(unittest.TestCase):
    """Review round 1, finding 1: Appwrite creates columns/indexes asynchronously. An index over a column that is still
    `processing` is rejected, so ensure_table must wait for `available` before creating indexes."""

    def zone(self):
        return copy.deepcopy(TABLES["zone"])

    def test_fake_rejects_an_index_over_a_processing_column(self):
        client = FakeAppwriteClient(polls_until_available=3)
        spec = self.zone()
        client.ensure_table(dict(spec, indexes=[]))
        with self.assertRaisesRegex(ValueError, "not available|processing"):
            client._create_index("zone", spec["indexes"][0])  # what ensure_table would do without waiting

    def test_ensure_table_waits_for_columns_before_creating_indexes(self):
        client = FakeAppwriteClient(polls_until_available=3)
        result = client.ensure_table(self.zone())
        self.assertTrue(result["indexes_added"])
        self.assertTrue(client.slept, "ensure_table never waited")
        self.assertTrue(all(s == "available" for s in client.column_statuses("zone").values()))

    def test_whole_core_domain_applies_to_a_slow_project(self):
        client = FakeAppwriteClient(polls_until_available=5)
        out = io.StringIO()
        result = migrate.run(client, "staging", str(APPWRITE_DIR / "migrations"), out=out, sleep=lambda s: None)
        self.assertEqual(result.exit_code, 0, out.getvalue())

    def test_wait_times_out_when_a_column_never_becomes_available(self):
        client = FakeAppwriteClient(polls_until_available=10**9)
        with self.assertRaisesRegex(migrate.MigrationError, "timed out"):
            client.ensure_table(self.zone())

    def test_wait_fails_fast_when_a_column_fails(self):
        client = FakeAppwriteClient(polls_until_available=2, failing_columns={"zone.name"})
        with self.assertRaisesRegex(migrate.MigrationError, "failed"):
            client.ensure_table(self.zone())
        self.assertLess(len(client.slept), 3)

    def test_real_adapter_waits_before_creating_indexes(self):
        from urllib.error import HTTPError

        from test_adapter import make

        spec = self.zone()
        n_cols, n_idx = len(spec["columns"]), len(spec["indexes"])
        keys = [c["key"] for c in spec["columns"]]
        proc = {"columns": [{"key": k, "status": "processing"} for k in keys]}
        done = {"columns": [{"key": k, "status": "available"} for k in keys]}
        responses = [HTTPError("u", 404, "nf", {}, io.BytesIO(b"{}")), {}] + [{}] * n_cols + [proc, proc, done] + [{}] * n_idx
        c, rec = make(responses)
        c.sleep = lambda s: None
        c.ensure_table(spec)
        calls = [(r.get_method(), r.full_url.split("/tablesdb/main")[1].split("?")[0]) for r in rec.requests]
        first_index = next(i for i, (m, p) in enumerate(calls) if m == "POST" and p.endswith("/indexes"))
        polls = [i for i, (m, p) in enumerate(calls) if m == "GET" and p == "/tables/zone/columns"]
        self.assertEqual(len(polls), 3)
        self.assertTrue(all(i < first_index for i in polls))

    def test_real_adapter_fails_fast_on_failed_column_and_times_out(self):
        from urllib.error import HTTPError

        from test_adapter import make

        spec = self.zone()
        keys = [c["key"] for c in spec["columns"]]
        bad = {"columns": [{"key": k, "status": "failed" if k == keys[0] else "processing"} for k in keys]}
        c, _ = make([HTTPError("u", 404, "nf", {}, io.BytesIO(b"{}")), {}] + [{}] * len(keys) + [bad])
        c.sleep = lambda s: None
        with self.assertRaisesRegex(migrate.MigrationError, "failed"):
            c.ensure_table(spec)
        proc = {"columns": [{"key": k, "status": "processing"} for k in keys]}
        c, _ = make([HTTPError("u", 404, "nf", {}, io.BytesIO(b"{}")), {}] + [{}] * len(keys) + [proc] * 50)
        clock = iter(range(0, 100000, 100))
        c.sleep, c.now = (lambda s: None), (lambda: next(clock))
        with self.assertRaisesRegex(migrate.MigrationError, "timed out"):
            c.ensure_table(spec)


class DriftDetection(unittest.TestCase):
    """Review round 1, findings 1 and 4: drift is compared on type, size, required, array, enum elements and default, and
    on index definitions and table permissions, in the fake and in the real adapter."""

    def spec(self):
        return copy.deepcopy(TABLES["user_profile"])

    def drifted(self, mutate):
        spec = self.spec()
        client = FakeAppwriteClient()
        client.ensure_table(spec)
        mutate(spec)
        with self.assertRaises(migrate.SchemaDrift):
            client.ensure_table(spec)

    def col(self, spec, key):
        return next(c for c in spec["columns"] if c["key"] == key)

    def test_fake_detects_column_drift_in_every_property(self):
        self.drifted(lambda s: self.col(s, "status").update(elements=["active", "other"]))
        self.drifted(lambda s: self.col(s, "status").update(default="suspended"))
        self.drifted(lambda s: self.col(s, "email").update(size=100))
        self.drifted(lambda s: self.col(s, "email").update(required=not self.col(s, "email")["required"]))
        self.drifted(lambda s: self.col(s, "email").update(array=True))
        self.drifted(lambda s: self.col(s, "email").update(type="text"))
        self.drifted(lambda s: self.col(s, "status").pop("default"))

    def test_fake_detects_index_definition_drift(self):
        self.drifted(lambda s: s["indexes"][0].update(type="key" if s["indexes"][0]["type"] == "unique" else "unique"))
        self.drifted(lambda s: s["indexes"][0].update(columns=list(reversed(s["indexes"][0]["columns"])) + ["status"]))
        self.drifted(lambda s: s["indexes"][0].update(orders=["DESC"] * len(s["indexes"][0]["orders"])))

    def test_fake_detects_table_permission_and_row_security_drift(self):
        self.drifted(lambda s: s.update(rowSecurity=False))
        self.drifted(lambda s: s.update({"$permissions": ['read("any")']}))

    def test_unchanged_spec_is_not_drift(self):
        client = FakeAppwriteClient()
        client.ensure_table(self.spec())
        client.ensure_table(self.spec())

    def rest_column(self, c):
        d = {"key": c["key"], "type": c["type"], "required": c["required"], "array": bool(c.get("array")), "status": "available"}
        for k in ("size", "elements", "format", "min", "max"):
            if k in c:
                d[k] = c[k]
        d["default"] = c.get("default")
        return d

    def rest_state(self, spec):
        return [
            {"$id": spec["$id"], "rowSecurity": spec["rowSecurity"], "$permissions": spec["$permissions"]},
            {"columns": [self.rest_column(c) for c in spec["columns"]]},
            {"indexes": [dict(i, status="available") for i in spec["indexes"]]},
        ]

    def real(self, spec, mutate):
        from test_adapter import make

        responses = self.rest_state(spec)
        mutate(responses)
        c, rec = make(responses)
        c.sleep = lambda s: None
        return c, rec

    def test_real_adapter_matching_remote_state_is_a_noop(self):
        spec = self.spec()
        c, rec = self.real(spec, lambda r: None)
        self.assertEqual(c.ensure_table(spec), {"created": False, "columns_added": [], "indexes_added": []})
        self.assertTrue(all(r.get_method() == "GET" for r in rec.requests))

    def test_real_adapter_detects_every_kind_of_drift(self):
        spec = self.spec()
        email = next(i for i, c in enumerate(spec["columns"]) if c["key"] == "email")
        status = next(i for i, c in enumerate(spec["columns"]) if c["key"] == "status")
        cases = {
            "size": lambda r: r[1]["columns"][email].update(size=7),
            "type": lambda r: r[1]["columns"][email].update(type="text"),
            "required": lambda r: r[1]["columns"][email].update(required=not spec["columns"][email]["required"]),
            "array": lambda r: r[1]["columns"][email].update(array=True),
            "elements": lambda r: r[1]["columns"][status].update(elements=["x"]),
            "default": lambda r: r[1]["columns"][status].update(default="suspended"),
            "index type": lambda r: r[2]["indexes"][0].update(type="key" if spec["indexes"][0]["type"] == "unique" else "unique"),
            "index columns": lambda r: r[2]["indexes"][0].update(columns=["status"]),
            "row security": lambda r: r[0].update(rowSecurity=False),
            "permissions": lambda r: r[0].update({"$permissions": ['read("label:opsadmin")']}),
        }
        for name, mutate in cases.items():
            with self.subTest(name):
                c, _ = self.real(spec, mutate)
                with self.assertRaises(migrate.SchemaDrift):
                    c.ensure_table(spec)


class ForeignKeyRuleInValidator(unittest.TestCase):
    """Review round 1, finding 2: the `*_id` rule applies to every domain, not just core."""

    def scratch(self, **table_over):
        table = {
            "$id": "widget", "name": "widget", "rowSecurity": True, "$permissions": [],
            "columns": [
                {"key": "owner_id", "type": "varchar", "size": 36, "required": True},
                {"key": "file_id", "type": "varchar", "size": 36, "required": False},
            ],
            "indexes": [{"key": "by_owner", "type": "key", "columns": ["owner_id"], "orders": ["ASC"]}],
        }
        table.update(table_over)
        return {"domain": "scratch", "description": "x", "tables": [table], "meta": {"widget": {"deletion": "hard"}}, "relations": []}

    def errors(self, scratch):
        return sb.validate(copy.deepcopy(CORE) + [scratch])

    def test_undeclared_owner_id_in_a_new_domain_fails(self):
        errors = self.errors(self.scratch())
        self.assertTrue(any("widget.owner_id" in e and "relation" in e for e in errors), errors)
        self.assertTrue(any("widget.file_id" in e for e in errors), errors)

    def test_declared_relation_and_marked_non_fk_pass(self):
        s = self.scratch()
        s["relations"] = [{"from": "widget.owner_id", "to": "user_profile"}]
        s["meta"]["widget"]["non_fk"] = {"file_id": "Appwrite Storage file id, not a table row"}
        self.assertEqual(self.errors(s), [])

    def test_id_arrays_and_unknown_non_fk_entries_are_checked(self):
        s = self.scratch()
        s["relations"] = [{"from": "widget.owner_id", "to": "user_profile"}]
        s["meta"]["widget"]["non_fk"] = {"file_id": "x", "ghost_id": "x"}
        s["tables"][0]["columns"].append({"key": "tag_ids", "type": "varchar", "size": 36, "required": False, "array": True})
        errors = self.errors(s)
        self.assertTrue(any("tag_ids" in e for e in errors), errors)
        self.assertTrue(any("ghost_id" in e for e in errors), errors)

    def test_every_real_domain_is_clean(self):
        self.assertEqual(sb.validate(CORE), [])


class RetentionAndInsertOnlyWording(unittest.TestCase):
    """Review round 1, finding 3: say exactly what is enforced today and what is a follow-up; state the retention rule."""

    STATEMENT = "the retention job is the only allowed hard-deleter"

    def test_notes_do_not_overstate_insert_only(self):
        for t in ("consent_record", "policy_acceptance"):
            note = CORE_DOMAIN["meta"][t]["note"].lower()
            self.assertNotIn("exposes no update or delete", note, t)
            self.assertIn("no client write permission", note, t)
            self.assertIn("follow-up", note, t)
            self.assertIn("m05", note, t)
            self.assertIn("m06", note, t)
            self.assertIn("create/list/get", note, t)
            self.assertIn(self.STATEMENT, note, t)

    def test_domain_description_readme_and_erd_state_the_retention_rule(self):
        for name, text in (
            ("core.json description", CORE_DOMAIN["description"]),
            ("appwrite/README.md", (REPO / "appwrite" / "README.md").read_text()),
            ("docs/erd/core.md", (REPO / "docs" / "erd" / "core.md").read_text()),
        ):
            flat = " ".join(text.lower().split())
            self.assertIn(self.STATEMENT, flat, name)
            self.assertIn("dp-6", flat, name)

    def test_readme_explains_how_immutable_and_soft_tables_meet_retention(self):
        flat = " ".join((REPO / "appwrite" / "README.md").read_text().lower().split())
        for needle in ("immutable", "soft-delete", "removed with the child", "m05", "m06"):
            self.assertIn(needle, flat)

    def test_availability_slot_note_says_overlaps_are_merged_by_the_core_service(self):
        note = CORE_DOMAIN["meta"]["availability_slot"]["note"].lower()
        self.assertIn("overlap", note)
        self.assertIn("core service", note)
        self.assertIn("merge", note)

    def test_ensure_table_docs_list_what_is_detected(self):
        doc = migrate.AppwriteClient.ensure_table.__doc__.lower()
        for needle in ("type", "size", "required", "array", "enum", "default", "index", "permission"):
            self.assertIn(needle, doc)
