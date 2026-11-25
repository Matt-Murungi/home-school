"""Tests for docs/legal/check-processors.py (C04). Stdlib only, offline.

Run: python3 -m unittest discover -s docs/legal/tests   (from repo root)
"""
import copy
import importlib.util
import json
import re
import unittest
from pathlib import Path

LEGAL = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("check_processors", LEGAL / "check-processors.py")
cp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cp)

REG = json.loads((LEGAL / "processors.json").read_text())
MD = (LEGAL / "processor-register.md").read_text()


def inv(*rows):
    return {"entries": [{"name": n, "kind": k, "consumer": c} for n, k, c in rows]}


INV = inv(
    ("APPWRITE_API_KEY", "server_secret", "core-service"),
    ("RESEND_API_KEY", "server_secret", "core-service"),
    ("SENTRY_DSN", "server_secret", "core-service"),
    ("GOOGLE_MAPS_ANDROID_KEY", "public_client_key", "app-parent"),
    ("HS_ENV", "non_secret", "core-service"),
)


def broken(fn):
    r = copy.deepcopy(REG)
    fn(r)
    return r


class TestRealFiles(unittest.TestCase):
    def test_committed_register_is_valid(self):
        self.assertEqual(cp.validate(REG, None, MD), [])

    def test_committed_register_matches_secrets_inventory_if_present(self):
        p = LEGAL.parent / "ops" / "secrets-inventory.json"
        if not p.exists():
            self.skipTest("docs/ops/secrets-inventory.json not on this branch (F09)")
        self.assertEqual(cp.validate(REG, json.loads(p.read_text()), MD, check_stale=True), [])

    def test_synthetic_inventory_covered(self):
        self.assertEqual(cp.validate(REG, INV, MD), [])

    def test_cli_exit_codes(self):
        self.assertEqual(cp.main([str(LEGAL / "processors.json")]), 0)


class TestRequiredIntegrations(unittest.TestCase):
    def test_each_sr_section_9_integration_has_entry(self):
        ids = {p["id"] for p in REG["processors"]}
        for need in ["codemagic", "apple-app-store", "google-play", "appwrite-cloud", "fly-io", "resend", "africas-talking", "firebase-fcm-apns",
                     "google-maps", "sentry", "mtn-momo", "uptime-vendor", "github", "card-gateway"]:
            self.assertIn(need, ids)

    def test_removed_required_processor_fails(self):
        r = broken(lambda r: r["processors"].pop(
            [i for i, p in enumerate(r["processors"]) if p["id"] == "sentry"][0]))
        errs = cp.validate(r, None, MD)
        self.assertTrue(any("sentry" in e and "required" in e for e in errs), errs)


class TestInventoryCrossCheck(unittest.TestCase):
    def test_uncovered_secret_fails(self):
        i = inv(("NEWVENDOR_API_KEY", "server_secret", "core-service"))
        errs = cp.validate(REG, i, MD)
        self.assertTrue(any("NEWVENDOR_API_KEY" in e for e in errs), errs)

    def test_uncovered_public_client_key_fails(self):
        i = inv(("MAPBOX_PUBLIC_KEY", "public_client_key", "app-parent"))
        self.assertTrue(any("MAPBOX_PUBLIC_KEY" in e for e in cp.validate(REG, i, MD)))

    def test_non_secret_and_internal_ignored(self):
        i = inv(("HS_ENV", "non_secret", "core-service"))
        self.assertEqual(cp.validate(REG, i, MD), [])

    def test_stale_secret_name_in_register_fails(self):
        r = broken(lambda r: r["processors"][0]["secret_names"].append("GHOST_KEY"))
        errs = cp.validate(r, INV, MD, check_stale=True)
        self.assertTrue(any("GHOST_KEY" in e for e in errs), errs)

    def test_secret_claimed_by_two_processors_fails(self):
        def f(r):
            for p in r["processors"]:
                if p["id"] == "resend":
                    p["secret_names"].append("SENTRY_DSN")
        errs = cp.validate(broken(f), INV, MD)
        self.assertTrue(any("SENTRY_DSN" in e and "more than one" in e for e in errs), errs)


class TestFields(unittest.TestCase):
    def test_every_required_field_is_enforced(self):
        for field in cp.REQUIRED_FIELDS:
            def f(r, field=field):
                del r["processors"][0][field]
            errs = cp.validate(broken(f), None, MD)
            self.assertTrue(any(field in e for e in errs), (field, errs))

    def test_empty_values_fail(self):
        for field in ("purpose", "exit_procedure", "data_categories", "regions", "sub_processors_note"):
            def f(r, field=field):
                r["processors"][0][field] = [] if isinstance(r["processors"][0][field], list) else ""
            self.assertTrue(cp.validate(broken(f), None, MD), field)

    def test_unknown_data_category_fails(self):
        r = broken(lambda r: r["processors"][0]["data_categories"].append("vibes"))
        self.assertTrue(any("vibes" in e for e in cp.validate(r, None, MD)))

    def test_bad_agreement_status_fails(self):
        def f(r):
            r["processors"][0]["agreement_status"] = "probably fine"
        self.assertTrue(any("agreement_status" in e for e in cp.validate(broken(f), None, MD)))

    def test_duplicate_id_fails(self):
        r = broken(lambda r: r["processors"].append(copy.deepcopy(r["processors"][0])))
        self.assertTrue(any("duplicate" in e for e in cp.validate(r, None, MD)))

    def test_signed_requires_real_link_and_date(self):
        def f(r):
            p = r["processors"][0]
            p["agreement_status"] = "signed"
        errs = cp.validate(broken(f), None, MD)
        self.assertTrue(any("signed" in e for e in errs), errs)

    def test_signed_with_evidence_passes(self):
        def f(r):
            p = r["processors"][0]
            p["agreement_status"] = "signed"
            p["dpa_link"] = "https://example.invalid/dpa-evidence"
            p["signed_date"] = "2026-12-01"
        self.assertEqual(cp.validate(broken(f), None, MD), [])

    def test_risk_accepted_requires_reference(self):
        def f(r):
            r["processors"][0]["agreement_status"] = "risk-accepted"
        self.assertTrue(any("risk" in e for e in cp.validate(broken(f), None, MD)))

    def test_cross_border_requires_note(self):
        def f(r):
            for p in r["processors"]:
                if p["cross_border"]:
                    p["cross_border_note"] = ""
        self.assertTrue(any("cross_border_note" in e for e in cp.validate(broken(f), None, MD)))

    def test_every_entry_says_what_to_do_if_dpa_refused(self):
        for p in REG["processors"]:
            self.assertTrue(p["if_dpa_refused"].strip(), p["id"])

    def test_unverified_label_on_legal_claims(self):
        for p in REG["processors"]:
            self.assertIn("unverified", p["cross_border_note"].lower(), p["id"])


# Independent expectation: which processor must hold each inventory secret, derived from the
# secret's name prefix (the inventory's consumer is where it is *used*, not who holds the data).
PREFIX_TO_PROCESSOR = [
    ("APPWRITE_", "appwrite-cloud"), ("FLY_", "fly-io"), ("RESEND_", "resend"),
    ("AFRICASTALKING_", "africas-talking"), ("FCM_", "firebase-fcm-apns"), ("APNS_", "firebase-fcm-apns"),
    ("GOOGLE_MAPS_", "google-maps"), ("SENTRY_", "sentry"), ("MOMO_", "mtn-momo"),
    ("AIRTEL_", "airtel-money"), ("CARD_GATEWAY_", "card-gateway"), ("HS_HEARTBEAT", "uptime-vendor"),
    ("PLAY_", "google-play"), ("IOS_", "apple-app-store"), ("APP_STORE_", "apple-app-store"),
    ("CODEMAGIC_", "codemagic"),
]
FIRST_PARTY = {"JOB_TICK_SECRET", "ADMIN_COOKIE_SECRET", "OTP_PEPPER"}
REAL_INV = LEGAL.parent / "ops" / "secrets-inventory.json"


class TestDerivedMapping(unittest.TestCase):
    def setUp(self):
        if not REAL_INV.exists():
            self.skipTest("no real inventory")
        self.inv = json.loads(REAL_INV.read_text())

    def test_every_inventory_secret_maps_as_derived(self):
        held = {n: p["id"] for p in REG["processors"] for n in p["secret_names"]}
        fp = {x["name"] for x in REG["first_party_secrets"]}
        for e in self.inv["entries"]:
            if e["kind"] not in ("server_secret", "public_client_key"):
                continue
            n = e["name"]
            want = [pid for pre, pid in PREFIX_TO_PROCESSOR if n.startswith(pre)]
            if want:
                self.assertEqual(held.get(n), want[0], n)
                self.assertNotIn(n, fp, n)
            else:
                self.assertIn(n, FIRST_PARTY, f"{n}: no vendor prefix; first party?")
                self.assertIn(n, fp)
        self.assertEqual(fp, FIRST_PARTY)

    def test_first_party_cannot_hide_vendor_secret(self):
        for name, consumer in [("RESEND_API_KEY", "core-service"), ("PLAY_UPLOAD_KEYSTORE", "app-stores"),
                               ("CODEMAGIC_SECURE_VARIABLES", "codemagic"), ("NEWTHING", "firebase-console"),
                               ("NEWTHING2", "app-stores"), ("NEWTHING3", "codemagic")]:
            def f(r, name=name):
                for p in r["processors"]:
                    if name in p["secret_names"]:
                        p["secret_names"].remove(name)
                r["first_party_secrets"].append({"name": name, "reason": "ours, honest"})
            i = inv((name, "server_secret", consumer))
            errs = cp.validate(broken(f), i, MD)
            self.assertTrue(any(name in e and "first-party" in e for e in errs), (name, errs))

    def test_first_party_needs_reason_and_not_also_claimed(self):
        r = broken(lambda r: r["first_party_secrets"][0].update(reason=""))
        self.assertTrue(any("reason" in e for e in cp.validate(r, None, MD)))
        r = broken(lambda r: r["processors"][0]["secret_names"].append(r["first_party_secrets"][0]["name"]))
        self.assertTrue(any("both" in e for e in cp.validate(r, None, MD)))

    def test_unlisted_first_party_style_secret_still_fails(self):
        i = inv(("ADMIN_COOKIE_SECRET2", "server_secret", "app-admin"))
        self.assertTrue(any("ADMIN_COOKIE_SECRET2" in e for e in cp.validate(REG, i, MD)))


class TestMutations(unittest.TestCase):
    """Each mutation of the real register must make the real-inventory check fail."""

    def setUp(self):
        self.inv = json.loads(REAL_INV.read_text()) if REAL_INV.exists() else None

    def fails(self, fn):
        self.assertTrue(cp.validate(broken(fn), self.inv, MD, check_stale=self.inv is not None))

    def test_remove_processor(self):
        self.fails(lambda r: r["processors"].pop(0))

    def test_remove_field(self):
        self.fails(lambda r: r["processors"][3].pop("exit_procedure"))

    def test_duplicate_id(self):
        self.fails(lambda r: r["processors"].append(dict(r["processors"][1])))

    def test_unknown_category(self):
        self.fails(lambda r: r["processors"][2]["data_categories"].append("x"))

    def test_signed_without_evidence(self):
        self.fails(lambda r: r["processors"][2].update(agreement_status="signed"))

    def test_unsigned_not_owner_blocked(self):
        self.fails(lambda r: r["processors"][2].update(owner_blocked=False))

    def test_claimed_twice(self):
        self.fails(lambda r: r["processors"][0]["secret_names"].append(r["processors"][1]["secret_names"][0]))

    def test_stale_name(self):
        self.fails(lambda r: r["processors"][0]["secret_names"].append("NOPE_KEY"))


class TestRegisterDoc(unittest.TestCase):
    def test_every_processor_in_markdown(self):
        r = broken(lambda r: r["processors"].append({**copy.deepcopy(r["processors"][0]), "id": "zz-new"}))
        errs = cp.validate(r, None, MD)
        self.assertTrue(any("zz-new" in e and "processor-register.md" in e for e in errs), errs)

    def test_register_marks_agreements_owner_blocked(self):
        self.assertIn("owner-blocked", MD.lower())
        self.assertIn("not signed", MD.lower())


class TestOtherDocs(unittest.TestCase):
    def read(self, name):
        return (LEGAL / name).read_text()

    def test_breach_procedure_sections(self):
        t = self.read("breach-procedure.md")
        for h in ["Detection", "Severity matrix", "Notification clock", "Roles", "Containment",
                  "Evidence preservation", "Notify the PDPO", "Notify affected", "Templates",
                  "Post-incident review", "Tabletop exercise"]:
            self.assertRegex(t, rf"(?im)^#+ .*{h}", h)
        for s in ["lost phone", "leaked api key", "wrong teacher", "bucket permission"]:
            self.assertIn(s, t.lower())
        self.assertIn("unverified", t.lower())
        self.assertIn("non-binding", t.lower())
        self.assertIn("docs/runbooks/secrets.md", t)
        self.assertIn("docs/ops/runbook.md", t)
        self.assertIn("alerts.json", t)

    def test_runbook_pointer_exists(self):
        p = LEGAL.parent / "runbooks" / "data-breach.md"
        self.assertTrue(p.exists())
        self.assertIn("docs/legal/breach-procedure.md", p.read_text())

    def test_pdpo_plan_covers_every_checklist_item(self):
        t = self.read("pdpo-registration-plan.md")
        nums = {int(m) for m in re.findall(r"(?m)^\| (\d+) \|", self.read("pdpo-registration-checklist.md"))}
        plan = {int(m) for m in re.findall(r"(?m)^\| (\d+) \|", t)}
        self.assertEqual(nums, plan)
        self.assertIn("Evidence", t)
        self.assertIn("unverified", t.lower())


if __name__ == "__main__":
    unittest.main()
