import copy
import json
import re
import sys
import unittest
from pathlib import Path

OPS = Path(__file__).resolve().parents[1]
ROOT = OPS.parent
sys.path.insert(0, str(OPS))
import check_ops  # noqa: E402

ALERTS = json.loads((OPS / "alerts.json").read_text())
UPTIME = json.loads((OPS / "uptime.json").read_text())
RUNBOOK = (ROOT / "docs/ops/runbook.md").read_text()
NFR17 = {
    "failed-payments",
    "failed-payouts",
    "job-failures",
    "unacknowledged-safeguarding",
    "uptime-down",
    "api-5xx-rate",
}


class Alerts(unittest.TestCase):
    def ids(self):
        return {a["id"] for a in ALERTS["alerts"]}

    def test_every_nfr17_alert_exists(self):
        self.assertTrue(NFR17 <= self.ids(), NFR17 - self.ids())

    def test_monitoring_itself_down_is_covered(self):
        self.assertIn("monitoring-heartbeat-missing", self.ids())

    def test_files_validate(self):
        self.assertEqual(check_ops.check(OPS, ROOT), [])

    def test_every_alert_has_a_runbook_section(self):
        for a in ALERTS["alerts"]:
            self.assertRegex(RUNBOOK, rf"(?m)^### {re.escape(a['id'])}\b", a["id"])

    def test_noise_controls_present(self):
        # edge case: alert fatigue. Every alert dedupes and has a threshold.
        for a in ALERTS["alerts"]:
            self.assertGreaterEqual(a["throttle_minutes"], 15, a["id"])
            self.assertIn("threshold", a)
            self.assertIn(a["severity"], {"page", "ticket", "info"})
        pages = [a for a in ALERTS["alerts"] if a["severity"] == "page"]
        self.assertLessEqual(len(pages), 6, "too many paging alerts causes fatigue")

    def test_safeguarding_and_money_alerts_page_immediately(self):
        by = {a["id"]: a for a in ALERTS["alerts"]}
        self.assertEqual(by["unacknowledged-safeguarding"]["severity"], "page")
        self.assertEqual(by["failed-payouts"]["severity"], "page")

    def test_routes_exist_and_hold_no_contact_details(self):
        routes = ALERTS["routes"]
        for a in ALERTS["alerts"]:
            for r in a["routes"]:
                self.assertIn(r, routes, a["id"])
        self.assertNotRegex(json.dumps(ALERTS), r"@|\+?\d{9,}")

    def test_validator_rejects_missing_alert(self):
        bad = copy.deepcopy(ALERTS)
        bad["alerts"] = [a for a in bad["alerts"] if a["id"] != "failed-payouts"]
        self.assertTrue(any("failed-payouts" in e for e in check_ops.check_alerts(bad, RUNBOOK)))

    def test_validator_rejects_alert_without_runbook_section(self):
        self.assertTrue(check_ops.check_alerts(ALERTS, "# empty runbook"))


class Uptime(unittest.TestCase):
    def test_health_every_minute_staging_and_production(self):
        for env in ("staging", "production"):
            hs = [
                c
                for c in UPTIME["checks"]
                if c["environment"] == env and c["service"] == "api" and c["path"] == "/health"
            ]
            self.assertEqual(len(hs), 1, env)
            self.assertLessEqual(hs[0]["interval_seconds"], 60)
            self.assertEqual(hs[0]["expected_status"], [200])

    def test_ready_and_admin_checked(self):
        keys = {(c["service"], c["path"], c["environment"]) for c in UPTIME["checks"]}
        for env in ("staging", "production"):
            self.assertIn(("api", "/ready", env), keys)
            self.assertTrue(any(k[0] == "admin" and k[2] == env for k in keys), env)

    def test_no_single_blip_alerts(self):
        # edge case: flapping / noise. Alert only after consecutive failures.
        for c in UPTIME["checks"]:
            self.assertGreaterEqual(c["confirm_after_failures"], 2, c["id"])

    def test_urls_are_variables_not_literals(self):
        for c in UPTIME["checks"]:
            self.assertRegex(c["base_url_var"], r"^HS_[A-Z_]+_URL$")
            self.assertNotIn("http", json.dumps(c))

    def test_validator_rejects_bad_manifests(self):
        bad = copy.deepcopy(UPTIME)
        bad["checks"][0]["interval_seconds"] = 600
        self.assertTrue(check_ops.check_uptime(bad))
        bad = copy.deepcopy(UPTIME)
        bad["checks"] = [c for c in bad["checks"] if c["environment"] != "production"]
        self.assertTrue(check_ops.check_uptime(bad))
        bad = copy.deepcopy(UPTIME)
        bad["checks"][0]["expected_status"] = [500]
        self.assertTrue(check_ops.check_uptime(bad))

    def test_health_path_matches_core_service(self):
        main = (ROOT / "services/core/src/hs_core/main.py").read_text()
        self.assertIn('"/health"', main)
        self.assertIn('"/ready"', main)


class NoSecrets(unittest.TestCase):
    def test_no_dsn_or_contact_details_committed(self):
        for p in [*OPS.glob("*.json"), *(ROOT / "docs/ops").glob("*.md")]:
            t = p.read_text()
            self.assertNotRegex(t, r"https://[0-9a-f]{16,}@", p.name)
            self.assertNotRegex(t, r"\+256\d{6,}", p.name)


class Docs(unittest.TestCase):
    def test_setup_and_integration_docs_cover_owner_blockers(self):
        setup = (ROOT / "docs/ops/setup.md").read_text()
        for term in ("Sentry", "Better Stack", "UptimeRobot", "DSN", "Alert routing", "heartbeat"):
            self.assertIn(term, setup)
        integ = (ROOT / "docs/ops/integration.md").read_text()
        for term in ("dart-define", "SENTRY_DSN", "offline", "scrub", "admin", "worker"):
            self.assertIn(term, integ)


if __name__ == "__main__":
    unittest.main()
