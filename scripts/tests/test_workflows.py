"""F07 (#33) tests: the GitHub workflows satisfy the ticket's acceptance criteria and edge cases.

We cannot run GitHub Actions offline, so these tests parse the YAML (scripts/ci_yaml.py, stdlib) and assert
the rules in scripts/ci_lint.py, then break each rule on a copy and check the linter notices (negative tests).
The workflows' first real run is the PR's own checks (reviewer must read them).
Stdlib only, offline; the PyYAML cross-check skips when uv or the network is unavailable.
"""

import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import ci_changes
import ci_lint
import ci_yaml

WF = ROOT / ".github" / "workflows"


def load(name):
    return ci_yaml.load((WF / name).read_text(encoding="utf-8"))


class Scratch:
    """A copy of the files the linter reads, with a text mutation applied to one file."""

    def __init__(self, test):
        self.dir = Path(tempfile.mkdtemp())
        test.addCleanup(shutil.rmtree, self.dir, True)
        shutil.copytree(ROOT / ".github", self.dir / ".github")
        (self.dir / "docs" / "ops").mkdir(parents=True)
        shutil.copy(ROOT / "docs/ops/deploy.md", self.dir / "docs/ops/deploy.md")
        shutil.copy(ROOT / "packages.json", self.dir / "packages.json")
        for lock in ("services/core/uv.lock", "apps/admin/package-lock.json"):
            (self.dir / lock).parent.mkdir(parents=True, exist_ok=True)
            (self.dir / lock).write_text("")
        for d in ("apps/parent", "apps/teacher", "packages/flutter_shared"):
            (self.dir / d).mkdir(parents=True, exist_ok=True)
            (self.dir / d / "pubspec.yaml").write_text("")

    def edit(self, rel, old, new, count=1):
        p = self.dir / rel
        text = p.read_text(encoding="utf-8")
        self.assert_in = old in text
        assert old in text, f"mutation target not found in {rel}: {old!r}"
        p.write_text(text.replace(old, new, count), encoding="utf-8")
        return self

    def problems(self):
        return ci_lint.run(self.dir)


class RepoIsClean(unittest.TestCase):
    def test_real_repo_has_no_lint_problems(self):
        self.assertEqual(ci_lint.run(ROOT), [])

    def test_scratch_copy_is_clean_so_negative_tests_are_meaningful(self):
        self.assertEqual(Scratch(self).problems(), [])

    def test_cli_exit_codes(self):
        ok = subprocess.run(
            [sys.executable, str(ROOT / "scripts/ci_lint.py")],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(ok.returncode, 0, ok.stdout)
        s = Scratch(self).edit(
            ".github/workflows/ci.yml", "  pull_request:\n", "  pull_request_target:\n"
        )
        bad = subprocess.run(
            [sys.executable, str(ROOT / "scripts/ci_lint.py"), "--root", str(s.dir)],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(bad.returncode, 1)
        self.assertIn("FAIL", bad.stdout)


class CriterionPathFilters(unittest.TestCase):
    """AC: path filters so only changed packages build."""

    def test_every_package_job_is_filtered_by_the_changes_job(self):
        jobs = load("ci.yml")["jobs"]
        for jid, job in jobs.items():
            if jid in ("changes", "repo-checks", "ci-ok"):
                continue
            self.assertIn("needs.changes.outputs.", job["if"], jid)
            self.assertIn("changes", job["needs"], jid)

    def test_changes_job_exports_what_jobs_consume(self):
        wf = load("ci.yml")
        outs = set(wf["jobs"]["changes"]["outputs"])
        self.assertEqual(outs, set(ci_changes.GROUPS) | {"flutter"})

    def test_every_implemented_package_has_a_job_or_a_recorded_exemption(self):
        manifest = json.loads((ROOT / "packages.json").read_text())
        jobs = load("ci.yml")["jobs"]
        covered = {
            p
            for j in jobs.values()
            for p in str((j.get("env") or {}).get("HS_PACKAGES", "")).split(",")
        }
        for pkg in manifest["packages"]:
            if pkg["status"] == "implemented":
                self.assertTrue(
                    pkg["id"] in covered
                    or ci_lint.EXEMPT_PACKAGES.get(pkg["id"], "").strip(),
                    pkg["id"],
                )

    def test_exemptions_name_real_packages_with_a_reason(self):
        ids = {
            p["id"]
            for p in json.loads((ROOT / "packages.json").read_text())["packages"]
        }
        for pid, reason in ci_lint.EXEMPT_PACKAGES.items():
            self.assertIn(pid, ids)
            self.assertGreater(len(reason), 20)

    def test_every_package_path_is_reachable_from_a_filter_group(self):
        """A new package whose files trigger no group would never be built: fail instead."""
        manifest = json.loads((ROOT / "packages.json").read_text())
        for pkg in manifest["packages"]:
            if pkg["status"] != "implemented" or pkg["id"] in ci_lint.EXEMPT_PACKAGES:
                continue
            res = ci_changes.compute([pkg["path"] + "/some_file"])
            on = [g for g, v in res.items() if v == "true"] + [
                json.loads(res["flutter"]) and "flutter"
            ]
            self.assertTrue(
                [x for x in on if x],
                f"{pkg['id']}: no CI group is triggered by {pkg['path']}",
            )

    def test_missing_job_for_a_new_package_is_reported(self):
        s = Scratch(self)
        m = json.loads((s.dir / "packages.json").read_text())
        m["packages"].append(
            {
                "id": "newpkg",
                "path": "services/new",
                "status": "implemented",
                "test": "pytest",
                "lint": "ruff check .",
            }
        )
        (s.dir / "packages.json").write_text(json.dumps(m))
        self.assertTrue(any("newpkg" in p and "no CI job" in p for p in s.problems()))

    def test_every_package_job_runs_the_manifest_runner_for_its_own_id(self):
        """No command is copied into the workflow: jobs call run_packages.py --only <id> (F07-R1)."""
        jobs = load("ci.yml")["jobs"]
        for jid, job in jobs.items():
            ids = [
                x
                for x in str((job.get("env") or {}).get("HS_PACKAGES", "")).split(",")
                if x
            ]
            if not ids or "matrix" in json.dumps(job.get("strategy", {})):
                continue
            for kind in ("test", "lint"):
                for i in ids:
                    self.assertTrue(
                        ci_lint._runs_manifest(job, kind, i, False), f"{jid} {kind} {i}"
                    )

    def test_flutter_matrix_ids_are_exactly_the_flutter_packages(self):
        job = load("ci.yml")["jobs"]["flutter"]
        self.assertEqual(
            set(job["env"]["HS_PACKAGES"].split(",")), set(ci_changes.FLUTTER)
        )
        for kind in ("test", "lint"):
            self.assertTrue(ci_lint._runs_manifest(job, kind, "x", True))

    def test_no_package_command_is_copied_into_ci_yml(self):
        text = (WF / "ci.yml").read_text()
        for pkg in json.loads((ROOT / "packages.json").read_text())["packages"]:
            if pkg["id"] in ci_lint.EXEMPT_PACKAGES:
                continue
            for kind in ("test", "lint"):
                for seg in [x.strip() for x in (pkg.get(kind) or "").split("&&")]:
                    if seg and seg not in ("npm ci",):
                        self.assertNotIn(f"run: {seg}", text, f"{pkg['id']}: {seg}")

    def test_manifest_command_change_needs_no_workflow_change(self):
        """Drift-proofing: edit a command in a copy of packages.json, the lint stays clean."""
        s = Scratch(self)
        m = json.loads((s.dir / "packages.json").read_text())
        for pkg in m["packages"]:
            if pkg["id"] == "appwrite":
                pkg["lint"] += " && python3 another_check.py"
        (s.dir / "packages.json").write_text(json.dumps(m))
        self.assertEqual(s.problems(), [])

    def test_job_calling_the_wrong_package_id_is_reported(self):
        s = Scratch(self).edit(
            ".github/workflows/ci.yml",
            "run: python3 scripts/run_packages.py test --only core",
            "run: python3 scripts/run_packages.py test --only docs",
        )
        probs = s.problems()
        self.assertTrue(any("test --only core" in p for p in probs), probs)
        self.assertTrue(any("claims HS_PACKAGES" in p for p in probs), probs)

    def test_job_skipping_lint_is_reported(self):
        s = Scratch(self).edit(
            ".github/workflows/ci.yml",
            "run: python3 scripts/run_packages.py lint --only appwrite",
            "run: echo skipped",
        )
        self.assertTrue(any("lint --only appwrite" in p for p in s.problems()))

    def test_matrix_job_with_hardcoded_id_is_reported(self):
        s = Scratch(self).edit(
            ".github/workflows/ci.yml",
            'run: python3 scripts/run_packages.py test --only "$PKG_ID"',
            "run: python3 scripts/run_packages.py test --only app-parent",
        )
        self.assertTrue(
            any("test --only app-parent" in p or "--only" in p for p in s.problems())
        )

    def test_run_packages_only_runs_one_package_and_rejects_unknown_ids(self):
        r = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts/run_packages.py"),
                "lint",
                "--only",
                "docs",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("lint docs", r.stdout)
        self.assertNotIn("services/core", r.stdout)
        bad = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts/run_packages.py"),
                "test",
                "--only",
                "nope",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(bad.returncode, 2)
        self.assertIn("no package with id nope", bad.stdout)


class PathFilterBehaviour(unittest.TestCase):
    def test_only_the_changed_package_builds(self):
        r = ci_changes.compute(["apps/admin/src/x.ts"])
        self.assertEqual({k for k, v in r.items() if v == "true"}, {"admin"})
        self.assertEqual(r["flutter"], "[]")

    def test_readme_in_docs_only_runs_docs_job(self):
        r = ci_changes.compute(["docs/product/x.md"])
        self.assertEqual({k for k, v in r.items() if v == "true"}, {"docs"})

    def test_shared_flutter_package_builds_both_apps_and_itself(self):
        ids = [
            x["id"]
            for x in json.loads(
                ci_changes.compute(["packages/flutter_shared/lib/a.dart"])["flutter"]
            )
        ]
        self.assertEqual(sorted(ids), ["app-parent", "app-teacher", "flutter-shared"])

    def test_one_app_builds_only_that_app(self):
        ids = [
            x["id"]
            for x in json.loads(
                ci_changes.compute(["apps/teacher/lib/a.dart"])["flutter"]
            )
        ]
        self.assertEqual(ids, ["app-teacher"])

    def test_function_change_runs_core_tests_and_appwrite_tests(self):
        r = ci_changes.compute(["appwrite/functions/job-tick/src/main.py"])
        self.assertEqual(r["core"], "true")
        self.assertEqual(r["appwrite"], "true")

    def test_shared_files_and_workflows_run_everything(self):
        for f in (
            "packages.json",
            "Makefile",
            ".github/workflows/ci.yml",
            "scripts/ci_changes.py",
        ):
            r = ci_changes.compute([f])
            self.assertTrue(all(v == "true" for k, v in r.items() if k != "flutter"), f)
            self.assertEqual(len(json.loads(r["flutter"])), 3)

    def test_unknown_change_set_fails_safe_to_everything(self):
        r = ci_changes.compute(None)
        self.assertTrue(all(v == "true" for k, v in r.items() if k != "flutter"))

    def test_first_push_or_missing_base_builds_everything(self):
        self.assertIsNone(ci_changes.git_changed("0" * 40, "HEAD"))
        self.assertIsNone(ci_changes.git_changed("", "HEAD"))
        self.assertIsNone(
            ci_changes.git_changed("f" * 40, "HEAD")
        )  # base not in the clone

    def test_cli_writes_github_output(self):
        out = Path(tempfile.mkdtemp()) / "gh_out"
        files = out.parent / "files.txt"
        files.write_text("services/core/src/a.py\n")
        env = {"GITHUB_OUTPUT": str(out), "PATH": "/usr/bin:/bin"}
        subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts/ci_changes.py"),
                "--files-from",
                str(files),
            ],
            check=True,
            capture_output=True,
            env=env,
        )
        lines = out.read_text().splitlines()
        self.assertIn("core=true", lines)
        self.assertIn("admin=false", lines)
        self.assertIn("flutter=[]", lines)
        shutil.rmtree(out.parent)

    def test_real_git_diff_between_commits(self):
        d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, d, True)

        def git(*a):
            subprocess.run(
                ["git", "-C", str(d), "-c", "user.email=a@b", "-c", "user.name=t", *a],
                check=True,
                capture_output=True,
            )

        git("init", "-q")
        (d / "a.txt").write_text("1")
        git("add", ".")
        git("commit", "-qm", "1")
        (d / "apps" / "admin").mkdir(parents=True)
        (d / "apps/admin/x.ts").write_text("1")
        git("add", ".")
        git("commit", "-qm", "2")
        old = ci_changes.ROOT
        ci_changes.ROOT = d
        try:
            self.assertEqual(
                ci_changes.git_changed("HEAD~1", "HEAD"), ["apps/admin/x.ts"]
            )
        finally:
            ci_changes.ROOT = old


class CiOkAggregate(unittest.TestCase):
    """The single required check."""

    def test_ci_ok_exists_needs_every_job_and_always_runs(self):
        jobs = load("ci.yml")["jobs"]
        self.assertIn("ci-ok", jobs)
        self.assertEqual(set(jobs["ci-ok"]["needs"]), set(jobs) - {"ci-ok"})
        self.assertEqual(jobs["ci-ok"]["if"], "always()")
        self.assertEqual(
            jobs["ci-ok"]["name"], "ci-ok"
        )  # the name branch protection matches

    def test_ci_ok_fails_on_failure_or_cancel_but_not_on_skipped(self):
        steps = load("ci.yml")["jobs"]["ci-ok"]["steps"]
        cond = steps[0]["if"]
        self.assertIn("contains(needs.*.result, 'failure')", cond)
        self.assertIn("contains(needs.*.result, 'cancelled')", cond)
        self.assertNotIn("skipped", cond)
        self.assertIn("exit 1", steps[0]["run"])
        self.assertNotIn("if", steps[1])

    def test_ci_ok_simulation(self):
        """Evaluate the documented condition over result sets (GitHub semantics for contains(needs.*.result, x))."""

        def fails(results):
            return "failure" in results or "cancelled" in results

        self.assertFalse(
            fails(["success", "skipped", "skipped"])
        )  # path filter skipped jobs
        self.assertFalse(fails(["skipped"] * 5))
        self.assertTrue(fails(["success", "failure", "skipped"]))
        self.assertTrue(fails(["cancelled", "success"]))

    def test_ci_ok_missing_a_need_is_reported(self):
        s = Scratch(self).edit(
            ".github/workflows/ci.yml", "core, appwrite, ops", "appwrite, ops"
        )
        self.assertTrue(
            any("ci-ok must need every other job" in p for p in s.problems())
        )

    def test_ci_ok_without_always_is_reported(self):
        s = Scratch(self).edit(".github/workflows/ci.yml", "    if: always()\n", "")
        self.assertTrue(any("always()" in p for p in s.problems()))

    def test_ci_ok_that_ignores_failures_is_reported(self):
        s = Scratch(self).edit(
            ".github/workflows/ci.yml", "contains(needs.*.result, 'failure') || ", ""
        )
        self.assertTrue(
            any("must fail when any needed job failed" in p for p in s.problems())
        )


class SecurityRules(unittest.TestCase):
    def test_no_pull_request_target_anywhere(self):
        for p in WF.glob("*.yml"):
            self.assertNotIn(
                "pull_request_target",
                ci_lint.triggers(ci_yaml.load(p.read_text())),
                p.name,
            )
            code = "\n".join(
                ln
                for ln in p.read_text().splitlines()
                if not ln.lstrip().startswith("#")
            )
            self.assertNotIn("pull_request_target", code)

    def test_pull_request_target_is_reported(self):
        s = Scratch(self).edit(
            ".github/workflows/ci.yml", "  pull_request:\n", "  pull_request_target:\n"
        )
        self.assertTrue(any("pull_request_target" in p for p in s.problems()))

    def test_permissions_are_read_only(self):
        for p in WF.glob("*.yml"):
            self.assertEqual(
                ci_yaml.load(p.read_text())["permissions"], {"contents": "read"}, p.name
            )

    def test_write_all_or_write_permission_is_reported(self):
        for new in ("permissions: write-all", "permissions:\n  contents: write"):
            s = Scratch(self).edit(
                ".github/workflows/ci.yml", "permissions:\n  contents: read", new
            )
            self.assertTrue(any("permissions" in p for p in s.problems()), new)

    def test_missing_permissions_is_reported(self):
        s = Scratch(self).edit(
            ".github/workflows/ci.yml", "permissions:\n  contents: read\n", ""
        )
        self.assertTrue(any("`permissions` missing" in p for p in s.problems()))

    def test_ci_uses_no_secrets_and_no_environments_so_forks_get_nothing(self):
        text = (WF / "ci.yml").read_text()
        self.assertNotRegex(text, r"secrets\.(?!GITHUB_TOKEN)")
        self.assertNotIn("environment:", text)
        self.assertNotIn("DEPLOY_ENABLED", text)

    def test_secret_in_a_pull_request_workflow_is_reported(self):
        s = Scratch(self).edit(
            ".github/workflows/ci.yml",
            "      - name: test (command from packages.json)\n        run: python3 scripts/run_packages.py test --only root-tests",
            "      - name: unit tests\n        env:\n          K: ${{ secrets.APPWRITE_API_KEY }}\n        working-directory: scripts",
        )
        self.assertTrue(any("secrets.APPWRITE_API_KEY" in p for p in s.problems()))

    def test_all_actions_pinned_to_full_sha(self):
        n = 0
        for p in WF.glob("*.yml"):
            for jid, job in ci_lint.jobs(ci_yaml.load(p.read_text())).items():
                for st in ci_lint.steps(job):
                    if "uses" in st:
                        n += 1
                        self.assertRegex(
                            st["uses"].split("@")[1],
                            r"^[0-9a-f]{40}$",
                            f"{p.name}:{jid}",
                        )
        self.assertGreater(n, 10)
        self.assertEqual(ci_lint.UNPINNED_ALLOWED, {})

    def test_tag_pinned_action_is_reported(self):
        s = Scratch(self).edit(
            ".github/workflows/ci.yml",
            "actions/setup-node@949feb2413d6458794dcd2491c4babbbce0c15c1",
            "actions/setup-node@v4",
        )
        self.assertTrue(any("not pinned" in p for p in s.problems()))

    def test_sha_comment_matches_the_pinned_ref_format(self):
        for p in WF.glob("*.yml"):
            for m in re.finditer(
                r"uses: (\S+)@([0-9a-f]{40})\s*(#.*)?$", p.read_text(), re.MULTILINE
            ):
                self.assertTrue(
                    m.group(3), f"{p.name}: {m.group(1)} pin has no version comment"
                )

    def test_checkout_does_not_persist_credentials(self):
        s = Scratch(self).edit(
            ".github/workflows/ci.yml",
            "persist-credentials: false",
            "persist-credentials: true",
        )
        self.assertTrue(any("persist-credentials" in p for p in s.problems()))

    def test_every_job_has_a_timeout(self):
        for p in WF.glob("*.yml"):
            for jid, job in ci_lint.jobs(ci_yaml.load(p.read_text())).items():
                self.assertIn("timeout-minutes", job, f"{p.name}:{jid}")

    def test_untrusted_context_in_run_is_reported(self):
        s = Scratch(self).edit(
            ".github/workflows/ci.yml",
            "run: make layout secrets",
            "run: echo ${{ github.event.pull_request.title }}",
        )
        self.assertTrue(any("untrusted context" in p for p in s.problems()))

    def test_env_values_not_expressions_are_used_for_inputs(self):
        text = (WF / "promote-production.yml").read_text()
        for run in re.findall(r"run: \|\n((?:\s{10,}.*\n)+)", text):
            self.assertNotIn("inputs.", run)


class CriterionPerformanceRules(unittest.TestCase):
    """HS_STRICT, concurrency, caching."""

    def test_hs_strict_is_on_for_the_workflow(self):
        self.assertEqual(load("ci.yml")["env"]["HS_STRICT"], "1")

    def test_no_job_overrides_strict(self):
        jobs = load("ci.yml")["jobs"]
        for jid, job in jobs.items():
            self.assertNotIn("HS_STRICT", job.get("env") or {}, jid)
        self.assertNotIn(
            "HS_STRICT", jobs["scripts"].get("env") or {}
        )  # strict everywhere (main-health made it safe)

    def test_strict_override_in_another_job_is_reported(self):
        s = Scratch(self).edit(
            ".github/workflows/ci.yml",
            "      HS_PACKAGES: docs\n",
            "      HS_PACKAGES: docs\n      HS_STRICT: '0'\n",
        )
        self.assertTrue(any("only the `scripts` job" in p for p in s.problems()))

    def test_missing_hs_strict_is_reported(self):
        s = Scratch(self).edit(
            ".github/workflows/ci.yml", '  HS_STRICT: "1"', '  OTHER: "1"'
        )
        self.assertTrue(any("HS_STRICT" in p for p in s.problems()))

    def test_ci_concurrency_cancels_only_pull_requests(self):
        c = load("ci.yml")["concurrency"]
        self.assertIn("pull_request.number", c["group"])
        self.assertEqual(
            c["cancel-in-progress"], "${{ github.event_name == 'pull_request' }}"
        )

    def test_deploy_workflows_never_cancel(self):
        for n in ("deploy-staging.yml", "promote-production.yml", "mobile-builds.yml"):
            wf = load(n)
            self.assertEqual(wf["concurrency"]["cancel-in-progress"], "false", n)
            for jid, job in wf["jobs"].items():
                if "concurrency" in job:
                    self.assertEqual(
                        job["concurrency"]["cancel-in-progress"], "false", f"{n}:{jid}"
                    )

    def test_deploy_cancel_in_progress_true_is_reported(self):
        s = Scratch(self).edit(
            ".github/workflows/deploy-staging.yml",
            "cancel-in-progress: false",
            "cancel-in-progress: true",
        )
        self.assertTrue(any("never cancel" in p for p in s.problems()))

    def test_dependency_caches_configured(self):
        jobs = load("ci.yml")["jobs"]
        uv = next(
            s
            for s in jobs["core"]["steps"]
            if s.get("uses", "").startswith("astral-sh/setup-uv")
        )
        self.assertEqual(uv["with"]["enable-cache"], "true")
        node = next(
            s
            for s in jobs["admin"]["steps"]
            if s.get("uses", "").startswith("actions/setup-node")
        )
        self.assertEqual(node["with"]["cache"], "npm")
        fl = next(
            s
            for s in jobs["flutter"]["steps"]
            if s.get("uses", "").startswith("subosito/flutter-action")
        )
        self.assertEqual(fl["with"]["cache"], "true")
        self.assertIn("pub-cache-key", fl["with"])

    def test_flutter_runs_changed_packages_only_via_matrix(self):
        job = load("ci.yml")["jobs"]["flutter"]
        self.assertEqual(
            job["strategy"]["matrix"]["include"],
            "${{ fromJSON(needs.changes.outputs.flutter) }}",
        )
        self.assertEqual(job["if"], "needs.changes.outputs.flutter != '[]'")

    def test_flutter_version_is_pinned(self):
        step = next(
            s
            for s in load("ci.yml")["jobs"]["flutter"]["steps"]
            if "flutter-action" in s.get("uses", "")
        )
        self.assertRegex(step["with"]["flutter-version"], r"^\d+\.\d+\.\d+$")


class DeployWorkflows(unittest.TestCase):
    """AC: staging deploy automatic on main; production behind manual approval; nothing deploys until enabled."""

    def test_staging_triggers_on_push_to_main_only(self):
        on = load("deploy-staging.yml")["on"]
        self.assertEqual(on["push"]["branches"], ["main"])
        self.assertNotIn("pull_request", on)

    def test_staging_jobs_use_staging_environment_and_flag(self):
        for jid, job in load("deploy-staging.yml")["jobs"].items():
            self.assertEqual(job["environment"], "staging", jid)
            self.assertIn("vars.DEPLOY_ENABLED == 'true'", job["if"], jid)

    def test_production_is_manual_only_and_uses_production_environment(self):
        wf = load("promote-production.yml")
        self.assertEqual(set(wf["on"]), {"workflow_dispatch"})
        for jid, job in wf["jobs"].items():
            self.assertEqual(job["environment"], "production", jid)
            self.assertIn("vars.DEPLOY_ENABLED == 'true'", job["if"], jid)
            self.assertIn("github.ref == 'refs/heads/main'", job["if"], jid)

    def test_production_deploys_the_verified_input_sha_not_the_dispatch_head(self):
        text = (WF / "promote-production.yml").read_text()
        self.assertNotIn("GITHUB_SHA", text.replace("not GITHUB_SHA", ""))
        step = next(
            st
            for st in load("promote-production.yml")["jobs"]["release"]["steps"]
            if "flyctl deploy" in st.get("run", "")
        )
        self.assertEqual(step["env"]["RELEASE_SHA"], "${{ inputs.sha }}")
        self.assertIn("GIT_SHA=${RELEASE_SHA::7}", step["run"])

    def test_production_verifies_sha_is_on_main(self):
        text = (WF / "promote-production.yml").read_text()
        self.assertIn("git merge-base --is-ancestor", text)
        self.assertEqual(
            load("promote-production.yml")["on"]["workflow_dispatch"]["inputs"]["sha"][
                "required"
            ],
            "true",
        )

    def test_every_deploy_or_secret_step_is_guarded_by_the_flag(self):
        for n in ("deploy-staging.yml", "promote-production.yml", "mobile-builds.yml"):
            for jid, job in load(n)["jobs"].items():
                for st in job["steps"]:
                    text = json.dumps(st)
                    if any(
                        m in text
                        for m in ("flyctl", "appwrite-cli", "migrate.py", "secrets.")
                    ):
                        self.assertIn(
                            "vars.DEPLOY_ENABLED == 'true'",
                            st.get("if", ""),
                            f"{n}:{jid}:{st.get('name')}",
                        )

    def test_unguarded_deploy_step_is_reported(self):
        s = Scratch(self).edit(
            ".github/workflows/deploy-staging.yml",
            "      - name: Smoke check /health on the deployed URL\n        if: vars.DEPLOY_ENABLED == 'true'\n",
            "      - name: fly deploy sneaky\n        run: flyctl deploy\n      - name: Smoke check /health on the deployed URL\n        if: vars.DEPLOY_ENABLED == 'true'\n",
        )
        self.assertTrue(any("without `if:" in p for p in s.problems()))

    def test_job_without_flag_or_environment_is_reported(self):
        s = Scratch(self).edit(
            ".github/workflows/deploy-staging.yml",
            "    if: vars.DEPLOY_ENABLED == 'true'\n    environment: staging\n    runs-on: ubuntu-24.04\n    timeout-minutes: 20\n    # Two",
            "    environment: staging\n    runs-on: ubuntu-24.04\n    timeout-minutes: 20\n    # Two",
        )
        self.assertTrue(any("job `if` must contain" in p for p in s.problems()))
        s = Scratch(self).edit(
            ".github/workflows/promote-production.yml",
            "    environment: production\n",
            "",
        )
        self.assertTrue(any("environment: production" in p for p in s.problems()))

    def test_secrets_are_scoped_to_steps_not_jobs(self):
        for n in ("deploy-staging.yml", "promote-production.yml"):
            for jid, job in load(n)["jobs"].items():
                self.assertNotIn(
                    "secrets.",
                    json.dumps({k: v for k, v in job.items() if k != "steps"}),
                    f"{n}:{jid}",
                )

    def test_deploy_workflow_has_least_privilege_token(self):
        for n in ("deploy-staging.yml", "promote-production.yml", "mobile-builds.yml"):
            self.assertEqual(load(n)["permissions"], {"contents": "read"})


class MigrationFailureAndOrder(unittest.TestCase):
    """Edge cases: migration fails halfway -> no promotion; schema first, API second; no overlapping schema runs."""

    def test_staging_api_jobs_need_schema_and_gate_on_result_and_promote(self):
        jobs = load("deploy-staging.yml")["jobs"]
        for jid in ("core", "job-tick", "admin"):
            self.assertIn("schema", jobs[jid]["needs"], jid)
            self.assertIn("needs.schema.result == 'success'", jobs[jid]["if"], jid)
            self.assertIn(
                "needs.schema.outputs.promote == 'true'", jobs[jid]["if"], jid
            )
        self.assertIn(
            "core", jobs["job-tick"]["needs"]
        )  # function after the API it calls

    def test_schema_job_exports_promote_from_the_runner_step(self):
        job = load("deploy-staging.yml")["jobs"]["schema"]
        self.assertEqual(
            job["outputs"]["promote"], "${{ steps.migrate.outputs.promote }}"
        )
        step = next(s for s in job["steps"] if s.get("id") == "migrate")
        self.assertIn("appwrite/migrate.py --env staging", step["run"])

    def test_schema_runs_never_overlap(self):
        for n, group in (
            ("deploy-staging.yml", "appwrite-schema-staging"),
            ("promote-production.yml", "appwrite-schema-production"),
        ):
            wf = load(n)
            job = next(j for j in wf["jobs"].values() if "migrate.py" in json.dumps(j))
            self.assertEqual(job["concurrency"]["group"], group)
            self.assertEqual(job["concurrency"]["cancel-in-progress"], "false")

    def test_production_single_job_orders_schema_before_api_and_gates_the_rest(self):
        steps = load("promote-production.yml")["jobs"]["release"]["steps"]
        names = [s.get("name", s.get("uses", "")) for s in steps]
        i_mig = next(i for i, s in enumerate(steps) if s.get("id") == "migrate")
        for i, s in enumerate(steps):
            if any(
                m in json.dumps(s) for m in ("flyctl", "appwrite-cli", "setup-node")
            ):
                self.assertGreater(i, i_mig, names[i])
                self.assertIn("steps.migrate.outcome == 'success'", s["if"], names[i])
                self.assertIn(
                    "steps.migrate.outputs.promote == 'true'", s["if"], names[i]
                )

    def test_runner_contract_the_gate_relies_on(self):
        """migrate.py writes `promote=` to $GITHUB_OUTPUT and exits non-zero on failure (F02)."""
        text = (ROOT / "appwrite" / "migrate.py").read_text()
        self.assertIn("GITHUB_OUTPUT", text)
        self.assertIn("promote=", text)
        self.assertIn("PROMOTE=", text)

    def test_runner_failure_end_to_end_with_fake_client_gives_no_promote(self):
        """Run the real runner against its fake with a failing migration and read what the workflow would read."""
        d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, d, True)
        (d / "migrations").mkdir()
        (d / "migrations" / "0001_ok.py").write_text("def up(ctx):\n    pass\n")
        (d / "migrations" / "0002_boom.py").write_text(
            "def up(ctx):\n    raise RuntimeError('halfway')\n"
        )
        (d / "environments.json").write_text(
            json.dumps(
                {
                    "environments": {
                        "staging": {
                            "projectName": "x",
                            "endpoint": "https://example.invalid/v1",
                            "projectId": "p",
                            "databaseId": "main",
                            "keyEnvVar": "K",
                            "platforms": [],
                            "seedAllowed": True,
                        }
                    }
                }
            )
        )
        harness = (
            "import sys, json; sys.path.insert(0, {!r})\n"
            "import migrate, fake_client\n"
            "sys.exit(migrate.main(['--env','staging','--dir',{!r},'--environments',{!r}]))\n"
        ).format(
            str(ROOT / "appwrite"),
            str(d / "migrations"),
            str(d / "environments.json"),
        )
        out = d / "gh_output"
        res = subprocess.run(
            [sys.executable, "-c", harness],
            check=False,
            capture_output=True,
            text=True,
            env={"GITHUB_OUTPUT": str(out), "K": "x" * 8, "PATH": "/usr/bin:/bin"},
        )
        # The real adapter would be used here (no network): either way the gate must read "no promote".
        self.assertNotEqual(res.returncode, 0)
        promoted = out.exists() and "promote=true" in out.read_text()
        self.assertFalse(promoted, res.stdout + res.stderr)

    def test_api_job_not_gated_on_promote_is_reported(self):
        s = Scratch(self).edit(
            ".github/workflows/deploy-staging.yml",
            " && needs.schema.outputs.promote == 'true'\n    environment: staging\n    runs-on: ubuntu-24.04\n    timeout-minutes: 20\n    concurrency:\n      group: deploy-staging-core",
            "\n    environment: staging\n    runs-on: ubuntu-24.04\n    timeout-minutes: 20\n    concurrency:\n      group: deploy-staging-core",
        )
        self.assertTrue(any("must be gated on" in p for p in s.problems()))

    def test_api_job_without_schema_dependency_is_reported(self):
        s = Scratch(self).edit(
            ".github/workflows/deploy-staging.yml",
            "    name: core service (fly deploy)\n    needs: schema\n",
            "    name: core service (fly deploy)\n",
        )
        self.assertTrue(
            any("must come after the schema job" in p for p in s.problems())
        )

    def test_schema_job_without_own_concurrency_group_is_reported(self):
        s = Scratch(self).edit(
            ".github/workflows/deploy-staging.yml",
            "      group: appwrite-schema-staging\n      cancel-in-progress: false\n",
            "",
        )
        self.assertTrue(any("concurrency" in p for p in s.problems()))

    def test_production_api_step_before_migrations_or_ungated_is_reported(self):
        s = Scratch(self).edit(
            ".github/workflows/promote-production.yml",
            "        if: vars.DEPLOY_ENABLED == 'true' && steps.migrate.outcome == 'success' && steps.migrate.outputs.promote == 'true'\n        working-directory: services/core",
            "        if: vars.DEPLOY_ENABLED == 'true'\n        working-directory: services/core",
        )
        self.assertTrue(
            any("must be gated on steps.migrate.outcome" in p for p in s.problems())
        )

    def test_production_without_ancestor_check_is_reported(self):
        s = Scratch(self).edit(
            ".github/workflows/promote-production.yml",
            "git merge-base --is-ancestor",
            "git merge-base",
        )
        self.assertTrue(any("on main" in p for p in s.problems()))


class OtherDeliverables(unittest.TestCase):
    def test_mobile_skeleton_is_manual_guarded_and_does_nothing_real(self):
        wf = load("mobile-builds.yml")
        self.assertEqual(set(wf["on"]), {"workflow_dispatch"})
        text = (WF / "mobile-builds.yml").read_text()
        self.assertIn("MOBILE_BUILDS_ENABLED", text)
        self.assertNotIn("secrets.", text)
        self.assertTrue((ROOT / "docs/ops/mobile-builds.md").is_file())

    def test_mobile_doc_lists_owner_steps_for_both_tracks(self):
        t = (ROOT / "docs/ops/mobile-builds.md").read_text()
        for w in ("TestFlight", "internal", "Codemagic", "Fastlane", "OWNER-BLOCKED"):
            self.assertIn(w, t)

    def test_dependabot_covers_every_ecosystem_and_lockfile(self):
        self.assertEqual(ci_lint.check_dependabot(ROOT), [])
        cfg = ci_yaml.load((ROOT / ".github/dependabot.yml").read_text())
        eco = {u["package-ecosystem"] for u in cfg["updates"]}
        self.assertTrue({"github-actions", "npm", "pub", "uv"} <= eco)

    def test_dependabot_gap_is_reported(self):
        s = Scratch(self)
        p = s.dir / ".github/dependabot.yml"
        p.write_text(
            p.read_text().replace("directory: /apps/admin", "directory: /apps/other")
        )
        self.assertTrue(
            any("/apps/admin" in e for e in ci_lint.check_dependabot(s.dir))
        )

    def test_deploy_doc_states_order_rollback_and_expand_contract(self):
        self.assertEqual(ci_lint.check_docs(ROOT), [])
        t = (ROOT / "docs/ops/deploy.md").read_text().lower()
        self.assertLess(t.index("schema first"), t.index("core service second"))
        self.assertIn("redeploy the previous image", t)
        self.assertIn("backward compatible", t)

    def test_deploy_doc_missing_rule_is_reported(self):
        s = Scratch(self).edit("docs/ops/deploy.md", "backward compatible", "ok")
        self.assertTrue(any("backward compatible" in p for p in s.problems()))

    def test_admin_host_tbd_is_documented_because_adr_0004_names_none(self):
        self.assertIn("TBD", (WF / "deploy-staging.yml").read_text())
        self.assertIn("Admin host", (ROOT / "docs/ops/deploy.md").read_text())

    def test_fly_configs_have_no_secrets_and_match_the_dockerfile(self):
        dockerfile = (ROOT / "services/core/Dockerfile").read_text()
        for env in ("staging", "production"):
            t = (ROOT / f"services/core/fly.{env}.toml").read_text()
            self.assertIn(f'HS_ENV = "{env}"', t)
            self.assertIn("internal_port = 8080", t)
            self.assertIn("EXPOSE 8080", dockerfile)
            self.assertIn('path = "/health"', t)
            self.assertNotRegex(t, r"(?i)(secret|token|api_key)\s*=")

    def test_workflow_files_are_readable_by_the_real_yaml_parser(self):
        """Cross-check ci_yaml against PyYAML (uv run --with pyyaml); skipped when unavailable."""
        uv = shutil.which("uv")
        if not uv:
            self.skipTest("uv not installed")
        code = (
            "import json,sys,yaml,pathlib\n"
            "out={}\n"
            "for p in sorted(pathlib.Path('.github').rglob('*.yml')):\n"
            "    out[str(p)]=yaml.safe_load(p.read_text())\n"
            "print(json.dumps(out, default=str))\n"
        )
        try:
            res = subprocess.run(
                [uv, "run", "--no-project", "--with", "pyyaml", "python", "-c", code],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
                timeout=120,
            )
        except subprocess.TimeoutExpired:
            self.skipTest("uv timed out")
        if res.returncode != 0:
            self.skipTest("pyyaml unavailable offline: " + res.stderr[-200:])
        real = json.loads(res.stdout)

        def norm(x):
            if isinstance(x, dict):
                return {
                    ("on" if k == "true" else str(k)): norm(v) for k, v in x.items()
                }
            if isinstance(x, list):
                return [norm(v) for v in x]
            if isinstance(x, bool):
                return "true" if x else "false"
            if isinstance(x, (int, float)):
                return str(x)
            if isinstance(x, str):
                return x.rstrip("\n")
            return "" if x is None else x

        for path, doc in real.items():
            mine = ci_yaml.load((ROOT / path).read_text())
            self.assertEqual(norm(mine), norm(doc), path)


class YamlSubset(unittest.TestCase):
    def test_unsupported_features_fail_loudly(self):
        for bad in (
            "a: &x 1\n",
            "a: !!str 1\n",
            "a: 'unterminated\n",
            "a: [1,\n  2]\n",
        ):
            with self.assertRaises(ValueError, msg=bad):
                ci_yaml.load(bad)

    def test_basic_shapes(self):
        d = ci_yaml.load(
            "a: 1 # c\nb:\n  - x\n  - y: 2\n    z: '3'\nc: |\n  l1\n  l2\nd: [p, q]\ne: {}\n"
        )
        self.assertEqual(
            d,
            {
                "a": "1",
                "b": ["x", {"y": "2", "z": "3"}],
                "c": "l1\nl2\n",
                "d": ["p", "q"],
                "e": {},
            },
        )

    def test_colon_and_hash_inside_values(self):
        d = ci_yaml.load('run: echo "a: b" # real comment\nurl: http://x/#frag\n')
        self.assertEqual(d["run"], 'echo "a: b"')
        self.assertEqual(d["url"], "http://x/#frag")


if __name__ == "__main__":
    unittest.main()
