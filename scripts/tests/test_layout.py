"""F01 acceptance tests: repo layout, tooling and contribution rules.

Run: python3 -m unittest discover -s scripts/tests -v   (or `make test`)
Stdlib only, offline.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import ClassVar

ROOT = Path(__file__).resolve().parents[2]
PACKAGES = [
    "apps/parent",
    "apps/teacher",
    "apps/admin",
    "packages/flutter_shared",
    "services/core",
    "appwrite",
    "docs",
    "scripts",
]


def run(cmd, cwd=ROOT, env=None, timeout=600):
    e = dict(os.environ)
    e.update(env or {})
    return subprocess.run(
        cmd,
        check=False,
        cwd=cwd,
        env=e,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


class Layout(unittest.TestCase):
    def test_directories_exist_with_readme(self):
        for p in PACKAGES:
            self.assertTrue((ROOT / p).is_dir(), p)
            self.assertTrue(
                (ROOT / p / "README.md").is_file(), f"{p}/README.md missing"
            )

    def test_docs_subfolders(self):
        self.assertTrue((ROOT / "docs/adr").is_dir())
        self.assertTrue((ROOT / "docs/product/README.md").is_file())

    def test_each_readme_states_purpose_and_test_command(self):
        for p in PACKAGES:
            text = read(f"{p}/README.md")
            self.assertIn("## Purpose", text, p)
            self.assertIn("## Test command", text, p)
            body = text.split("## Test command", 1)[1]
            self.assertTrue(
                "make test" in body or "`" in body,
                f"{p}: no test command in code ticks",
            )

    def test_manifest_covers_every_package(self):
        m = json.loads(read("packages.json"))
        ids = {x["path"] for x in m["packages"]}
        for p in PACKAGES:
            self.assertIn(p, ids)
        for x in m["packages"]:
            self.assertIn(x["status"], ("implemented", "planned"))
            if x["status"] == "implemented":
                self.assertTrue(x.get("test"), x["path"])
            else:
                self.assertTrue(
                    x.get("ticket"),
                    f"{x['path']}: planned needs the ticket that scaffolds it",
                )

    def test_spikes_kept(self):
        self.assertTrue((ROOT / "spikes/admin-appwrite").is_dir())
        self.assertTrue((ROOT / "spikes/jobs-demo").is_dir())

    def test_root_readme_lists_layout(self):
        t = read("README.md")
        for p in (
            "apps/parent",
            "apps/teacher",
            "apps/admin",
            "packages/flutter_shared",
            "services/core",
            "appwrite/",
            "docs/",
            "scripts/",
            "make test",
        ):
            self.assertIn(p, t)

    def test_root_files(self):
        for f in (
            ".editorconfig",
            ".gitignore",
            ".pre-commit-config.yaml",
            ".gitleaks.toml",
            "Makefile",
            "CONTRIBUTING.md",
            "CLAUDE.md",
            ".github/CODEOWNERS",
            ".github/pull_request_template.md",
        ):
            self.assertTrue((ROOT / f).is_file(), f)

    def test_codeowners_owner(self):
        self.assertIn("@Matt-Murungi", read(".github/CODEOWNERS"))

    def test_pr_template(self):
        t = read(".github/pull_request_template.md")
        self.assertIn("Closes #", t)
        self.assertIn("Generated with [Claude Code]", t)

    def test_precommit_has_each_language(self):
        t = read(".pre-commit-config.yaml")
        for needle in (
            "dart format",
            "ruff",
            "prettier",
            "eslint",
            "gitleaks",
            "make lint",
        ):
            self.assertIn(needle, t)


class Docs(unittest.TestCase):
    RULES: ClassVar[list[str]] = [
        "home tutoring, not homeschooling",
        "memory.md",
        "read-only",
        "integer UGX",
        "UTC",
        "Africa/Kampala",
        "5,000",
        "0002",
        "0004",
        "0012",
        "tests first",
        "adapter",
        "fake",
        "Closes #",
        "make test",
        "make lint",
        "secret",
        "one ticket",
        "Co-Authored-By",
    ]

    def check(self, rel):
        t = read(rel).lower()
        for r in self.RULES:
            self.assertIn(r.lower(), t, f"{rel} lacks: {r}")

    def test_claude_md(self):
        self.check("CLAUDE.md")
        t = read("CLAUDE.md")
        self.assertIn("<id>-<slug>", t)  # branch naming

    def test_contributing(self):
        self.check("CONTRIBUTING.md")
        self.assertIn("<id>-<slug>", read("CONTRIBUTING.md"))


class Tooling(unittest.TestCase):
    def test_make_test_and_lint_exit_zero(self):
        # Exercises the make entry points end to end, cheaply. Skipped here: root-tests (would
        # recurse into this file), spikes and app-admin (npm ci needs network and a writable npm
        # cache) and the Flutter packages (minutes of flutter test/analyze, and a hard 600 s
        # subprocess timeout under load). Each of those runs for real in `make test`/`make lint`.
        skip = (
            "root-tests,spike-admin,spike-jobs,app-admin,"
            "app-parent,app-teacher,flutter-shared"
        )
        r = run(["make", "test", f"HS_SKIP={skip}"])
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("SKIP", r.stdout)
        r = run(["make", "lint", f"HS_SKIP={skip}"])
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_planned_packages_skip_clearly(self):
        # uses its own manifest so it does not depend on which tickets have landed
        d = tempfile.mkdtemp()
        try:
            man = Path(d, "m.json")
            man.write_text(
                json.dumps(
                    {
                        "packages": [
                            {
                                "id": "x",
                                "path": "apps/x",
                                "status": "planned",
                                "ticket": "F99",
                                "test": "false",
                            }
                        ]
                    }
                )
            )
            r = run(
                [
                    sys.executable,
                    "scripts/run_packages.py",
                    "test",
                    "--manifest",
                    str(man),
                ]
            )
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn(
                "SKIP apps/x: not implemented yet (planned for F99)", r.stdout
            )
        finally:
            shutil.rmtree(d)

    def test_missing_tool_degrades_with_message(self):
        # empty PATH apart from python: required tool missing -> clear SKIP, exit 0
        d = tempfile.mkdtemp()
        try:
            man = Path(d, "m.json")
            man.write_text(
                json.dumps(
                    {
                        "packages": [
                            {
                                "id": "x",
                                "path": "x",
                                "status": "implemented",
                                "requires": ["definitely-not-a-tool"],
                                "test": "false",
                            }
                        ]
                    }
                )
            )
            r = run(
                [
                    sys.executable,
                    "scripts/run_packages.py",
                    "test",
                    "--manifest",
                    str(man),
                ]
            )
            self.assertEqual(r.returncode, 0, r.stdout)
            self.assertIn("tool missing: definitely-not-a-tool", r.stdout)
            r = run(
                [
                    sys.executable,
                    "scripts/run_packages.py",
                    "test",
                    "--manifest",
                    str(man),
                ],
                env={"HS_STRICT": "1"},
            )
            self.assertNotEqual(r.returncode, 0)
        finally:
            shutil.rmtree(d)

    def _needs_manifest(self, d):
        man = Path(d, "m.json")
        Path(d, "x").mkdir()
        man.write_text(
            json.dumps(
                {
                    "packages": [
                        {
                            "id": "x",
                            "path": str(Path(d, "x")),
                            "status": "implemented",
                            "needs": "node_modules",
                            "setup": "npm ci",
                            "lint": "true",
                        }
                    ]
                }
            )
        )
        return man

    def test_unprepared_package_skips_with_setup_hint(self):
        # A package whose dependencies are not installed (needs path missing) is skipped
        # with the exact setup command, not run into an opaque "command not found".
        d = tempfile.mkdtemp()
        try:
            man = self._needs_manifest(d)
            cmd = [
                sys.executable,
                "scripts/run_packages.py",
                "lint",
                "--manifest",
                str(man),
            ]
            r = run(cmd)
            self.assertEqual(r.returncode, 0, r.stdout)
            self.assertIn(
                f"SKIP {Path(d, 'x')}: not set up (missing node_modules); run `npm ci` in {Path(d, 'x')}",
                r.stdout,
            )
            r = run(cmd, env={"HS_STRICT": "1"})
            self.assertNotEqual(
                r.returncode, 0, "strict mode must fail an unprepared package"
            )
            Path(d, "x", "node_modules").mkdir()
            r = run(cmd)
            self.assertEqual(r.returncode, 0, r.stdout)
            self.assertIn("1 ran ok", r.stdout)
        finally:
            shutil.rmtree(d)

    def test_failing_package_fails_run(self):
        d = tempfile.mkdtemp()
        try:
            man = Path(d, "m.json")
            man.write_text(
                json.dumps(
                    {
                        "packages": [
                            {
                                "id": "x",
                                "path": ".",
                                "status": "implemented",
                                "test": "false",
                            }
                        ]
                    }
                )
            )
            r = run(
                [
                    sys.executable,
                    "scripts/run_packages.py",
                    "test",
                    "--manifest",
                    str(man),
                ]
            )
            self.assertNotEqual(r.returncode, 0)
        finally:
            shutil.rmtree(d)


class LayoutGuard(unittest.TestCase):
    """Edge case: an agent adds a package outside the layout -> lint fails clearly."""

    def make_tree(self, d):
        for p in (
            "apps",
            "packages",
            "services",
            "appwrite",
            "docs",
            "scripts",
            "spikes",
        ):
            Path(d, p).mkdir()
        Path(d, "packages.json").write_text(read("packages.json"))
        for x in json.loads(read("packages.json"))["packages"]:
            Path(d, x["path"]).mkdir(parents=True, exist_ok=True)

    def test_repo_passes(self):
        r = run([sys.executable, "scripts/check_layout.py"])
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_unknown_top_level_dir_fails(self):
        d = tempfile.mkdtemp()
        try:
            self.make_tree(d)
            Path(d, "random_pkg").mkdir()
            r = run([sys.executable, "scripts/check_layout.py", "--root", d])
            self.assertEqual(r.returncode, 1)
            self.assertIn("random_pkg", r.stdout)
            self.assertIn("outside the layout", r.stdout)
        finally:
            shutil.rmtree(d)

    def test_unregistered_package_under_apps_fails(self):
        d = tempfile.mkdtemp()
        try:
            self.make_tree(d)
            Path(d, "apps/newthing").mkdir()
            r = run([sys.executable, "scripts/check_layout.py", "--root", d])
            self.assertEqual(r.returncode, 1)
            self.assertIn("apps/newthing", r.stdout)
            self.assertIn("packages.json", r.stdout)
        finally:
            shutil.rmtree(d)

    def test_missing_readme_fails(self):
        d = tempfile.mkdtemp()
        try:
            self.make_tree(d)
            r = run([sys.executable, "scripts/check_layout.py", "--root", d])
            self.assertEqual(r.returncode, 1)
            self.assertIn("README.md", r.stdout)
        finally:
            shutil.rmtree(d)


class SecretScan(unittest.TestCase):
    def scan(self, d):
        return run([sys.executable, "scripts/secret_scan.py", "--path", d])

    def test_flags_planted_secrets(self):
        d = tempfile.mkdtemp()
        try:
            # built at runtime so this file itself is not a secret
            fake_aws = "AKIA" + "ABCDEFGHIJKLMNOP"
            fake_gh = "ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"
            Path(d, "a.py").write_text(f'KEY = "{fake_aws}"\n')
            Path(d, "b.txt").write_text(f"token {fake_gh}\n")
            Path(d, "c.env").write_text(
                "APPWRITE_API_KEY=" + "standard_" + "0123456789abcdef" * 4 + "\n"
            )
            Path(d, "d.pem").write_text("-----BEGIN RSA PRIVATE" + " KEY-----\nabc\n")
            r = self.scan(d)
            self.assertEqual(r.returncode, 1, r.stdout)
            for f in ("a.py", "b.txt", "c.env", "d.pem"):
                self.assertIn(f, r.stdout)
            self.assertNotIn(fake_aws, r.stdout)  # never echo the secret
            self.assertNotIn(fake_gh, r.stdout)
        finally:
            shutil.rmtree(d)

    def test_clean_dir_and_placeholders_pass(self):
        d = tempfile.mkdtemp()
        try:
            Path(d, "x.env.example").write_text(
                "APPWRITE_API_KEY=\nAPPWRITE_PROJECT_ID=your-project-id\n"
            )
            Path(d, "ok.py").write_text("print('hello')\n")
            self.assertEqual(self.scan(d).returncode, 0)
        finally:
            shutil.rmtree(d)

    def test_repo_is_clean(self):
        r = run([sys.executable, "scripts/secret_scan.py"])
        self.assertEqual(r.returncode, 0, r.stdout)


if __name__ == "__main__":
    unittest.main()
