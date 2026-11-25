#!/usr/bin/env python3
"""Lint the GitHub workflow files against the F07 rules. Stdlib only, offline.

usage: ci_lint.py [--root DIR]      exit 0 clean, 1 with one line per violation.
Each rule has a positive and a negative test in scripts/tests/test_workflows.py. This complements
(does not replace) actionlint; see docs/ops/ci.md.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ci_yaml

ROOT = Path(__file__).resolve().parents[1]
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
FLAG = "vars.DEPLOY_ENABLED == 'true'"
# Actions allowed to be pinned by tag instead of SHA (owner/reviewer to pin). Empty: all are pinned.
UNPINNED_ALLOWED: dict[str, str] = {}
# Implemented packages with no CI job, and why. Anything else needs a job (HS_PACKAGES) in ci.yml.
EXEMPT_PACKAGES = {
    "spike-admin": "throwaway spike (CLAUDE.md); run locally via make test, not worth CI minutes",
    "spike-jobs": "throwaway spike (CLAUDE.md); run locally via make test, not worth CI minutes",
}
# Jobs may name these before packages.json lists them (their ticket merges later).
OPTIONAL_PACKAGES = {"ops"}
DEPLOY_MARKERS = (
    "flyctl",
    "fly deploy",
    "appwrite ",
    "migrate.py",
    "npm i -g appwrite",
    "appwrite-cli",
)
RUN_INJECTION = re.compile(r"\$\{\{\s*(github\.event\.|github\.head_ref|inputs\.)")


def load_workflows(root: Path) -> dict[str, dict]:
    out = {}
    for p in sorted((root / ".github" / "workflows").glob("*.yml")):
        out[p.name] = ci_yaml.load(p.read_text(encoding="utf-8"))
    return out


def triggers(wf: dict) -> set[str]:
    on = wf.get("on", {})
    if isinstance(on, str):
        return {on}
    if isinstance(on, list):
        return set(on)
    return set(on)


def jobs(wf: dict) -> dict[str, dict]:
    return wf.get("jobs", {}) or {}


def steps(job: dict) -> list[dict]:
    return job.get("steps", []) or []


def needs_of(job: dict) -> list[str]:
    n = job.get("needs", [])
    return [n] if isinstance(n, str) else list(n)


def _text(obj) -> str:
    return json.dumps(obj, sort_keys=True)


def check_workflows(
    wfs: dict[str, dict], manifest: dict, root: Path | None = None
) -> list[str]:
    errs: list[str] = []
    e = errs.append

    for name, wf in wfs.items():
        trig = triggers(wf)
        if "pull_request_target" in trig:
            e(f"{name}: pull_request_target is forbidden (runs fork code with secrets)")
        perms = wf.get("permissions")
        if perms is None:
            e(f"{name}: top-level `permissions` missing (default token is too broad)")
        elif perms == "write-all" or (
            isinstance(perms, dict) and "write" in perms.values()
        ):
            e(f"{name}: top-level permissions must be read-only, got {perms!r}")
        for jid, job in jobs(wf).items():
            jp = job.get("permissions")
            if jp == "write-all":
                e(f"{name}:{jid}: write-all permissions")
            if "timeout-minutes" not in job:
                e(
                    f"{name}:{jid}: timeout-minutes missing (a hung job burns minutes for 6 h)"
                )
            for i, st in enumerate(steps(job)):
                uses = st.get("uses")
                if uses and not uses.startswith("./"):
                    action, _, ref = uses.partition("@")
                    base = "/".join(action.split("/")[:2])
                    if not SHA_RE.match(ref) and base not in UNPINNED_ALLOWED:
                        e(
                            f"{name}:{jid}: step {i + 1} `{uses}` is not pinned to a full commit SHA"
                        )
                if (
                    uses
                    and uses.startswith("actions/checkout")
                    and (st.get("with") or {}).get("persist-credentials") != "false"
                ):
                    e(f"{name}:{jid}: checkout must set persist-credentials: false")
                run = st.get("run", "")
                if RUN_INJECTION.search(run):
                    e(
                        f"{name}:{jid}: step {i + 1} interpolates untrusted context into a shell script"
                    )
        if "pull_request" in trig:
            blob = _text(wf)
            for m in re.findall(r"secrets\.([A-Za-z0-9_]+)", blob):
                if m != "GITHUB_TOKEN":
                    e(
                        f"{name}: uses secrets.{m} in a pull_request workflow (forks must get no secrets)"
                    )
            if '"environment"' in blob:
                e(
                    f"{name}: pull_request workflow must not use a deployment environment"
                )
            if "DEPLOY_ENABLED" in blob:
                e(f"{name}: pull_request workflow must not touch deploy variables")

    # ---- ci.yml
    ci = wfs.get("ci.yml")
    if ci is None:
        e("ci.yml missing")
    else:
        trig = triggers(ci)
        if "pull_request" not in trig or "push" not in trig:
            e("ci.yml must run on pull_request and push")
        if "main" not in _text((ci.get("on") or {}).get("push", "")):
            e("ci.yml push trigger must be limited to main")
        conc = ci.get("concurrency") or {}
        if not conc.get("group") or "pull_request" not in str(
            conc.get("cancel-in-progress", "")
        ):
            e(
                "ci.yml needs a concurrency group with cancel-in-progress for pull requests only"
            )
        if (ci.get("env") or {}).get("HS_STRICT") != "1":
            e(
                "ci.yml must set HS_STRICT: '1' at workflow level (missing tools fail, not skip)"
            )
        cj = jobs(ci)
        for jid, job in cj.items():
            if "HS_STRICT" in (job.get("env") or {}) and jid != "scripts":
                e(
                    f"ci.yml:{jid}: only the `scripts` job may override HS_STRICT (it nests `make test`)"
                )
        gate = cj.get("ci-ok")
        if gate is None:
            e("ci.yml has no `ci-ok` aggregate job")
        else:
            others = set(cj) - {"ci-ok"}
            if set(needs_of(gate)) != others:
                e(
                    f"ci-ok must need every other job; missing {sorted(others - set(needs_of(gate)))}, "
                    f"extra {sorted(set(needs_of(gate)) - others)}"
                )
            if "always()" not in str(gate.get("if", "")):
                e(
                    "ci-ok must run with `if: always()` so skipped/failed needs still report"
                )
            g = _text(gate)
            if "needs.*.result" not in g or "failure" not in g or "cancelled" not in g:
                e(
                    "ci-ok must fail when any needed job failed or was cancelled (needs.*.result)"
                )
            if (
                "skipped" in str(gate.get("run", ""))
                and "exit 1" in g
                and "skipped" in g.split("failure")[0]
            ):
                pass
        # package coverage
        covered: dict[str, str] = {}
        for jid, job in cj.items():
            for pid in (
                str((job.get("env") or {}).get("HS_PACKAGES", ""))
                .replace(" ", "")
                .split(",")
            ):
                if pid:
                    covered[pid] = jid
        for pkg in manifest["packages"]:
            if pkg.get("status") != "implemented":
                continue
            pid = pkg["id"]
            if pid in EXEMPT_PACKAGES:
                if not EXEMPT_PACKAGES[pid].strip():
                    e(f"exemption for {pid} has no reason")
                continue
            if pid not in covered:
                e(
                    f"package {pid} ({pkg['path']}) has no CI job (env HS_PACKAGES) and no exemption"
                )
                continue
            job = cj[covered[pid]]
            matrix = "matrix" in _text(job.get("strategy", {}))
            want_dir = "${{ matrix.path }}" if matrix else pkg["path"]
            for kind in ("test", "lint"):
                for seg in [
                    s.strip() for s in (pkg.get(kind) or "").split("&&") if s.strip()
                ]:
                    if not any(
                        seg in st.get("run", "")
                        and st.get("working-directory") == want_dir
                        for st in steps(job)
                    ):
                        e(
                            f"job {covered[pid]} lacks `{seg}` in working-directory {want_dir} "
                            f"({kind} command of {pid} in packages.json)"
                        )
        for pid in covered:
            if pid not in {p["id"] for p in manifest["packages"]} | OPTIONAL_PACKAGES:
                e(f"ci.yml job claims unknown package {pid}")
        for jid, job in cj.items():
            if jid in ("ci-ok", "changes"):
                continue
            if (
                "needs.changes.outputs" not in str(job.get("if", ""))
                and jid not in ALWAYS_ON
            ):
                e(
                    f"ci.yml:{jid}: no path filter (`if: needs.changes.outputs...`) and not in the always-on list"
                )

    # ---- deploy workflows
    for name, env_name in (
        ("deploy-staging.yml", "staging"),
        ("promote-production.yml", "production"),
        ("mobile-builds.yml", None),
    ):
        wf = wfs.get(name)
        if wf is None:
            e(f"{name} missing")
            continue
        trig = triggers(wf)
        if trig - {"push", "workflow_dispatch"}:
            e(f"{name}: unexpected triggers {sorted(trig)}")
        if name == "promote-production.yml" and trig != {"workflow_dispatch"}:
            e(f"{name}: production promotion must be manual (workflow_dispatch only)")
        if name == "deploy-staging.yml" and "push" not in trig:
            e(f"{name}: staging deploys on push to main")
        if name == "deploy-staging.yml" and "main" not in _text(
            (wf.get("on") or {}).get("push", "")
        ):
            e(f"{name}: push trigger must be limited to main")
        for jid, job in jobs(wf).items():
            where = f"{name}:{jid}"
            if FLAG not in str(job.get("if", "")):
                e(
                    f"{where}: job `if` must contain {FLAG} (nothing deploys until the owner enables it)"
                )
            if env_name and job.get("environment") != env_name:
                e(f"{where}: must use `environment: {env_name}`")
            for i, st in enumerate(steps(job)):
                blob = _text(st)
                is_deploy = (
                    any(m in st.get("run", "") for m in DEPLOY_MARKERS)
                    or "secrets." in blob
                    or "flyctl-actions" in st.get("uses", "")
                )
                if is_deploy and FLAG not in str(st.get("if", "")):
                    e(
                        f"{where}: step {i + 1} ({st.get('name', '?')}) deploys or uses secrets without `if: {FLAG}`"
                    )
            # secrets only inside env/with of guarded steps; not at job level
            if "secrets." in _text({k: v for k, v in job.items() if k != "steps"}):
                e(
                    f"{where}: secrets at job level; scope them to the step that needs them"
                )
        conc_all = _text(wf.get("concurrency", "")) + _text(
            [j.get("concurrency") for j in jobs(wf).values()]
        )
        if "cancel-in-progress" not in conc_all:
            e(f"{name}: no concurrency group")
        if '"cancel-in-progress": "true"' in conc_all.replace("'", '"'):
            e(f"{name}: never cancel a running deploy or migration")
        if name in ("deploy-staging.yml", "promote-production.yml"):
            e_list = check_deploy_order(name, wf)
            errs.extend(e_list)
        if name == "promote-production.yml":
            blob = _text(wf)
            if "merge-base --is-ancestor" not in blob:
                e(
                    f"{name}: must verify the promoted sha is on main (git merge-base --is-ancestor)"
                )
    return errs


ALWAYS_ON = {"repo-checks"}


def check_deploy_order(name: str, wf: dict) -> list[str]:
    errs = []
    js = jobs(wf)
    schema = [j for j, v in js.items() if "migrate.py" in _text(v)]
    if len(schema) != 1:
        return [
            f"{name}: expected exactly one job running appwrite/migrate.py, found {schema}"
        ]
    sj = schema[0]
    sjob = js[sj]
    conc = sjob.get("concurrency") or {}
    if (
        not isinstance(conc, dict)
        or not conc.get("group")
        or str(conc.get("cancel-in-progress")) != "false"
    ):
        errs.append(
            f"{name}:{sj}: schema job needs its own concurrency group with cancel-in-progress: false"
        )
    if "promote" not in (sjob.get("outputs") or {}):
        errs.append(f"{name}:{sj}: must export the runner's `promote` output")
    mig = [s for s in steps(sjob) if "migrate.py" in s.get("run", "")]
    if not mig or not mig[0].get("id"):
        errs.append(
            f"{name}:{sj}: the migrate step needs an id so its outputs can gate promotion"
        )
    sid = mig[0].get("id") if mig else None
    seen_migrate = False
    for st in steps(sjob):
        if st is mig[0]:
            seen_migrate = True
            continue
        text = _text(st)
        is_deploy = any(
            m in text
            for m in ("flyctl", "appwrite-cli", "flyctl-actions", "npm run build")
        )
        if not is_deploy:
            continue
        label = st.get("name") or st.get("uses") or "?"
        if not seen_migrate:
            errs.append(
                f"{name}:{sj}: `{label}` deploys before the migrations (schema first, API second)"
            )
        ifs = str(st.get("if", ""))
        if sid and (
            f"steps.{sid}.outcome == 'success'" not in ifs
            or f"steps.{sid}.outputs.promote == 'true'" not in ifs
        ):
            errs.append(
                f"{name}:{sj}: `{label}` must be gated on steps.{sid}.outcome == 'success' and "
                f"steps.{sid}.outputs.promote == 'true'"
            )
    for jid, job in js.items():
        if jid == sj:
            continue
        deploys = any(
            m in _text(job.get("steps", []))
            for m in ("flyctl", "appwrite push", "appwrite-cli", "npm run build")
        )
        if not deploys:
            continue
        ifs = str(job.get("if", ""))
        if sj not in needs_of(job) and not _reaches(js, jid, sj):
            errs.append(
                f"{name}:{jid}: deploy job must come after the schema job `{sj}` (schema first, API second)"
            )
        if (
            f"needs.{sj}.outputs.promote == 'true'" not in ifs
            or f"needs.{sj}.result == 'success'" not in ifs
        ):
            errs.append(
                f"{name}:{jid}: must be gated on needs.{sj}.result == 'success' and "
                f"needs.{sj}.outputs.promote == 'true'"
            )
    return errs


def _reaches(js: dict, start: str, target: str, seen=None) -> bool:
    seen = seen or set()
    for n in needs_of(js[start]):
        if n == target or (
            n not in seen and n in js and _reaches(js, n, target, seen | {n})
        ):
            return True
    return False


def check_dependabot(root: Path) -> list[str]:
    p = root / ".github" / "dependabot.yml"
    if not p.is_file():
        return [".github/dependabot.yml missing"]
    cfg = ci_yaml.load(p.read_text(encoding="utf-8"))
    have = {
        (u.get("package-ecosystem"), u.get("directory")) for u in cfg.get("updates", [])
    }
    errs = []
    for u in cfg.get("updates", []):
        if not (u.get("schedule") or {}).get("interval"):
            errs.append(
                f"dependabot: {u.get('package-ecosystem')} {u.get('directory')} has no schedule"
            )
    need = []
    for lock, eco in (
        ("uv.lock", ("uv", "pip")),
        ("package-lock.json", ("npm",)),
        ("pubspec.yaml", ("pub",)),
    ):
        for f in sorted(root.rglob(lock)):
            rel = f.relative_to(root)
            if any(
                part in ("node_modules", "spikes", ".dart_tool", "build")
                for part in rel.parts
            ):
                continue
            need.append(("/" + str(rel.parent) if str(rel.parent) != "." else "/", eco))
    for d, eco in need:
        if not any(e in eco and dd == d for e, dd in have):
            errs.append(f"dependabot: no {'/'.join(eco)} entry for {d}")
    if ("github-actions", "/") not in have:
        errs.append("dependabot: github-actions entry for / missing")
    return errs


def check_docs(root: Path) -> list[str]:
    p = root / "docs" / "ops" / "deploy.md"
    if not p.is_file():
        return ["docs/ops/deploy.md missing"]
    t = p.read_text(encoding="utf-8").lower()
    errs = []
    for need in (
        "rollback",
        "schema",
        "expand",
        "contract",
        "backward compatible",
        "promote",
        "deploy_enabled",
    ):
        if need not in t:
            errs.append(f"docs/ops/deploy.md must mention `{need}`")
    return errs


def run(root: Path = ROOT) -> list[str]:
    manifest = json.loads((root / "packages.json").read_text(encoding="utf-8"))
    try:
        wfs = load_workflows(root)
    except ValueError as exc:
        return [f"workflow YAML not readable by ci_yaml: {exc}"]
    return (
        check_workflows(wfs, manifest, root) + check_dependabot(root) + check_docs(root)
    )


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--root", default=str(ROOT))
    a = ap.parse_args(argv)
    errs = run(Path(a.root))
    for line in errs:
        print("FAIL", line)
    print(f"ci_lint: {len(errs)} problem(s)" if errs else "ci_lint: ok")
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main())
