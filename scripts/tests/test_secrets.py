"""F09 (#35) tests: secrets inventory, config matrix, .env.example, leak handling.

Run: python3 -m unittest discover -s scripts/tests -v   (stdlib only, offline).
Each test names the acceptance criterion / edge case of issue #35 it proves.
"""

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import secrets_check as sc

INV_PATH = ROOT / "docs/ops/secrets-inventory.json"
ENVS = ("development", "staging", "production")


def inv():
    return copy.deepcopy(json.loads(INV_PATH.read_text()))


def errs(i, **kw):
    return sc.validate(i, ROOT, **kw)


def entry(i, name, consumer=None):
    for e in i["entries"]:
        if e["name"] == name and (consumer is None or e["consumer"] == consumer):
            return e
    raise KeyError(name)


class RealInventory(unittest.TestCase):
    def test_real_inventory_is_valid(self):
        self.assertEqual(errs(inv()), [])

    def test_cli_passes_on_repo(self):
        r = subprocess.run(
            [sys.executable, str(ROOT / "scripts/secrets_check.py")],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_inventory_covers_required_platform_secrets(self):
        names = {e["name"] for e in inv()["entries"]}
        for need in (
            "APPWRITE_API_KEY", "JOB_TICK_SECRET", "MOMO_COLLECTION_API_KEY", "MOMO_COLLECTION_SUBSCRIPTION_KEY",
            "MOMO_DISBURSEMENT_API_KEY", "MOMO_DISBURSEMENT_SUBSCRIPTION_KEY", "AIRTEL_CLIENT_SECRET",
            "CARD_GATEWAY_SECRET_KEY", "FCM_SERVICE_ACCOUNT_JSON", "APNS_AUTH_KEY", "RESEND_API_KEY",
            "AFRICASTALKING_API_KEY", "GOOGLE_MAPS_SERVER_KEY", "SENTRY_DSN", "FLY_API_TOKEN",
        ):  # fmt: skip
            self.assertIn(need, names)

    def test_every_server_secret_has_full_rotation_record(self):
        i = inv()
        for e in i["entries"]:
            if e["kind"] == "server_secret":
                steps = sc.resolve_procedure(i, e)
                self.assertGreaterEqual(len(steps), 3, e["name"])
                for f in ("rotation_owner", "rotation_interval_days", "blast_radius"):
                    self.assertTrue(e.get(f), f"{e['name']} {f}")

    def test_appwrite_keys_distinct_per_consumer_and_environment(
        self,
    ):  # SR 4, F02 keys.md
        i = inv()
        for consumer in ("core-service", "ci-deploy"):
            e = entry(i, "APPWRITE_API_KEY", consumer)
            self.assertEqual(set(e["environments"]), set(ENVS))
            self.assertEqual(len(set(e["stores"].values())), 3)


class Validator(unittest.TestCase):
    def assertFails(self, i, fragment, **kw):
        out = errs(i, **kw)
        self.assertTrue(any(fragment in e for e in out), f"{fragment!r} not in {out}")

    # SR 4: "Production secrets are never available to development"
    def test_live_value_in_non_production_rejected(self):
        i = inv()
        entry(i, "MOMO_COLLECTION_API_KEY")["value_class"]["staging"] = "live"
        self.assertFails(i, "live value outside production")

    def test_production_store_in_dev_consumer_rejected(self):
        i = inv()
        e = entry(i, "APPWRITE_API_KEY", "core-service")
        e["stores"]["development"] = e["stores"]["production"]
        self.assertFails(i, "store")

    def test_dev_only_consumer_with_production_scope_rejected(self):
        i = inv()
        i["entries"].append(
            {**entry(i, "RESEND_API_KEY"), "name": "X_KEY", "consumer": "local-dev", "environments": ["production"],
             "stores": {"production": "local:.env-production"}}
        )  # fmt: skip
        self.assertFails(i, "consumer local-dev may not hold production")

    # Edge: shared dev key
    def test_shared_key_between_environments_detected(self):
        i = inv()
        e = entry(i, "RESEND_API_KEY")
        e["stores"]["staging"] = e["stores"]["development"]
        self.assertFails(i, "shared")

    def test_server_secret_must_declare_distinct_value_per_environment(self):
        i = inv()
        entry(i, "RESEND_API_KEY")["value_per_environment"] = False
        self.assertFails(i, "value_per_environment")

    # AC1: no secrets in app bundles
    def test_server_secret_in_app_bundle_rejected(self):
        i = inv()
        entry(i, "RESEND_API_KEY")["location"] = "build_define"
        self.assertFails(i, "app bundle")

    def test_public_client_key_needs_restriction(self):
        i = inv()
        entry(i, "GOOGLE_MAPS_ANDROID_KEY")["restriction"] = ""
        self.assertFails(i, "restriction")

    def test_public_key_outside_build_define_rejected(self):
        i = inv()
        entry(i, "GOOGLE_MAPS_ANDROID_KEY")["location"] = "fly_secret"
        self.assertFails(i, "build_define")

    def test_secret_like_name_marked_non_secret_rejected(self):
        i = inv()
        e = entry(i, "APPWRITE_PROJECT_ID")
        e["name"] = "SOMETHING_SECRET"
        self.assertFails(i, "looks secret")

    # Rotation
    def test_missing_rotation_procedure_rejected(self):
        i = inv()
        entry(i, "RESEND_API_KEY")["procedure"] = "nope"
        self.assertFails(i, "procedure")

    def test_short_procedure_rejected(self):
        i = inv()
        i["procedures"]["provider-key"] = ["one step"]
        self.assertFails(i, "at least 3 steps")

    def test_no_overlap_requires_downtime_note(
        self,
    ):  # edge: rotate within an hour without downtime
        i = inv()
        e = entry(i, "RESEND_API_KEY")
        e["rotation_overlap"] = False
        e.pop("downtime_note", None)
        self.assertFails(i, "downtime_note")

    def test_rotation_interval_bounds(self):
        i = inv()
        entry(i, "RESEND_API_KEY")["rotation_interval_days"] = 0
        self.assertFails(i, "rotation_interval_days")
        entry(i, "RESEND_API_KEY")["rotation_interval_days"] = 4000
        self.assertFails(i, "rotation_interval_days")

    def test_appwrite_interval_not_longer_than_keys_md_expiry(self):
        i = inv()
        entry(i, "APPWRITE_API_KEY", "core-service")["rotation_interval_days"] = 180
        self.assertFails(i, "keys.md")

    def test_unknown_consumer_and_duplicate_entries_rejected(self):
        i = inv()
        entry(i, "RESEND_API_KEY")["consumer"] = "ghost"
        self.assertFails(i, "unknown consumer")
        j = inv()
        j["entries"].append(copy.deepcopy(j["entries"][0]))
        self.assertFails(j, "duplicate")

    def test_inventory_never_contains_secret_values(self):
        i = inv()
        entry(i, "RESEND_API_KEY")["notes"] = "key is standard_" + "a" * 64
        self.assertFails(i, "secret value")


class ConfigCoverage(unittest.TestCase):
    def test_core_settings_fields_found(self):
        f = sc.core_settings_fields(ROOT)
        self.assertIn("APPWRITE_API_KEY", f)
        self.assertIn("HS_ENV", f)
        self.assertTrue(f["APPWRITE_API_KEY"], "SecretStr field flagged secret")
        self.assertFalse(f["APPWRITE_ENDPOINT"])

    def test_every_core_setting_is_in_inventory(self):
        i = inv()
        i["entries"] = [e for e in i["entries"] if e["name"] != "LOG_LEVEL"]
        self.assertTrue(any("LOG_LEVEL" in e for e in errs(i)))

    def test_secretstr_setting_must_be_server_secret(self):
        i = inv()
        e = entry(i, "APPWRITE_API_KEY", "core-service")
        e["kind"] = "non_secret"
        self.assertTrue(any("SecretStr" in x for x in errs(i)))

    def test_app_sources_scanned_for_config_keys(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "apps/admin/src").mkdir(parents=True)
            (root / "apps/admin/src/a.ts").write_text(
                "const a = process.env.NEXT_PUBLIC_NEW_THING;\n"
            )
            (root / "apps/parent/lib").mkdir(parents=True)
            (root / "apps/parent/lib/a.dart").write_text(
                "const k = String.fromEnvironment('MAPS_X');\n"
            )
            self.assertEqual(
                sc.app_config_keys(root), {"NEXT_PUBLIC_NEW_THING", "MAPS_X"}
            )

    def test_unlisted_app_key_flagged_and_public_secretlike_flagged(self):
        i = inv()
        out = sc.validate(
            i, ROOT, extra_app_keys={"NEXT_PUBLIC_ADMIN_API_KEY", "BRAND_NEW"}
        )
        self.assertTrue(any("BRAND_NEW" in e for e in out))
        self.assertTrue(any("NEXT_PUBLIC_ADMIN_API_KEY" in e for e in out))


class EnvExample(unittest.TestCase):
    def test_core_env_example_complete_and_empty(self):
        self.assertEqual(sc.check_env_example(ROOT), [])

    def test_missing_nonempty_and_extra_variables_flagged(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "services/core/src/hs_core").mkdir(parents=True)
            (root / "services/core/src/hs_core/settings.py").write_text(
                "class Settings:\n    hs_env: str = 'dev'\n    appwrite_api_key: SecretStr = SecretStr('')\n"
                "    log_level: str = 'INFO'\n"
            )
            (root / "services/core/.env.example").write_text(
                "HS_ENV=dev\nAPPWRITE_API_KEY=\nGHOST=\n"
            )
            out = sc.check_env_example(root)
            joined = "\n".join(out)
            self.assertIn("LOG_LEVEL missing", joined)
            self.assertIn("HS_ENV must be empty", joined)
            self.assertIn("GHOST", joined)
            self.assertNotIn("dev", joined.replace("HS_ENV", ""))  # values never echoed


class Docs(unittest.TestCase):
    def test_generated_docs_in_sync(self):
        self.assertEqual(sc.check_docs(ROOT, inv()), [])

    def test_drift_detected(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            for p in ("docs/ops/config-matrix.md", "docs/runbooks/secrets.md"):
                (root / p).parent.mkdir(parents=True, exist_ok=True)
                (root / p).write_text((ROOT / p).read_text())
            m = root / "docs/ops/config-matrix.md"
            m.write_text(
                m.read_text().replace("APPWRITE_ENDPOINT", "APPWRITE_ENDPOINT_X", 1)
            )
            self.assertTrue(any("out of date" in e for e in sc.check_docs(root, inv())))

    def test_runbook_has_leak_response_and_drill(self):
        text = (ROOT / "docs/runbooks/secrets.md").read_text().lower()
        for h in ("## leak response", "revoke", "rotate", "audit", "notify", "## rotation drill",
                  "committed to git", "within one hour", "without downtime"):  # fmt: skip
            self.assertIn(h, text)

    def test_missing_runbook_section_flagged(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "docs/runbooks").mkdir(parents=True)
            (root / "docs/runbooks/secrets.md").write_text("# nothing\n")
            (root / "docs/ops").mkdir(parents=True)
            (root / "docs/ops/config-matrix.md").write_text("x")
            out = sc.check_docs(root, inv())
            self.assertTrue(any("Leak response" in e for e in out))


class LeakedSecretScanning(unittest.TestCase):
    """Edge: a secret is accidentally committed -> scanner flags it, value never printed, runbook covers it."""

    def scan(self, content):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "oops.txt").write_text(content)
            return subprocess.run(
                [sys.executable, str(ROOT / "scripts/secret_scan.py"), "--path", d],
                check=False,
                capture_output=True,
                text=True,
            )

    def test_provider_keys_flagged_without_echo(self):
        samples = {
            "appwrite": "k = 'standard_" + "ab12" * 20 + "'",
            "google": "k = 'AIza" + "Sy" + "A" * 33 + "'",
            "resend": "RESEND_KEY = re_" + "Ab3" * 10,
            "sentry": "dsn = https://" + "a1" * 16 + "@o12345.ingest.sentry.io/678",
            "fly": "tok = fo1_" + "Ab3" * 15,
            "africastalking": "k = atsk_" + "ab12" * 12,
        }
        for name, line in samples.items():
            r = self.scan(line + "\n")
            self.assertEqual(r.returncode, 1, name)
            value = line.split("'")[1] if "'" in line else line.split()[-1]
            self.assertNotIn(value, r.stdout + r.stderr, name)

    def test_clean_file_passes(self):
        self.assertEqual(self.scan("APPWRITE_API_KEY=\n").returncode, 0)

    def test_repo_is_clean(self):
        r = subprocess.run(
            [sys.executable, str(ROOT / "scripts/secret_scan.py")],
            check=False,
            capture_output=True,
            text=True,
            cwd=ROOT,
        )
        self.assertEqual(r.returncode, 0, r.stdout)

    def test_gitignore_blocks_env_files_but_not_example(self):
        text = (ROOT / ".gitignore").read_text()
        self.assertRegex(text, r"(?m)^\.env\.\*$")
        self.assertRegex(text, r"(?m)^!\.env\.example$")


if __name__ == "__main__":
    unittest.main()
