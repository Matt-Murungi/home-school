#!/usr/bin/env python3
"""Validate the secrets inventory and configuration (F09, #35). Stdlib only, offline, never prints values.

usage: secrets_check.py [--write]
  (no flag)  validate docs/ops/secrets-inventory.json against the repo; exit 1 on any error
  --write    regenerate the generated tables in docs/ops/config-matrix.md and docs/runbooks/secrets.md

Checks: schema; consumer/environment scope (production secrets never in dev/staging, no shared stores,
live payment values only in production); server secrets have an owner, interval, blast radius and a rotation
procedure of at least 3 steps; no server secret in an app bundle; public client keys are restricted; every
variable read by services/core settings and by apps appears in the inventory; SecretStr settings are server
secrets; services/core/.env.example lists every setting with an empty value; Appwrite key intervals agree
with appwrite/keys.md; generated docs are in sync; the runbook has the required sections.
"""

import ast
import json
import re
import sys
from pathlib import Path

ENVS = ("development", "staging", "production")
KINDS = {"server_secret", "public_client_key", "non_secret"}
LOCATIONS = {
    "fly_secret", "appwrite_function_variable", "github_actions_secret", "provider_console",
    "build_define", "fly_env", "github_actions_env", "host_env",
    "codemagic_variable", "app_store_console",
}  # fmt: skip
SECRET_LIKE = re.compile(r"(?i)(SECRET|PASSWORD|PASSWD|PRIVATE|TOKEN|API_?KEY)")
VALUE_LIKE = re.compile(
    r"standard_[0-9a-f]{20,}|-----BEGIN [A-Z ]*PRIVATE KEY|\b(AIza|fo1_|atsk_)[A-Za-z0-9_-]{20,}"
)
APP_BUILTINS = {"NODE_ENV", "CI", "PORT", "NEXT_RUNTIME", "VERCEL", "HOSTNAME"}
# a secret shared by several consumers must list every one (removing a row is a missing consumer)
REQUIRED_CONSUMERS = {"JOB_TICK_SECRET": {"core-service", "job-tick-function"}}
GEN = "<!-- BEGIN GENERATED:{n} -->", "<!-- END GENERATED:{n} -->"
RUNBOOK_SECTIONS = (
    "Routine rotation",
    "Leak response",
    "Committed to git",
    "Rotation drill",
)
SKIP_DIRS = {
    "node_modules",
    "build",
    ".next",
    ".dart_tool",
    ".venv",
    "__pycache__",
    ".git",
}
INV = "docs/ops/secrets-inventory.json"
UPTIME = "ops/uptime.json"
# ADR 0003 names the Fly apps `homeschool-core-staging` and `homeschool-core-prod`, so a store may end in the
# short environment name as well as the long one.
ENV_SUFFIXES = {
    "development": ("development", "dev"),
    "staging": ("staging",),
    "production": ("production", "prod"),
}


def load(root: Path):
    return json.loads((root / INV).read_text())


def resolve_procedure(inv, e):
    return inv.get("procedures", {}).get(e.get("procedure"), [])


# ---- config discovery ------------------------------------------------------------------------


def core_settings_fields(root: Path) -> dict[str, bool]:
    """{ENV_NAME: is_secret} for fields of services/core Settings (secret = annotated SecretStr)."""
    path = root / "services/core/src/hs_core/settings.py"
    out: dict[str, bool] = {}
    if not path.exists():
        return out
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.ClassDef) and node.name == "Settings":
            for st in node.body:
                if (
                    isinstance(st, ast.AnnAssign)
                    and isinstance(st.target, ast.Name)
                    and not st.target.id.startswith("_")
                ):
                    if st.target.id == "model_config":
                        continue
                    out[st.target.id.upper()] = "SecretStr" in ast.unparse(
                        st.annotation
                    )
    return out


APP_PATTERNS = [
    re.compile(r"process\.env\.([A-Z][A-Z0-9_]*)"),
    re.compile(r"process\.env\[\s*['\"]([A-Z][A-Z0-9_]*)['\"]\s*\]"),
    re.compile(
        r"(?<![\w.])env\??\.([A-Z][A-Z0-9_]*)"
    ),  # an `env` object passed in (admin config.ts)
    re.compile(r"fromEnvironment\(\s*['\"]([A-Za-z][A-Za-z0-9_]*)['\"]"),
]


def app_config_keys(root: Path) -> set[str]:
    keys: set[str] = set()
    base = root / "apps"
    if not base.exists():
        return keys
    for p in base.rglob("*"):
        if not p.is_file() or p.suffix not in {
            ".ts",
            ".tsx",
            ".js",
            ".jsx",
            ".mjs",
            ".dart",
        }:
            continue
        if set(p.relative_to(base).parts) & SKIP_DIRS:
            continue
        text = p.read_text(errors="ignore")
        for rx in APP_PATTERNS:
            keys.update(rx.findall(text))
    return keys - APP_BUILTINS


def env_example_names(root: Path):
    path = root / "services/core/.env.example"
    rows = {}
    if path.exists():
        for line in path.read_text().splitlines():
            if line.strip() and not line.lstrip().startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                rows[k.strip()] = v.strip()
    return rows


def check_env_example(root: Path) -> list[str]:
    fields = core_settings_fields(root)
    rows = env_example_names(root)
    out = []
    for n in fields:
        if n not in rows:
            out.append(f"{n} missing from services/core/.env.example")
    for n, v in rows.items():
        if v:
            out.append(f"{n} must be empty in .env.example (value not shown)")
        if n not in fields:
            out.append(f"{n} in .env.example is not read by services/core settings")
    return out


# ---- generated docs ------------------------------------------------------------------------------


def cell(e, env):
    if env not in e.get("environments", []):
        return "-"
    if e.get("kind") == "non_secret":
        return e.get("values", {}).get(env, "-")
    if e.get("kind") == "public_client_key":
        return "build define"
    vc = e.get("value_class", {}).get(env)
    store = e.get("stores", {}).get(env, "?")
    if e.get("owner_blocked"):
        store += " (owner-blocked)"
    return store + (f" ({vc})" if vc else "")


def gen_matrix(inv) -> str:
    rows = [
        "| Variable | Kind | Consumer | development | staging | production | Where it lives |",
        "|---|---|---|---|---|---|---|",
    ]
    order = {"server_secret": 0, "public_client_key": 1, "non_secret": 2}
    for e in sorted(
        inv["entries"],
        key=lambda e: (
            order.get(e.get("kind"), 9),
            e.get("name", ""),
            e.get("consumer", ""),
        ),
    ):
        label = {
            "server_secret": "server secret",
            "public_client_key": "public client key",
            "non_secret": "config",
        }.get(e.get("kind"), "?")
        rows.append(
            f"| `{e.get('name')}` | {label} | {e.get('consumer')} | "
            + " | ".join(cell(e, env).replace("|", "/") for env in ENVS)
            + f" | {e.get('location')} |"
        )
    return "\n".join(rows)


def gen_schedule(inv) -> str:
    rows = [
        "| Secret | Consumer | Owner | Rotate every | Procedure | Overlap | Blast radius if leaked |",
        "|---|---|---|---|---|---|---|",
    ]
    for e in sorted(
        (e for e in inv["entries"] if e.get("kind") == "server_secret"),
        key=lambda e: (e.get("name", ""), e.get("consumer", "")),
    ):
        rows.append(
            f"| `{e.get('name')}` | {e.get('consumer')}{' (owner-blocked)' if e.get('owner_blocked') else ''} | "
            f"{e.get('rotation_owner', '?')} | {e.get('rotation_interval_days', '?')} d | "
            f"{e.get('procedure', '?')} | {'yes' if e.get('rotation_overlap') else 'no'} | {e.get('blast_radius', '?')} |"
        )
    return "\n".join(rows)


def gen_procedures(inv) -> str:
    out = []
    for name, steps in sorted(inv["procedures"].items()):
        users = sorted(
            {
                f"`{e.get('name')}` ({e.get('consumer')})"
                for e in inv["entries"]
                if e.get("procedure") == name
            }
        )
        out.append(f"### Procedure `{name}`\n\nUsed by: {', '.join(users) or 'none'}\n")
        out += [f"{i}. {s}" for i, s in enumerate(steps, 1)]
        out.append("")
    return "\n".join(out).rstrip()


GENERATED = {
    "docs/ops/config-matrix.md": {"matrix": gen_matrix},
    "docs/runbooks/secrets.md": {
        "schedule": gen_schedule,
        "procedures": gen_procedures,
    },
}


def _block(text, name):
    b, e = (g.format(n=name) for g in GEN)
    if b not in text or e not in text:
        return None
    return text.split(b, 1)[1].split(e, 1)[0].strip("\n")


def check_docs(root: Path, inv) -> list[str]:
    out = []
    for rel, blocks in GENERATED.items():
        p = root / rel
        if not p.exists():
            out.append(f"{rel} is missing")
            continue
        text = p.read_text()
        for name, fn in blocks.items():
            cur = _block(text, name)
            if cur is None:
                out.append(f"{rel}: generated block {name!r} missing")
            elif cur != fn(inv):
                out.append(
                    f"{rel}: generated block {name!r} is out of date; run `python3 scripts/secrets_check.py --write`"
                )
        if rel.endswith("secrets.md"):
            for h in RUNBOOK_SECTIONS:
                if not re.search(rf"(?im)^#+\s*{re.escape(h)}", text):
                    out.append(f"{rel}: missing section {h!r}")
    return out


def write_docs(root: Path, inv):
    for rel, blocks in GENERATED.items():
        p = root / rel
        text = p.read_text()
        for name, fn in blocks.items():
            b, e = (g.format(n=name) for g in GEN)
            head, rest = text.split(b, 1)
            _, tail = rest.split(e, 1)
            text = f"{head}{b}\n{fn(inv)}\n{e}{tail}"
        p.write_text(text)


# ---- validation ----------------------------------------------------------------------------------


def _days(expiry: str) -> int | None:
    m = re.match(r"^(\d+)\s*(d|days|w|weeks|m|months)$", expiry.strip(), re.IGNORECASE)
    if not m:
        return None
    return int(m.group(1)) * {"d": 1, "w": 7, "m": 30}[m.group(2).lower()[0]]


def _keys_md(root: Path):
    sys.path.insert(0, str(root / "appwrite"))
    try:
        import check_keys  # type: ignore
    finally:
        sys.path.pop(0)
    p = root / "appwrite/keys.md"
    return check_keys.parse(p.read_text()) if p.exists() else []


def shape_errors(inv) -> list[str]:
    """Type problems that would make later checks crash; every message names the field."""
    if not isinstance(inv, dict):
        return ["inventory must be a JSON object"]
    out: list[str] = []
    for key, typ, label in (
        ("environments", list, "a list"),
        ("consumers", dict, "an object"),
        ("procedures", dict, "an object"),
        ("entries", list, "a list"),
    ):
        if key not in inv:
            out.append(f"top-level {key} is missing")
        elif not isinstance(inv[key], typ):
            out.append(f"top-level {key} must be {label}")
    if out:
        return out
    if inv["environments"] != list(ENVS):
        out.append(f"environments must be exactly {list(ENVS)}")
    for n, c in inv["consumers"].items():
        if not isinstance(c, dict) or not isinstance(
            c.get("allowed_environments"), list
        ):
            out.append(
                f"consumer {n}: must be an object with an allowed_environments list"
            )
    for n, steps in inv["procedures"].items():
        if not isinstance(steps, list) or not all(isinstance(x, str) for x in steps):
            out.append(f"procedure {n}: must be a list of text steps")
    text_fields = (
        "name",
        "kind",
        "consumer",
        "location",
        "procedure",
        "rotation_owner",
        "blast_radius",
        "downtime_note",
        "notes",
        "restriction",
    )
    for idx, e in enumerate(inv["entries"]):
        if not isinstance(e, dict):
            out.append(f"entries[{idx}] must be an object")
            continue
        who = f"{e['name']}" if isinstance(e.get("name"), str) else f"entries[{idx}]"
        for f in text_fields:
            if f in e and not isinstance(e[f], str):
                out.append(f"{who}: {f} must be text")
        for f in ("stores", "values", "value_class"):
            if f in e and not isinstance(e[f], dict):
                out.append(f"{who}: {f} must be an object")
        if "environments" in e and (
            not isinstance(e["environments"], list)
            or not all(isinstance(x, str) for x in e["environments"])
        ):
            out.append(f"{who}: environments must be a list of text")
        for f in ("rotation_overlap", "owner_blocked", "value_per_environment"):
            if f in e and not isinstance(e[f], bool):
                out.append(f"{who}: {f} must be true or false")
    return out


def uptime_vars(root: Path) -> dict[str, str]:
    """{variable: 'secret'|'config'} named in ops/uptime.json (heartbeat URL = capability secret)."""
    p = root / UPTIME
    if not p.exists():
        return {}
    data = json.loads(p.read_text())
    out: dict[str, str] = {}
    hb = data.get("heartbeat", {}).get("url_var")
    if hb:
        out[hb] = "secret"
    for c in data.get("checks", []):
        if c.get("base_url_var"):
            out[c["base_url_var"]] = "config"
    return out


def validate(inv, root: Path, extra_app_keys=None) -> list[str]:
    bad = shape_errors(inv)
    if bad:
        return bad
    errs: list[str] = []
    entries = inv.get("entries", [])
    consumers = inv.get("consumers", {})
    seen = set()
    if VALUE_LIKE.search(json.dumps(inv)):
        errs.append(
            "inventory contains something that looks like a secret value (names and metadata only)"
        )
    for e in entries:
        who = f"{e.get('name', '?')} ({e.get('consumer', '?')})"
        for f in ("name", "kind", "consumer", "environments", "location"):
            if not e.get(f):
                errs.append(f"{who}: missing field {f}")
        if not e.get("name") or not e.get("kind"):
            continue
        key = (e["name"], e.get("consumer"))
        if key in seen:
            errs.append(f"{who}: duplicate entry")
        seen.add(key)
        kind = e["kind"]
        if kind not in KINDS:
            errs.append(f"{who}: unknown kind {kind!r}")
            continue
        if e.get("location") not in LOCATIONS:
            errs.append(f"{who}: unknown location {e.get('location')!r}")
        c = consumers.get(e.get("consumer"))
        if c is None:
            errs.append(f"{who}: unknown consumer {e.get('consumer')!r}")
        envs = e.get("environments", [])
        for env in envs:
            if env not in ENVS:
                errs.append(f"{who}: unknown environment {env!r}")
            elif c is not None and env not in c["allowed_environments"]:
                errs.append(
                    f"{who}: consumer {e['consumer']} may not hold {env} secrets"
                )
        for env, vc in e.get("value_class", {}).items():
            if vc == "live" and env != "production":
                errs.append(
                    f"{who}: live value outside production ({env}); production secrets never exist in dev/staging"
                )
        if kind == "non_secret":
            if SECRET_LIKE.search(e["name"]) and not e["name"].startswith(
                "APPWRITE_API_KEY_"
            ):
                errs.append(f"{who}: name looks secret but kind is non_secret")
            for env in envs:
                if not e.get("values", {}).get(env):
                    errs.append(f"{who}: missing value description for {env}")
            continue
        stores = e.get("stores", {})
        blocked = e.get("owner_blocked") is True
        for env in envs:
            s = stores.get(env)
            if not s:
                errs.append(f"{who}: no store for {env}")
            elif blocked and s == "TBD":
                continue  # owner-blocked: store not chosen yet; owner, interval, blast radius, procedure still required
            elif not s.endswith(ENV_SUFFIXES.get(env, (env,))):
                errs.append(
                    f"{who}: store {s!r} is not a {env} store (production secrets never in dev/staging)"
                )
        if not blocked and len({stores.get(env) for env in envs}) < len(envs):
            errs.append(
                f"{who}: shared store between environments (one key per environment)"
            )
        if kind == "public_client_key":
            if e.get("location") != "build_define":
                errs.append(f"{who}: public client key must be a build_define")
            if not str(e.get("restriction", "")).strip():
                errs.append(
                    f"{who}: public client key needs a restriction (package id, quota, allowed origins)"
                )
            continue
        # server_secret
        if e.get("location") == "build_define":
            errs.append(f"{who}: server secret must not go into an app bundle (SEC-3)")
        if len(envs) > 1 and e.get("value_per_environment") is not True:
            errs.append(
                f"{who}: value_per_environment must be true (a distinct value per environment)"
            )
        for f in ("rotation_owner", "blast_radius"):
            if not str(e.get(f, "")).strip():
                errs.append(f"{who}: missing {f}")
        iv = e.get("rotation_interval_days")
        if not isinstance(iv, int) or not 1 <= iv <= 365:
            errs.append(f"{who}: rotation_interval_days must be 1..365")
        steps = resolve_procedure(inv, e)
        if not e.get("procedure") or not steps:
            errs.append(
                f"{who}: no rotation procedure ({e.get('procedure')!r} not defined)"
            )
        elif len(steps) < 3:
            errs.append(f"{who}: procedure {e['procedure']} needs at least 3 steps")
        if "rotation_overlap" not in e:
            errs.append(f"{who}: rotation_overlap must be true or false")
        elif (
            e["rotation_overlap"] is False
            and not str(e.get("downtime_note", "")).strip()
        ):
            errs.append(
                f"{who}: rotation_overlap is false, so downtime_note is required"
            )
        if (
            e.get("procedure") == "payment-credential"
            and e.get("value_class", {}).get("production") != "live"
        ):
            errs.append(
                f"{who}: payment credentials need value_class (sandbox outside production, live in production)"
            )

    held: dict[str, set] = {}
    for e in entries:
        if e.get("name") and e.get("kind") == "server_secret":
            held.setdefault(e["name"], set()).add(e.get("consumer"))
    for n, need in REQUIRED_CONSUMERS.items():
        for c in sorted(need - held.get(n, set())):
            errs.append(
                f"{n}: missing consumer {c} (a secret shared by several consumers must list each one)"
            )

    # variables named in ops/uptime.json (no Settings field, so the code scan cannot see them)
    by_name = {e["name"]: e for e in entries if e.get("name")}
    for n, cls in sorted(uptime_vars(root).items()):
        e = by_name.get(n)
        if e is None:
            errs.append(f"{n}: named in ops/uptime.json but missing from the inventory")
        elif cls == "secret" and e.get("kind") != "server_secret":
            errs.append(
                f"{n}: heartbeat URL is a capability secret, so it must be a server_secret"
            )
        elif cls == "config" and e.get("kind") == "server_secret":
            errs.append(
                f"{n}: uptime base URL is a hostname, so it must be non_secret config"
            )

    # config keys read by code must be in the inventory
    names = {e["name"]: e for e in entries if e.get("name")}
    fields = core_settings_fields(root)
    for n, is_secret in fields.items():
        if n not in names:
            errs.append(
                f"{n}: read by services/core settings but missing from the inventory"
            )
        elif is_secret and not any(
            e["name"] == n
            and e["consumer"] == "core-service"
            and e["kind"] == "server_secret"
            for e in entries
        ):
            errs.append(
                f"{n}: is a SecretStr setting, so it must be a server_secret in the inventory"
            )
    app_keys = app_config_keys(root) | set(extra_app_keys or ())
    for n in sorted(app_keys):
        if n not in names:
            if n.startswith(("NEXT_PUBLIC_", "PUBLIC_")) and SECRET_LIKE.search(n):
                errs.append(
                    f"{n}: public variable looks secret; it would ship to browsers"
                )
            errs.append(f"{n}: read by an app but missing from the inventory")
        elif names[n]["kind"] == "server_secret" and not (
            # Next.js server-side env (never NEXT_PUBLIC_) of the admin app is not bundled for browsers
            not n.startswith(("NEXT_PUBLIC_", "PUBLIC_"))
            and any(
                e.get("name") == n and e.get("consumer") == "app-admin" for e in entries
            )
        ):
            errs.append(
                f"{n}: server secret referenced by app code (would reach the bundle)"
            )

    # appwrite/keys.md agreement
    for r in _keys_md(root):
        consumer, env = r["consumer"], r["environment"]
        inv_e = [
            e
            for e in entries
            if e["name"] == "APPWRITE_API_KEY" and e["consumer"] == consumer
        ]
        if not inv_e or env not in inv_e[0]["environments"]:
            errs.append(
                f"keys.md row {r['key']} has no APPWRITE_API_KEY entry for {consumer}/{env} in the inventory"
            )
            continue
        days = _days(r["expiry"])
        if days and inv_e[0].get("rotation_interval_days", 0) > days:
            errs.append(
                f"APPWRITE_API_KEY ({consumer}): rotation interval is longer than the key expiry in appwrite/keys.md"
            )

    errs += check_env_example(root)
    errs += check_docs(root, inv)
    return errs


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    root = Path(__file__).resolve().parents[1]
    inv = load(root)
    if "--write" in argv and not shape_errors(inv):
        write_docs(root, inv)
        print("secrets: generated blocks rewritten")
    errs = validate(inv, root)
    for e in errs:
        print(f"secrets: {e}")
    if not errs:
        n = sum(e["kind"] == "server_secret" for e in inv["entries"])
        print(
            f"secrets: inventory ok ({len(inv['entries'])} entries, {n} server secrets)"
        )
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main())
