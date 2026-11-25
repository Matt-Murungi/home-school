"""F01 round-2 tests: pre-commit scoping, ruff-clean scripts, secret-scan edge cases, docs wording.

Stdlib only, offline (the ruff test skips when neither ruff nor uvx is installed).
"""

import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import secret_scan


def precommit_hooks():
    """Minimal yaml-free parse: {hook id: {key: value}} for `- id:` items and their `key: value` lines."""
    hooks, cur = {}, None
    for line in (
        (ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8").splitlines()
    ):
        m = re.match(r"\s*-\s*id:\s*(\S+)", line)
        if m:
            cur = hooks.setdefault(m.group(1), {})
            continue
        m = re.match(r"\s+(\w+):\s*(.*)$", line)
        if m and cur is not None:
            cur[m.group(1)] = m.group(2).strip()
    return hooks


class PreCommitScope(unittest.TestCase):
    LANG_HOOKS = ("dart-format", "ruff-check", "ruff-format")

    def test_language_hooks_are_scoped_to_their_packages(self):
        hooks = precommit_hooks()
        for hid in self.LANG_HOOKS:
            files = hooks[hid].get("files", "")
            self.assertTrue(
                files.startswith("^"),
                f"{hid}: files must be anchored to a package dir, got {files!r}",
            )
            self.assertRegex(
                files,
                r"\^\(?(apps|packages|services|scripts)",
                f"{hid} not scoped: {files!r}",
            )

    def test_every_files_pattern_is_anchored(self):
        for hid, h in precommit_hooks().items():
            if "files" in h:
                self.assertTrue(
                    h["files"].startswith("^"),
                    f"{hid}: unanchored files {h['files']!r}",
                )

    def test_python_hooks_cover_core_and_scripts_not_spikes(self):
        h = precommit_hooks()["ruff-check"]["files"]
        self.assertTrue(re.search(h, "services/core/app/x.py"))
        self.assertTrue(re.search(h, "scripts/secret_scan.py"))
        self.assertFalse(re.search(h, "spikes/jobs-demo/src/a.py"))
        self.assertFalse(re.search(h, "docs/adr/check-adrs.py"))

    def test_dart_hook_scope(self):
        h = precommit_hooks()["dart-format"]["files"]
        self.assertTrue(re.search(h, "apps/parent/lib/main.dart"))
        self.assertTrue(re.search(h, "packages/flutter_shared/lib/a.dart"))
        self.assertFalse(re.search(h, "spikes/x/a.dart"))


class RuffOnScripts(unittest.TestCase):
    def ruff(self):
        if shutil.which("ruff"):
            return ["ruff"]
        if shutil.which("uvx"):
            return ["uvx", "ruff"]
        self.skipTest(
            "neither ruff nor uvx installed; run `uvx ruff check scripts` where available"
        )

    def run_ruff(self, *args):
        try:
            return subprocess.run(
                [*self.ruff(), *args, "scripts"],
                cwd=ROOT,
                capture_output=True,
                text=True,
                timeout=300,
                check=False,
            )
        except subprocess.TimeoutExpired:
            self.skipTest("ruff unavailable (timeout, offline?)")

    def test_ruff_check_clean(self):
        r = self.run_ruff("check")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_ruff_format_clean(self):
        r = self.run_ruff("format", "--check")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


class SecretScanRound2(unittest.TestCase):
    def scan_text(self, text, name="a.txt"):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / name).write_text(text, encoding="utf-8")
            return secret_scan.scan(Path(d))

    VALUE = "Zq9xLm2PwRt7YuIo4Ks81"

    def test_json_quoted_keys_flagged(self):
        for t in (
            '{"password": "@V@"}'.replace("@V@", self.VALUE),
            '{"api_key": "@V@"}'.replace("@V@", self.VALUE),
            "{'token': '@V@'}".replace("@V@", self.VALUE),
            '"client_secret" : "@V@",'.replace("@V@", self.VALUE),
        ):
            self.assertTrue(self.scan_text(t), t.replace(self.VALUE, "<v>"))

    def test_real_value_with_example_comment_still_flagged(self):
        for t in (
            'secret = "@V@"  # example'.replace("@V@", self.VALUE),
            "api_key = '@V@'  # your key here".replace("@V@", self.VALUE),
            'password: "@V@" # <rotate me>'.replace("@V@", self.VALUE),
        ):
            self.assertTrue(self.scan_text(t), t.replace(self.VALUE, "<v>"))

    def test_placeholders_not_flagged(self):
        for t in (
            "APPWRITE_API_KEY=",
            "APPWRITE_API_KEY=<your-key>",
            'api_key = "<your-api-key-goes-here>"',
            "password=changeme",
            "password=changemechangemechangeme",
            "token=xxxxxxxxxxxxxxxxxxxxxxxx",
            'secret = "your-secret-goes-here-abc"',
            "secret: ${SECRET_FROM_ENV_VARIABLE}",
            "token = process.env.SOME_LONG_TOKEN_NAME_X",
            "token = os.environ['SOME_LONG_TOKEN_NAME_X']",
            '{"password": "<your-password-here-123>"}',
        ):
            self.assertEqual(self.scan_text(t), [], t)

    def test_output_never_prints_value(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "a.json").write_text(
                '{"password": "@V@"}'.replace("@V@", self.VALUE), encoding="utf-8"
            )
            r = subprocess.run(
                [sys.executable, str(ROOT / "scripts/secret_scan.py"), "--path", d],
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertEqual(r.returncode, 1)
        self.assertNotIn(self.VALUE, r.stdout + r.stderr)


class ContributingWording(unittest.TestCase):
    def test_step7_humans_open_pr_agents_hand_off(self):
        t = (ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
        step = next(line for line in t.splitlines() if line.startswith("7."))
        self.assertIn("Humans open", step)
        self.assertIn("agent builders", step)
        self.assertIn("reviewer", step)


if __name__ == "__main__":
    unittest.main()
