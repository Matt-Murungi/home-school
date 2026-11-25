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
from typing import ClassVar

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


class HooksSelectOnlyProcessableFiles(unittest.TestCase):
    """R2-1: no hook may select an existing placeholder/README file it cannot process."""

    NODE_HOOKS = ("prettier", "eslint")
    ALLOWED_EXT: ClassVar[dict] = {
        "dart-format": {".dart"},
        "ruff-check": {".py"},
        "ruff-format": {".py"},
        "prettier": {".ts", ".tsx", ".js", ".jsx", ".json", ".css"},
        "eslint": {".ts", ".tsx", ".js", ".jsx"},
    }

    def tracked(self):
        out = subprocess.run(
            ["git", "ls-files"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.split("\n")
        files = {f for f in out if f}
        # also include files present but not yet committed (new READMEs)
        for base in ("apps", "packages", "services", "scripts"):
            for p in (ROOT / base).rglob("*"):
                if p.is_file() and "node_modules" not in p.parts:
                    files.add(str(p.relative_to(ROOT)))
        return sorted(files)

    def test_no_hook_selects_unprocessable_files(self):
        files = self.tracked()
        for hid, h in precommit_hooks().items():
            if "files" not in h or hid not in self.ALLOWED_EXT:
                continue
            for f in files:
                if re.search(h["files"], f):
                    self.assertIn(
                        Path(f).suffix,
                        self.ALLOWED_EXT[hid],
                        f"hook {hid} selects {f}, which it cannot process",
                    )

    def test_no_hook_selects_readmes(self):
        readmes = [f for f in self.tracked() if f.endswith("README.md")]
        self.assertTrue(readmes)
        for hid, h in precommit_hooks().items():
            if "files" not in h:
                continue
            for f in readmes:
                self.assertFalse(re.search(h["files"], f), f"hook {hid} selects {f}")

    def test_node_hooks_guarded_until_package_exists(self):
        # F06 made apps/admin real: the hooks no longer skip silently (that would let
        # unlinted admin code through). They run the package-local binary and fail with a
        # clear "needs npm ci first" message when node_modules is absent.
        hooks = precommit_hooks()
        for hid in self.NODE_HOOKS:
            entry = hooks[hid]["entry"]
            self.assertEqual(hooks[hid].get("language"), "system")
            self.assertIn(
                f"apps/admin/node_modules/.bin/{hid}", entry, f"{hid} unguarded"
            )
            self.assertIn("needs npm ci first", entry)
            self.assertNotIn("npx", entry, "must use the lockfile-pinned local binary")

    def test_node_hook_guard_fails_clearly_without_npm_ci(self):
        # Execute the guard for real in a dir without apps/admin/node_modules: it must
        # fail with the fix in the message and never reach for the network.
        for hid in self.NODE_HOOKS:
            entry = precommit_hooks()[hid]["entry"]
            with tempfile.TemporaryDirectory() as d:
                r = subprocess.run(
                    ["bash", "-c", entry + " apps/admin/src/x.ts"],
                    cwd=d,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(r.returncode, 1, f"{hid}: {r.stdout}{r.stderr}")
                self.assertIn("apps/admin needs npm ci first", r.stdout + r.stderr)

    def test_node_hook_runs_local_binary_with_package_relative_paths(self):
        # With node_modules present the hook runs the local tool from apps/admin with the
        # `apps/admin/` prefix stripped from each filename.
        for hid in self.NODE_HOOKS:
            entry = precommit_hooks()[hid]["entry"]
            with tempfile.TemporaryDirectory() as d:
                b = Path(d, "apps/admin/node_modules/.bin")
                b.mkdir(parents=True)
                tool = b / hid
                tool.write_text('#!/bin/sh\necho "cwd=$(basename "$PWD") args=$*"\n')
                tool.chmod(0o755)
                r = subprocess.run(
                    ["bash", "-c", entry + " apps/admin/src/a.ts apps/admin/b.json"],
                    cwd=d,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(r.returncode, 0, f"{hid}: {r.stdout}{r.stderr}")
                self.assertIn("cwd=admin", r.stdout)
                self.assertIn("src/a.ts b.json", r.stdout)


class SecretScanExitCode(unittest.TestCase):
    def run_cli(self, d):
        return subprocess.run(
            [sys.executable, str(ROOT / "scripts/secret_scan.py"), "--path", d],
            capture_output=True,
            text=True,
            check=False,
        )

    def test_exit_1_when_secret_found_and_0_when_clean(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "ok.txt").write_text("nothing here\n", encoding="utf-8")
            self.assertEqual(self.run_cli(d).returncode, 0)
            (Path(d) / "bad.txt").write_text(
                "k = AKIA" + "ABCDEFGHIJKLMNOP\n", encoding="utf-8"
            )
            r = self.run_cli(d)
            self.assertEqual(r.returncode, 1)
            self.assertNotIn("ABCDEFGHIJKLMNOP", r.stdout + r.stderr)


class ContributingWording(unittest.TestCase):
    def test_step7_humans_open_pr_agents_hand_off(self):
        t = (ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
        step = next(line for line in t.splitlines() if line.startswith("7."))
        self.assertIn("Humans open", step)
        self.assertIn("agent builders", step)
        self.assertIn("reviewer", step)


if __name__ == "__main__":
    unittest.main()
