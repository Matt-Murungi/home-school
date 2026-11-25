"""Migration runner tests (offline, fake client). Maps to F02 (#28) acceptance criteria and edge cases."""

import io
import unittest

from helpers import make_dir, script  # noqa: F401  (also sets sys.path)

import migrate
from fake_client import FakeAppwriteClient


def run(client, directory, env="staging", **kw):
    out = io.StringIO()
    kw.setdefault("sleep", lambda s: None)
    result = migrate.run(client, env, directory, out=out, **kw)
    return result, out.getvalue()


class Ordering(unittest.TestCase):
    def setUp(self):
        self.tmp, self.d = make_dir()
        self.addCleanup(self.tmp.cleanup)
        self.client = FakeAppwriteClient()

    def test_runs_in_numeric_order_and_records_versions(self):
        for n in ("0010_ten", "0002_two", "0001_one"):
            script(self.d, n)
        result, _ = run(self.client, self.d)
        self.assertEqual(self.client.log, ["0001_one", "0002_two", "0010_ten"])
        self.assertEqual([r["version"] for r in self.client.applied_rows()], [1, 2, 10])
        self.assertEqual(result.applied, [1, 2, 10])
        self.assertTrue(result.promote)
        self.assertEqual(result.exit_code, 0)

    def test_schema_migration_table_created_if_missing(self):
        script(self.d, "0001_one")
        run(self.client, self.d)
        self.assertIn("schema_migration", self.client.tables)

    def test_rerun_is_a_noop(self):
        script(self.d, "0001_one")
        script(self.d, "0002_two")
        run(self.client, self.d)
        self.client.log.clear()
        result, out = run(self.client, self.d)
        self.assertEqual(self.client.log, [])
        self.assertEqual(result.applied, [])
        self.assertEqual(result.skipped, [1, 2])
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(len(self.client.applied_rows()), 2)
        self.assertIn("nothing to do", out)

    def test_new_script_after_rerun_runs_only_the_new_one(self):
        script(self.d, "0001_one")
        run(self.client, self.d)
        self.client.log.clear()
        script(self.d, "0002_two")
        run(self.client, self.d)
        self.assertEqual(self.client.log, ["0002_two"])

    def test_duplicate_version_numbers_rejected(self):
        script(self.d, "0001_a")
        script(self.d, "0001_b")
        result, out = run(self.client, self.d)
        self.assertEqual(result.exit_code, 2)
        self.assertFalse(result.promote)
        self.assertEqual(self.client.log, [])
        self.assertIn("duplicate", out)

    def test_bad_file_name_rejected(self):
        (self.d / "oops.py").write_text("def up(ctx): pass\n")
        result, out = run(self.client, self.d)
        self.assertEqual(result.exit_code, 2)
        self.assertIn("oops.py", out)

    def test_changed_applied_script_is_detected(self):
        script(self.d, "0001_one")
        run(self.client, self.d)
        (self.d / "0001_one.py").write_text("def up(ctx):\n    pass  # edited\n")
        result, out = run(self.client, self.d)
        self.assertEqual(result.exit_code, 2)
        self.assertFalse(result.promote)
        self.assertIn("changed after it was applied", out)

    def test_applied_version_missing_from_disk_is_reported(self):
        script(self.d, "0001_one")
        run(self.client, self.d)
        (self.d / "0001_one.py").unlink()
        result, out = run(self.client, self.d)
        self.assertEqual(result.exit_code, 2)
        self.assertIn("missing", out)


class FailureStopsEverything(unittest.TestCase):
    """Edge case: schema push fails halfway on staging: CI stops and reports; no deploy to production."""

    def setUp(self):
        self.tmp, self.d = make_dir()
        self.addCleanup(self.tmp.cleanup)
        self.client = FakeAppwriteClient()

    def test_first_failure_stops_later_scripts_and_blocks_promotion(self):
        script(self.d, "0001_ok")
        script(self.d, "0002_boom", body="raise RuntimeError('half-applied')")
        script(self.d, "0003_never")
        result, out = run(self.client, self.d)
        self.assertEqual(self.client.log, ["0001_ok"])
        self.assertEqual(result.failed, 2)
        self.assertEqual(result.exit_code, 1)
        self.assertFalse(result.promote)
        self.assertEqual([r["version"] for r in self.client.applied_rows()], [1])
        self.assertIn("half-applied", out)
        self.assertIn("PROMOTE=false", out)
        self.assertIn("0002", out)

    def test_failed_script_is_retried_on_next_run(self):
        script(self.d, "0001_flaky", body="ctx.client.log.append('x'); assert len(ctx.client.log) > 1")
        result, _ = run(self.client, self.d)
        self.assertEqual(result.exit_code, 1)
        self.client.log.append("fixed")
        result, _ = run(self.client, self.d)
        self.assertEqual(result.exit_code, 0)
        self.assertEqual([r["version"] for r in self.client.applied_rows()], [1])

    def test_success_prints_promote_true(self):
        script(self.d, "0001_ok")
        _, out = run(self.client, self.d)
        self.assertIn("PROMOTE=true", out)

    def test_cli_exit_code_nonzero_on_failure(self):
        script(self.d, "0001_boom", body="raise RuntimeError('x')")
        rc = migrate.main(
            ["--env", "staging", "--dir", str(self.d)],
            client_factory=lambda env: self.client,
            out=io.StringIO(),
            sleep=lambda s: None,
        )
        self.assertEqual(rc, 1)


class WaitsForAvailable(unittest.TestCase):
    """Edge case: column or index still processing after push: runner waits before data migrations."""

    def setUp(self):
        self.tmp, self.d = make_dir()
        self.addCleanup(self.tmp.cleanup)

    def test_waits_until_processing_columns_and_indexes_are_available(self):
        client = FakeAppwriteClient(
            tables={"sample_item": {"columns": {"title": "processing"}, "indexes": {"by_title": "processing"}}},
            polls_until_available=3,
        )
        script(self.d, "0001_data", body="ctx.client.log.append(ctx.client.snapshot_statuses())")
        sleeps = []
        result, out = run(client, self.d, sleep=sleeps.append)
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(len(sleeps), 3)
        self.assertEqual(client.log, [{"available"}])  # script saw only available columns/indexes
        self.assertIn("waiting", out)

    def test_times_out_and_fails_without_running_script(self):
        client = FakeAppwriteClient(
            tables={"sample_item": {"columns": {"title": "processing"}, "indexes": {}}},
            polls_until_available=10**9,
        )
        script(self.d, "0001_data")
        clock = iter(range(0, 100000, 100))
        result, out = run(client, self.d, timeout=300, now=lambda: next(clock), sleep=lambda s: None)
        self.assertEqual(result.exit_code, 1)
        self.assertFalse(result.promote)
        self.assertEqual(client.log, [])
        self.assertIn("timed out", out)

    def test_failed_column_status_fails_fast(self):
        client = FakeAppwriteClient(tables={"t": {"columns": {"c": "failed"}, "indexes": {}}})
        script(self.d, "0001_data")
        result, out = run(client, self.d)
        self.assertEqual(result.exit_code, 1)
        self.assertEqual(client.log, [])
        self.assertIn("failed", out)


class SeedGuard(unittest.TestCase):
    """Acceptance: seed loads into dev and staging only, never production (refused by environment check)."""

    def setUp(self):
        self.tmp, self.d = make_dir()
        self.addCleanup(self.tmp.cleanup)
        self.client = FakeAppwriteClient()
        script(self.d, "0001_schema")
        script(self.d, "0002_seed_sample")

    def test_seed_runs_in_dev_and_staging(self):
        for env in ("development", "staging"):
            client = FakeAppwriteClient()
            result, _ = run(client, self.d, env=env)
            self.assertEqual(result.exit_code, 0, env)
            self.assertIn("0002_seed_sample", client.log)

    def test_production_refuses_before_running_anything(self):
        result, out = run(self.client, self.d, env="production")
        self.assertEqual(result.exit_code, 3)
        self.assertFalse(result.promote)
        self.assertEqual(self.client.log, [])  # not even the schema script ran
        self.assertIn("seed", out.lower())
        self.assertIn("production", out)

    def test_production_without_seeds_runs_normally(self):
        (self.d / "0002_seed_sample.py").unlink()
        result, _ = run(self.client, self.d, env="production")
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(self.client.log, ["0001_schema"])

    def test_production_check_is_case_and_whitespace_insensitive(self):
        for env in ("Production", " PRODUCTION ", "prod"):
            result, _ = run(FakeAppwriteClient(), self.d, env=env)
            self.assertEqual(result.exit_code, 3, env)

    def test_already_applied_seed_does_not_matter_but_new_seed_does(self):
        # Seed recorded earlier (e.g. someone mislabelled): still refuse in production, never silently skip.
        self.client.record_applied(2, "seed_sample", "x")
        result, _ = run(self.client, self.d, env="production")
        self.assertEqual(result.exit_code, 3)

    def test_unknown_environment_rejected(self):
        result, out = run(self.client, self.d, env="qa")
        self.assertEqual(result.exit_code, 2)
        self.assertIn("unknown environment", out)

    def test_seed_in_cli_production_exit_code(self):
        rc = migrate.main(
            ["--env", "production", "--dir", str(self.d)],
            client_factory=lambda env: self.client,
            out=io.StringIO(),
            sleep=lambda s: None,
        )
        self.assertEqual(rc, 3)


if __name__ == "__main__":
    unittest.main()
