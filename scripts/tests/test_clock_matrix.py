"""scripts/clock_matrix.py: parsing and classification (the freezing itself needs time-machine)."""

import sys
import unittest
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import clock_matrix as cm


class Parse(unittest.TestCase):
    def test_pytest_ids(self):
        out = "FAILED tests/test_a.py::test_x - assert 1\nERROR tests/test_b.py::test_y\nok"
        self.assertEqual(
            cm.parse_failures("pytest", out),
            {"tests/test_a.py::test_x", "tests/test_b.py::test_y"},
        )

    def test_unittest_ids(self):
        out = (
            "FAIL: test_a (test_m.Class.test_a)\nERROR: test_b (test_m.Class.test_b)\n"
        )
        self.assertEqual(len(cm.parse_failures("unittest", out)), 2)


class Classify(unittest.TestCase):
    def test_clock_dependent_vs_always_failing(self):
        flaky, every = cm.classify(
            {"day": {"a", "z"}, "night": {"a", "b", "z"}, "month": {"z"}}
        )
        self.assertEqual(flaky, {"a": ["day", "night"], "b": ["night"]})
        self.assertEqual(every, ["z"])

    def test_broken_run_is_reported(self):
        flaky, _ = cm.classify({"day": set(), "night": None})
        self.assertEqual(flaky, {"<run broke: timeout or crash>": ["night"]})

    def test_all_green(self):
        self.assertEqual(cm.classify({"a": set(), "b": set()}), ({}, []))


class Clocks(unittest.TestCase):
    def test_every_clock_has_a_kampala_offset_and_covers_the_cases(self):
        for label, iso in cm.CLOCKS.items():
            at = datetime.fromisoformat(iso)
            self.assertEqual(at.utcoffset().total_seconds(), 3 * 3600, label)
        hours = {datetime.fromisoformat(i).hour for i in cm.CLOCKS.values()}
        self.assertTrue({3, 9, 12, 20, 21, 23} <= hours)
        days = {datetime.fromisoformat(i).strftime("%a") for i in cm.CLOCKS.values()}
        self.assertTrue({"Sun", "Wed"} <= days)
        self.assertTrue(set(cm.QUICK) <= set(cm.CLOCKS))
        # clocks where the UTC date differs from the Kampala date
        differ = [
            k
            for k, i in cm.CLOCKS.items()
            if datetime.fromisoformat(i).date()
            != datetime.fromisoformat(i).astimezone(UTC).date()
        ]
        self.assertGreaterEqual(len(differ), 3)


if __name__ == "__main__":
    unittest.main()
